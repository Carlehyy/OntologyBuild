from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, BinaryIO

from jose import JWTError, jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.data_channel.datasets.models import StorageDeletionOutbox
from app.data_channel.file_assets.models import PipelineFileAsset
from app.data_channel.pipelines.models import Pipeline
from app.data_channel.steward.models import N8nPipeline
from app.shared.storage import StorageService, get_storage_service


FILE_REF_TYPE = "file_ref"
UPLOAD_TOKEN_TYPE = "pipeline-file-upload"
FILE_BUCKET = "media"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SAFE_EXTENSION = re.compile(r"^[a-z0-9]{1,16}$")
_SAFE_PATH_ID = re.compile(r"^[A-Za-z0-9._~-]{1,100}$")
_SAFE_MIME = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
logger = logging.getLogger(__name__)


class FileAssetError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_original_name(value: str | None) -> str:
    """Keep a display name, never a path or response-header injection value."""
    raw = unicodedata.normalize("NFKC", str(value or "attachment"))
    raw = raw.replace("\\", "/")
    name = PurePosixPath(raw).name
    name = _CONTROL.sub("", name).strip().strip(".")
    if not name:
        name = "attachment"
    # Bound UTF-8 size as well as the database character length.
    encoded = name.encode("utf-8")
    if len(encoded) > 240:
        encoded = encoded[:240]
        while True:
            try:
                name = encoded.decode("utf-8")
                break
            except UnicodeDecodeError:
                encoded = encoded[:-1]
    return name[:240] or "attachment"


def _extension(name: str) -> str:
    suffix = PurePosixPath(name).suffix.lower().lstrip(".")
    return suffix if _SAFE_EXTENSION.fullmatch(suffix) else ""


def _allowed_extensions() -> set[str] | None:
    values = {
        item.strip().lower().lstrip(".")
        for item in settings.pipeline_file_allowed_extensions.split(",")
        if item.strip()
    }
    return None if "*" in values else values


def validate_filename(name: str) -> None:
    allowed = _allowed_extensions()
    extension = _extension(name)
    if allowed is not None and extension not in allowed:
        shown = ", ".join(sorted(allowed))
        raise FileAssetError(f"不允许上传 .{extension or '无扩展名'} 文件；允许类型：{shown}")


def create_upload_token(
    *, pipeline_id: str, workflow_id: str, invocation_id: str,
    purpose: str, owner_id: str | None,
) -> str:
    if purpose not in {"preview", "run"}:
        raise FileAssetError("非法的文件上传用途")
    now = _now()
    exp = now + timedelta(minutes=max(1, settings.pipeline_file_upload_token_minutes))
    return jwt.encode(
        {
            "typ": UPLOAD_TOKEN_TYPE,
            "pipeline_id": pipeline_id,
            "workflow_id": workflow_id,
            "invocation_id": invocation_id,
            "purpose": purpose,
            "owner_id": owner_id,
            "iat": now,
            "exp": exp,
            "jti": str(uuid.uuid4()),
        },
        settings.secret_key,
        algorithm="HS256",
    )


