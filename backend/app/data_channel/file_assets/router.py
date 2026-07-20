from __future__ import annotations

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
    store_upload,
)
from app.deps import get_db
from app.shared.storage import get_storage_service


upload_router = APIRouter()
asset_router = APIRouter()


def _bearer(value: str | None) -> str:
    if not value or not value.lower().startswith("bearer "):
        raise HTTPException(401, "缺少文件上传令牌")
    return value[7:].strip()


def _accessible_asset(db: Session, asset_id: str) -> PipelineFileAsset | None:
    from datetime import datetime, timezone

    return db.query(PipelineFileAsset).filter(
        PipelineFileAsset.id == asset_id,
        PipelineFileAsset.storage_uri.is_not(None),
        or_(
            PipelineFileAsset.status == "committed",
            and_(
                PipelineFileAsset.status == "ready",
                PipelineFileAsset.expires_at.is_not(None),
                PipelineFileAsset.expires_at > datetime.now(timezone.utc),
            ),
        ),
    ).first()


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
    return {"file_ref": canonical_file_ref(asset)}


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
    try:
        stream = get_storage_service().get_stream(asset.storage_uri)
    except Exception as exc:  # noqa: BLE001
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
        "Cache-Control": "private, no-store",
    }
    return StreamingResponse(body(), media_type=asset.content_type, headers=headers)
