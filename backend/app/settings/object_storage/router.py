from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from minio.error import S3Error
from sqlalchemy.orm import Session
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from app.auth.models import User
from app.config import settings
from app.deps import get_current_user, get_db, require_admin
from app.services.encryption_service import decrypt, encrypt
from app.settings.object_storage.models import MinioConfig, MinioOperationAudit
from app.settings.object_storage.schemas import (
    BucketCreateRequest,
    MinioConfigResponse,
    MinioConnectionTestRequest,
    MinioConnectionTestResponse,
    MinioTokenResponse,
    ObjectTransferRequest,
    OperationAuditOut,
    PresignRequest,
    TextObjectUploadRequest,
)
from app.settings.object_storage.service import (
    ConfiguredMinioService,
    MinioServiceError,
    audit_operation,
    build_client,
    close_client,
    generate_mcp_token,
    normalize_endpoint,
    reset_configured_client,
    validate_bucket_name,
)


router = APIRouter()


def _config(db: Session) -> MinioConfig | None:
    return db.query(MinioConfig).filter(MinioConfig.id == "default").first()


def _service(db: Session) -> ConfiguredMinioService:
    try:
        return ConfiguredMinioService.from_db(db)
    except MinioServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _minio_call(callback):
    try:
        return callback()
    except MinioServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except S3Error as exc:
        code = getattr(exc, "code", "MinioError") or "MinioError"
        status_code = 409 if code in {"BucketNotEmpty", "BucketAlreadyOwnedByYou", "InvalidBucketName"} else 502
        raise HTTPException(status_code=status_code, detail=f"MinIO 操作失败：{code}") from exc
    except (Urllib3HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail="MinIO 网络连接失败") from exc


def _reset_shared_storage() -> None:
    reset_configured_client()
    try:
        from app.shared.storage import reset_storage_service
        reset_storage_service()
    except ImportError:
        pass


@router.get("/minio-config", response_model=MinioConfigResponse)
def get_minio_config(db: Session = Depends(get_db), _=Depends(require_admin)):
    cfg = _config(db)
    if not cfg:
        return MinioConfigResponse()
    return MinioConfigResponse(
        enabled=cfg.enabled,
        endpoint=cfg.endpoint,
        secure=cfg.secure,
        region=cfg.region,
        default_bucket=cfg.default_bucket,
        has_access_key=bool(cfg.access_key_encrypted),
        has_secret_key=bool(cfg.secret_key_encrypted),
        read_enabled=cfg.read_enabled,
        write_enabled=cfg.write_enabled,
        delete_enabled=cfg.delete_enabled,
        mcp_enabled=cfg.mcp_enabled,
        has_mcp_token=bool(cfg.mcp_token_hash),
        mcp_token_hint=cfg.mcp_token_hint,
        connected=cfg.connected,
        last_test_status=cfg.last_test_status,
        last_test_message=cfg.last_test_message,
        last_tested_at=cfg.last_tested_at,
    )


@router.put("/minio-config")
def reject_unverified_minio_config(
    _body: MinioConnectionTestRequest,
    _=Depends(require_admin),
):
    raise HTTPException(status_code=400, detail="请使用测试连接接口；MinIO 配置仅在连接成功后保存")