def decode_upload_token(token: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except JWTError as exc:
        raise FileAssetError("文件上传令牌无效或已过期") from exc
    required = {"pipeline_id", "workflow_id", "invocation_id", "purpose"}
    if claims.get("typ") != UPLOAD_TOKEN_TYPE or not required.issubset(claims):
        raise FileAssetError("令牌不是有效的流水线文件上传令牌")
    if claims.get("purpose") not in {"preview", "run"}:
        raise FileAssetError("文件上传令牌用途无效")
    for key in ("pipeline_id", "workflow_id", "invocation_id"):
        value = str(claims.get(key) or "").strip()
        if not value or len(value) > 100:
            raise FileAssetError(f"文件上传令牌字段 {key} 无效")
        if key in {"pipeline_id", "invocation_id"} and not _SAFE_PATH_ID.fullmatch(value):
            raise FileAssetError(f"文件上传令牌字段 {key} 包含不安全字符")
    return claims


def gateway_context(
    *, pipeline_id: str, workflow_id: str, invocation_id: str,
    purpose: str, owner_id: str | None,
) -> dict[str, Any]:
    base_url = settings.pipeline_file_gateway_base_url.rstrip("/")
    return {
        "contract_version": 1,
        "upload_url": f"{base_url}/upload",
        "token": create_upload_token(
            pipeline_id=pipeline_id,
            workflow_id=workflow_id,
            invocation_id=invocation_id,
            purpose=purpose,
            owner_id=owner_id,
        ),
        "invocation_id": invocation_id,
        "purpose": purpose,
        "max_bytes": max(1, settings.pipeline_file_max_upload_mb) * 1024 * 1024,
    }


def verify_upload_scope(db: Session, claims: dict[str, Any]) -> None:
    pipeline_id = str(claims["pipeline_id"])
    workflow_id = str(claims["workflow_id"])
    pipeline = db.query(Pipeline.id).filter(Pipeline.id == pipeline_id).first()
    binding = db.query(N8nPipeline.id).filter(
        N8nPipeline.pipeline_id == pipeline_id,
        N8nPipeline.n8n_workflow_id == workflow_id,
        N8nPipeline.status != "archived",
    ).first()
    if pipeline is None or binding is None:
        raise FileAssetError("上传令牌对应的受管流水线已不存在或绑定已失效")


def inspect_upload(stream: BinaryIO) -> tuple[int, str]:
    max_bytes = max(1, settings.pipeline_file_max_upload_mb) * 1024 * 1024
    digest = hashlib.sha256()
    size = 0
    stream.seek(0)
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            stream.seek(0)
            raise FileAssetError(
                f"附件超过 {settings.pipeline_file_max_upload_mb} MB 上限")
        digest.update(chunk)
    stream.seek(0)
    if size <= 0:
        raise FileAssetError("不能上传空文件")
    return size, digest.hexdigest()


def canonical_file_ref(asset: PipelineFileAsset) -> dict[str, Any]:
    return {
        "$type": FILE_REF_TYPE,
        "id": asset.id,
        "name": asset.original_name,
        "size": int(asset.size),
        "content_type": asset.content_type,
        "sha256": asset.sha256,
        "download_url": f"/api/v2/file-assets/{asset.id}/download",
    }


def _object_key(asset_id: str, pipeline_id: str, invocation_id: str, name: str,
                purpose: str) -> str:
    stamp = _now().strftime("%Y/%m/%d")
    ext = _extension(name)
    suffix = f".{ext}" if ext else ""
    # Every path component except the fixed prefix is a platform-generated or
    # token-bound identifier; the untrusted original filename is never a key.
    return (
        f"pipeline-files/{pipeline_id}/{purpose}/{stamp}/"
        f"{invocation_id}/{asset_id}{suffix}"
    )


def _expiry_for(purpose: str) -> datetime:
    hours = (
        settings.pipeline_file_preview_retention_hours
        if purpose == "preview"
        else settings.pipeline_file_pending_retention_hours
    )
    return _now() + timedelta(hours=max(1, hours))


def store_upload(
    db: Session, *, claims: dict[str, Any], stream: BinaryIO,
    filename: str | None, content_type: str | None, idempotency_key: str,
    storage: StorageService | None = None,
) -> PipelineFileAsset:
    verify_upload_scope(db, claims)
    idem = str(idempotency_key or "").strip()
    if not idem or len(idem) > 200:
        raise FileAssetError("idempotency_key 必填且不能超过 200 个字符")
    name = sanitize_original_name(filename)
    validate_filename(name)
    size, sha256 = inspect_upload(stream)
    pipeline_id = str(claims["pipeline_id"])
    invocation_id = str(claims["invocation_id"])
    workflow_id = str(claims["workflow_id"])
    purpose = str(claims["purpose"])

    existing = db.query(PipelineFileAsset).filter(
        PipelineFileAsset.pipeline_id == pipeline_id,
        PipelineFileAsset.invocation_id == invocation_id,
        PipelineFileAsset.idempotency_key == idem,
    ).first()
    if existing is not None:
        if existing.sha256 != sha256 or int(existing.size) != size:
            raise FileAssetError("同一 idempotency_key 已用于不同文件")
        if existing.status not in {"ready", "committed"}:
            raise FileAssetError("该幂等上传记录已失效，请使用新的 idempotency_key")
        return existing

    asset_id = str(uuid.uuid4())
    key = _object_key(asset_id, pipeline_id, invocation_id, name, purpose)
    mime = (content_type or "").split(";", 1)[0].strip()[:200]
    if not _SAFE_MIME.fullmatch(mime):
        mime = "application/octet-stream"
    storage = storage or get_storage_service()
    original_name_b64 = base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii")
    uri = storage.put_object(
        FILE_BUCKET, key, stream, content_type=mime, length=size,
        metadata={
            "file-id": asset_id,
            "pipeline-id": pipeline_id,
            "invocation-id": invocation_id,
            "sha256": sha256,
            "original-name-b64": original_name_b64,
        },
    )
    asset = PipelineFileAsset(
        id=asset_id,
        pipeline_id=pipeline_id,
        workflow_id=workflow_id,
        invocation_id=invocation_id,
        owner_id=claims.get("owner_id") or None,
        purpose=purpose,
        status="ready",
        idempotency_key=idem,
        original_name=name,
        object_key=key,
        storage_uri=uri,
        size=size,
        content_type=mime,
        sha256=sha256,
        expires_at=_expiry_for(purpose),
    )
    db.add(asset)
    try:
        db.commit()
        db.refresh(asset)
        return asset
    except IntegrityError:
        db.rollback()
        try:
            storage.delete_object(uri)
        finally:
            winner = db.query(PipelineFileAsset).filter(
                PipelineFileAsset.pipeline_id == pipeline_id,
                PipelineFileAsset.invocation_id == invocation_id,
                PipelineFileAsset.idempotency_key == idem,
            ).first()
        if winner and winner.sha256 == sha256 and int(winner.size) == size:
            return winner
        raise FileAssetError("并发幂等上传冲突")
    except Exception:
        db.rollback()
        try:
            storage.delete_object(uri)
        finally:
            pass
        raise


def _collect_ref_ids(value: Any, result: set[str]) -> None:
    if isinstance(value, dict):
        if value.get("$type") == FILE_REF_TYPE:
            asset_id = str(value.get("id") or "").strip()
            if not asset_id:
                raise FileAssetError("file_ref 缺少 id")
            result.add(asset_id)
            return
        for child in value.values():
            _collect_ref_ids(child, result)
    elif isinstance(value, list):
        for child in value:
            _collect_ref_ids(child, result)


def validate_and_canonicalize_refs(
    db: Session, rows: list[dict], *, pipeline_id: str, invocation_id: str,
) -> tuple[list[dict], list[str]]:
    ids: set[str] = set()
    _collect_ref_ids(rows, ids)
    if not ids:
        return rows, []
    assets = db.query(PipelineFileAsset).filter(PipelineFileAsset.id.in_(ids)).all()
    by_id = {item.id: item for item in assets}
    missing = sorted(ids - set(by_id))
    if missing:
        raise FileAssetError(f"输出引用了不存在的 file_ref：{missing[0]}")
    for asset in assets:
        if asset.pipeline_id != pipeline_id or asset.invocation_id != invocation_id:
            raise FileAssetError(f"file_ref {asset.id} 不属于本流水线的本次执行")
        if asset.status != "ready" or not asset.storage_uri:
            raise FileAssetError(f"file_ref {asset.id} 当前不可用（status={asset.status}）")
        expires_at = asset.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= _now():
                raise FileAssetError(f"file_ref {asset.id} 已过期，请重新执行流水线")

    def replace(value: Any) -> Any:
        if isinstance(value, dict):
            if value.get("$type") == FILE_REF_TYPE:
                return canonical_file_ref(by_id[str(value["id"])])
            return {key: replace(child) for key, child in value.items()}
        if isinstance(value, list):
            return [replace(child) for child in value]
        return value

    return replace(rows), sorted(ids)


def _delete_assets(db: Session, assets: list[PipelineFileAsset]) -> int:
    now = _now()
    count = 0
    for asset in assets:
        if asset.status in {"deleted", "committed"}:
            continue
        if asset.storage_uri:
            db.add(StorageDeletionOutbox(storage_uri=asset.storage_uri))
        asset.storage_uri = None
        asset.status = "deleted"
        asset.deleted_at = now
        asset.expires_at = None
        count += 1
    return count


def reconcile_invocation(
    db: Session, *, pipeline_id: str, invocation_id: str,
    referenced_ids: list[str], commit: bool = True,
) -> int:
    referenced = set(referenced_ids)
    assets = db.query(PipelineFileAsset).filter(
        PipelineFileAsset.pipeline_id == pipeline_id,
        PipelineFileAsset.invocation_id == invocation_id,
        PipelineFileAsset.status == "ready",
    ).all()
    changed = _delete_assets(db, [asset for asset in assets if asset.id not in referenced])
    if commit:
        db.commit()
        from app.data_channel.datasets.service import drain_storage_deletion_outbox
        drain_storage_deletion_outbox(db)
    else:
        db.flush()
    return changed


def commit_invocation(
    db: Session, *, pipeline_id: str, invocation_id: str,
    referenced_ids: list[str], dataset_version_id: str | None,
) -> None:
    referenced = set(referenced_ids)
    assets = db.query(PipelineFileAsset).filter(
        PipelineFileAsset.pipeline_id == pipeline_id,
        PipelineFileAsset.invocation_id == invocation_id,
        PipelineFileAsset.status == "ready",
    ).all()
    found = {asset.id for asset in assets}
    if referenced - found:
        raise FileAssetError("正式入湖前 file_ref 已失效，请重新运行流水线")
    now = _now()
    for asset in assets:
        if asset.id in referenced:
            asset.status = "committed"
            asset.dataset_version_id = dataset_version_id
            asset.expires_at = None
            asset.committed_at = now
    _delete_assets(db, [asset for asset in assets if asset.id not in referenced])
    db.flush()


def abandon_invocation(db: Session, *, pipeline_id: str, invocation_id: str) -> int:
    assets = db.query(PipelineFileAsset).filter(
        PipelineFileAsset.pipeline_id == pipeline_id,
        PipelineFileAsset.invocation_id == invocation_id,
        PipelineFileAsset.status == "ready",
    ).all()
    changed = _delete_assets(db, assets)
    db.commit()
    if changed:
        from app.data_channel.datasets.service import drain_storage_deletion_outbox
        drain_storage_deletion_outbox(db)
    return changed


def cleanup_expired_assets(
    db: Session, *, limit: int = 200, storage: StorageService | None = None,
) -> int:
    assets = db.query(PipelineFileAsset).filter(
        PipelineFileAsset.status == "ready",
        PipelineFileAsset.expires_at.is_not(None),
        PipelineFileAsset.expires_at <= _now(),
    ).order_by(PipelineFileAsset.expires_at).limit(max(1, min(limit, 1000))).all()
    changed = _delete_assets(db, assets)
    if changed:
        db.commit()
        from app.data_channel.datasets.service import drain_storage_deletion_outbox
        drain_storage_deletion_outbox(db, storage=storage)
    return changed


def _cleanup_cycle() -> None:
    """Run one isolated cleanup cycle with its own SQLAlchemy session."""
    from app.database import SessionLocal
    from app.data_channel.datasets.service import drain_storage_deletion_outbox

    db = SessionLocal()
    try:
        cleanup_expired_assets(db)
        # Retry prior storage failures even when this cycle found no newly
        # expired assets. Object deletion is idempotent and outbox-backed.
        drain_storage_deletion_outbox(db)
    finally:
        db.close()


async def file_asset_cleanup_loop() -> None:
    """Periodically expire preview/pending files without blocking FastAPI."""
    interval = max(30, int(settings.pipeline_file_cleanup_interval_seconds))
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(_cleanup_cycle)
        except asyncio.CancelledError:
            raise
        except Exception:  # best effort; the next cycle retries durable outbox work
            logger.exception("流水线附件周期清理失败")
