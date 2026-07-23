from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.data_channel.file_assets.models import PipelineFileAsset
from app.data_channel.file_assets.service import (
    FileAssetError,
    canonical_file_ref,
    cleanup_expired_assets,
    decode_upload_token,
    issue_share_token,
    public_share_url,
    recover_share_token,
    revoke_share_token,
    store_upload,
)
from app.deps import get_current_user, get_db
from app.shared.storage import get_storage_service


upload_router = APIRouter()
asset_router = APIRouter(dependencies=[Depends(get_current_user)])
public_router = APIRouter()


def _bearer(value: str | None) -> str:
    if not value or not value.lower().startswith("bearer "):
        raise HTTPException(401, "缺少文件上传令牌")
    return value[7:].strip()


def _accessible_asset(db: Session, asset_id: str) -> PipelineFileAsset | None:
    return db.query(PipelineFileAsset).filter(
        PipelineFileAsset.id == asset_id,
        PipelineFileAsset.storage_uri.is_not(None),
        or_(
            PipelineFileAsset.status == "committed",
            and_(
                PipelineFileAsset.status == "ready",
                or_(
                    PipelineFileAsset.expires_at.is_(None),
                    PipelineFileAsset.expires_at > datetime.now(timezone.utc),
                ),
            ),
        ),
    ).first()


def _public_asset(db: Session, token: str) -> PipelineFileAsset | None:
    if not token or len(token) > 200:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return db.query(PipelineFileAsset).filter(
        PipelineFileAsset.share_token_hash == token_hash,
        PipelineFileAsset.share_revoked_at.is_(None),
        PipelineFileAsset.storage_uri.is_not(None),
        or_(
            PipelineFileAsset.status == "committed",
            and_(
                PipelineFileAsset.status == "ready",
                or_(
                    PipelineFileAsset.expires_at.is_(None),
                    PipelineFileAsset.expires_at > datetime.now(timezone.utc),
                ),
            ),
        ),
    ).first()


def _download_response(
    asset: PipelineFileAsset, *, anonymous: bool = False,
) -> StreamingResponse:
    try:
        stream = get_storage_service().get_stream(asset.storage_uri)
    except Exception as exc:  # noqa: BLE001
        if anonymous:
            # Public callers receive one indistinguishable error for unknown,
            # revoked, deleted and currently unreadable attachments.
            raise HTTPException(404, "分享附件不存在或已失效") from exc
        raise HTTPException(502, "附件存储暂时不可用") from exc

    def body():
        try:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            stream.close()
            release = getattr(stream, "release_conn", None)
            if callable(release):
                release()

    fallback = quote(asset.original_name.encode("utf-8"))
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{fallback}",
        "Content-Length": str(asset.size),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store" if anonymous else "private, no-store",
    }
    return StreamingResponse(body(), media_type=asset.content_type, headers=headers)


def _share_response(asset: PipelineFileAsset) -> dict:
    token = recover_share_token(asset)
    enabled = bool(
        asset.share_token_hash and asset.share_revoked_at is None
    )
    permanent = asset.status == "committed" or asset.expires_at is None
    return {
        "asset_id": asset.id,
        "enabled": enabled,
        "recoverable": token is not None,
        "token": token,
        "share_url": public_share_url(token) if token else None,
        "permanent": permanent,
        "expires_at": (
            asset.expires_at.isoformat()
            if not permanent and asset.expires_at is not None else None
        ),
        "created_at": (
            asset.share_created_at.isoformat()
            if asset.share_created_at is not None else None
        ),
        "revoked_at": (
            asset.share_revoked_at.isoformat()
            if asset.share_revoked_at is not None else None
        ),
    }


def _require_share_manager(asset: PipelineFileAsset, user) -> None:
    """Limit share mutation to the attachment owner or an administrator."""
    if str(getattr(user, "role", "") or "") == "admin":
        return
    user_id = str(getattr(user, "id", "") or "")
    if asset.owner_id is not None and user_id == str(asset.owner_id):
        return
    # Legacy assets without an owner are deliberately administrator-only.
    raise HTTPException(403, "仅附件所有者或管理员可以管理匿名分享")


@upload_router.post("/upload", status_code=201)
def upload_pipeline_file(
    file: UploadFile = File(...),
    idempotency_key: str = Form(...),
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    try:
        claims = decode_upload_token(_bearer(authorization))
        cleanup_expired_assets(db, limit=50)
        asset = store_upload(
            db,
            claims=claims,
            stream=file.file,
            filename=file.filename,
            content_type=file.content_type,
            idempotency_key=idempotency_key,
        )
    except FileAssetError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Upload responses are persisted in n8n execution history.  Anonymous
    # bearer links are issued only after the terminal FileRef set is validated.
    return {"file_ref": canonical_file_ref(asset, include_share=False)}


@asset_router.get("/{asset_id}")
def get_file_asset(asset_id: str, db: Session = Depends(get_db)):
    asset = _accessible_asset(db, asset_id)
    if asset is None:
        raise HTTPException(404, "附件不存在或已过期")
    return canonical_file_ref(asset)


@asset_router.get("/{asset_id}/download")
def download_file_asset(asset_id: str, db: Session = Depends(get_db)):
    asset = _accessible_asset(db, asset_id)
    if asset is None:
        raise HTTPException(404, "附件不存在或已过期")
    return _download_response(asset)


@asset_router.get("/{asset_id}/share")
def get_file_asset_share(asset_id: str, db: Session = Depends(get_db)):
    asset = _accessible_asset(db, asset_id)
    if asset is None:
        raise HTTPException(404, "附件不存在或已过期")
    return _share_response(asset)


@asset_router.post("/{asset_id}/share")
def create_or_regenerate_file_asset_share(
    asset_id: str,
    regenerate: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    asset = _accessible_asset(db, asset_id)
    if asset is None:
        raise HTTPException(404, "附件不存在或已过期")
    _require_share_manager(asset, current_user)
    _token, created = issue_share_token(asset, regenerate=regenerate)
    pinned = asset.expires_at is not None
    if pinned:
        asset.expires_at = None
    if created or pinned:
        db.commit()
        db.refresh(asset)
    return _share_response(asset)


@asset_router.post("/{asset_id}/share/regenerate")
def regenerate_file_asset_share(
    asset_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    asset = _accessible_asset(db, asset_id)
    if asset is None:
        raise HTTPException(404, "附件不存在或已过期")
    _require_share_manager(asset, current_user)
    issue_share_token(asset, regenerate=True)
    asset.expires_at = None
    db.commit()
    db.refresh(asset)
    return _share_response(asset)


@asset_router.delete("/{asset_id}/share")
def revoke_file_asset_share(
    asset_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    asset = _accessible_asset(db, asset_id)
    if asset is None:
        raise HTTPException(404, "附件不存在或已过期")
    _require_share_manager(asset, current_user)
    changed = revoke_share_token(asset)
    if changed:
        db.commit()
        db.refresh(asset)
    response = _share_response(asset)
    response["status"] = "revoked"
    return response


@public_router.get("/{token}/download")
def download_shared_file_asset(token: str, db: Session = Depends(get_db)):
    asset = _public_asset(db, token)
    if asset is None:
        raise HTTPException(404, "分享附件不存在或已失效")
    return _download_response(asset, anonymous=True)