@router.post("/minio-config/test", response_model=MinioConnectionTestResponse)
def test_and_save_minio_config(
    body: MinioConnectionTestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        endpoint, secure = normalize_endpoint(body.endpoint, secure=body.secure)
        default_bucket = validate_bucket_name(body.default_bucket)
    except MinioServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    current = _config(db)
    access_key = body.access_key.strip()
    secret_key = body.secret_key
    if not access_key and current and current.access_key_encrypted:
        try:
            access_key = decrypt(current.access_key_encrypted)
        except Exception:
            return MinioConnectionTestResponse(ok=False, message="已保存的 Access Key 无法解密，请重新输入")
    if not secret_key and current and current.secret_key_encrypted:
        try:
            secret_key = decrypt(current.secret_key_encrypted)
        except Exception:
            return MinioConnectionTestResponse(ok=False, message="已保存的 Secret Key 无法解密，请重新输入")
    if not access_key or not secret_key:
        return MinioConnectionTestResponse(ok=False, message="请填写 MinIO Access Key 和 Secret Key", endpoint=endpoint)

    client = None
    try:
        client = build_client(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=body.region.strip() or "us-east-1",
            timeout_seconds=body.timeout_seconds,
        )
        buckets = client.list_buckets()
        ready = client.bucket_exists(default_bucket)
        if body.create_default_bucket and not ready:
            if not body.write_enabled:
                raise MinioServiceError("默认 Bucket 不存在；请开放写权限以自动创建，或先在 MinIO 中创建")
            client.make_bucket(default_bucket, location=body.region.strip() or None)
            ready = True
    except Exception as exc:
        close_client(client)
        message = str(exc).strip() or exc.__class__.__name__
        return MinioConnectionTestResponse(
            ok=False,
            message=f"MinIO 连接失败：{message[:400]}",
            endpoint=endpoint,
        )

    close_client(client)
    cfg = current or MinioConfig(id="default")
    if current is None:
        db.add(cfg)
    cfg.enabled = body.enabled
    cfg.endpoint = endpoint
    cfg.secure = secure
    cfg.region = body.region.strip() or "us-east-1"
    cfg.default_bucket = default_bucket
    cfg.access_key_encrypted = encrypt(access_key)
    cfg.secret_key_encrypted = encrypt(secret_key)
    cfg.read_enabled = body.read_enabled
    cfg.write_enabled = body.write_enabled
    cfg.delete_enabled = body.delete_enabled
    cfg.mcp_enabled = body.mcp_enabled
    cfg.connected = True
    cfg.last_test_status = "success"
    cfg.last_test_message = f"连接成功，可访问 {len(buckets)} 个 Bucket"
    cfg.last_tested_at = datetime.now(timezone.utc)
    plaintext_token: str | None = None
    if body.mcp_enabled and not cfg.mcp_token_hash:
        plaintext_token, cfg.mcp_token_hash, cfg.mcp_token_hint = generate_mcp_token()
    db.commit()
    _reset_shared_storage()
    audit_operation(
        db, actor_type="admin_http", actor_id=current_user.id,
        operation="minio_config_test", success=True,
        details={"endpoint": endpoint, "bucket_count": len(buckets)},
    )
    return MinioConnectionTestResponse(
        ok=True,
        message=cfg.last_test_message,
        endpoint=endpoint,
        bucket_count=len(buckets),
        default_bucket_ready=ready,
        mcp_token=plaintext_token,
    )


@router.post("/minio-config/mcp-token/rotate", response_model=MinioTokenResponse)
def rotate_minio_mcp_token(
    db: Session = Depends(get_db), current_user: User = Depends(require_admin),
):
    cfg = _config(db)
    if not cfg or not cfg.connected or not cfg.mcp_enabled:
        raise HTTPException(status_code=409, detail="请先连接并启用 MinIO MCP")
    token, cfg.mcp_token_hash, cfg.mcp_token_hint = generate_mcp_token()
    db.commit()
    audit_operation(
        db, actor_type="admin_http", actor_id=current_user.id,
        operation="minio_mcp_token_rotate",
    )
    return MinioTokenResponse(token=token, token_hint=cfg.mcp_token_hint)


@router.get("/minio/buckets")
def list_buckets(db: Session = Depends(get_db), _=Depends(require_admin)):
    return {"buckets": _minio_call(lambda: _service(db).list_buckets())}


@router.post("/minio/buckets", status_code=201)
def create_bucket(
    body: BucketCreateRequest, db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = _minio_call(lambda: _service(db).create_bucket(body.bucket, body.region))
    audit_operation(db, actor_type="admin_http", actor_id=current_user.id, operation="create_bucket", bucket=body.bucket)
    return result


@router.delete("/minio/buckets/{bucket}")
def delete_bucket(
    bucket: str, db: Session = Depends(get_db), current_user: User = Depends(require_admin),
):
    result = _minio_call(lambda: _service(db).delete_bucket(bucket))
    audit_operation(db, actor_type="admin_http", actor_id=current_user.id, operation="delete_bucket", bucket=bucket)
    return result


@router.get("/minio/objects")
def list_objects(
    bucket: str, prefix: str = "", search: str = "", limit: int = Query(100, ge=1, le=500),
    cursor: str = "", db: Session = Depends(get_db), _=Depends(require_admin),
):
    return _minio_call(lambda: _service(db).list_objects(
        bucket=bucket, prefix=prefix, search=search, limit=limit, cursor=cursor,
    ))


@router.get("/minio/objects/metadata")
def object_metadata(bucket: str, key: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    return _minio_call(lambda: _service(db).stat_object(bucket, key))


@router.get("/minio/objects/preview")
def preview_object(
    bucket: str, key: str, max_bytes: int = Query(200000, ge=1, le=1000000),
    db: Session = Depends(get_db), _=Depends(require_admin),
):
    return _minio_call(lambda: _service(db).read_object(bucket=bucket, key=key, max_bytes=max_bytes))


@router.get("/minio/objects/download")
def download_object(bucket: str, key: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    service = _service(db)
    metadata = _minio_call(lambda: service.stat_object(bucket, key))
    response = _minio_call(lambda: service.get_stream(bucket=bucket, key=key))

    def iterator():
        try:
            while chunk := response.read(1024 * 1024):
                yield chunk
        finally:
            response.close()
            response.release_conn()

    filename = quote(key.rsplit("/", 1)[-1], safe="")
    return StreamingResponse(
        iterator(),
        media_type=metadata.get("content_type") or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/minio/objects/upload", status_code=201)
def upload_object(
    bucket: str,
    key: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    file.file.seek(0, 2)
    length = file.file.tell()
    file.file.seek(0)
    limit = settings.max_upload_mb * 1024 * 1024
    if length > limit:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb}MB 上传上限")
    result = _minio_call(lambda: _service(db).upload_stream(
        bucket=bucket, key=key, data=file.file, length=length,
        content_type=file.content_type or "application/octet-stream",
    ))
    audit_operation(
        db, actor_type="admin_http", actor_id=current_user.id, operation="upload_object",
        bucket=bucket, object_key=key, details={"size": length},
    )
    return result


@router.post("/minio/objects/text", status_code=201)
def upload_text_object(
    body: TextObjectUploadRequest, db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    data = body.content.encode("utf-8")
    if len(data) > 2_000_000:
        raise HTTPException(status_code=413, detail="文本内容超过 2MB 上传上限")
    result = _minio_call(lambda: _service(db).upload_bytes(
        bucket=body.bucket, key=body.key, data=data, content_type=body.content_type,
    ))
    audit_operation(
        db, actor_type="admin_http", actor_id=current_user.id, operation="upload_text",
        bucket=body.bucket, object_key=body.key, details={"size": len(data)},
    )
    return result


@router.post("/minio/objects/copy")
def copy_object(
    body: ObjectTransferRequest, db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = _minio_call(lambda: _service(db).copy_object(**body.model_dump()))
    audit_operation(
        db, actor_type="admin_http", actor_id=current_user.id, operation="copy_object",
        bucket=body.destination_bucket, object_key=body.destination_key,
    )
    return result


@router.post("/minio/objects/move")
def move_object(
    body: ObjectTransferRequest, db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = _minio_call(lambda: _service(db).move_object(**body.model_dump()))
    audit_operation(
        db, actor_type="admin_http", actor_id=current_user.id, operation="move_object",
        bucket=body.destination_bucket, object_key=body.destination_key,
    )
    return result


@router.delete("/minio/objects")
def delete_object(
    bucket: str, key: str, db: Session = Depends(get_db), current_user: User = Depends(require_admin),
):
    result = _minio_call(lambda: _service(db).delete_object(bucket=bucket, key=key))
    audit_operation(
        db, actor_type="admin_http", actor_id=current_user.id, operation="delete_object",
        bucket=bucket, object_key=key,
    )
    return result


@router.post("/minio/objects/presign")
def presign_object(body: PresignRequest, db: Session = Depends(get_db), _=Depends(require_admin)):
    return _minio_call(lambda: _service(db).presign(**body.model_dump()))


@router.get("/minio/audits", response_model=list[OperationAuditOut])
def list_minio_audits(
    limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), _=Depends(require_admin),
):
    return db.query(MinioOperationAudit).order_by(MinioOperationAudit.created_at.desc()).limit(limit).all()
