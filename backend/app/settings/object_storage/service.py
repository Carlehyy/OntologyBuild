from __future__ import annotations

import base64
import hashlib
import hmac
import io
import ipaddress
import json
import re
import secrets
import threading
from datetime import timedelta
from typing import Any, BinaryIO
from urllib.parse import urlsplit

import urllib3
from minio import Minio
from minio.commonconfig import CopySource
from sqlalchemy.orm import Session

from app.services.encryption_service import decrypt
from app.settings.object_storage.models import MinioConfig, MinioOperationAudit


MCP_PATH = "/mcp/minio"
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv",
    ".xml", ".yaml", ".yml", ".html", ".htm", ".css", ".js", ".ts",
    ".tsx", ".jsx", ".py", ".sql", ".log", ".ini", ".toml", ".conf",
}


class MinioServiceError(ValueError):
    pass


def normalize_endpoint(raw: str, *, secure: bool) -> tuple[str, bool]:
    value = (raw or "").strip()
    if not value:
        raise MinioServiceError("请填写 MinIO S3 API 端点")
    explicit_scheme = "://" in value
    parsed = urlsplit(value if explicit_scheme else f"//{value}")
    if explicit_scheme and parsed.scheme.lower() not in {"http", "https"}:
        raise MinioServiceError("MinIO 端点仅支持 HTTP 或 HTTPS")
    if not parsed.hostname:
        raise MinioServiceError("MinIO 端点格式无效")
    if parsed.username or parsed.password:
        raise MinioServiceError("MinIO 端点不能内嵌账号或密码")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        if parsed.path.rstrip("/").endswith("/browser"):
            raise MinioServiceError("请输入 MinIO S3 API 端点（通常为 9000 端口），不要填写 /browser 控制台地址")
        raise MinioServiceError("MinIO 端点不能包含路径、查询参数或片段")
    try:
        port = parsed.port
    except ValueError as exc:
        raise MinioServiceError("MinIO 端点端口无效") from exc
    hostname = parsed.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    endpoint = f"{hostname}:{port}" if port else hostname
    resolved_secure = parsed.scheme.lower() == "https" if explicit_scheme else bool(secure)
    return endpoint, resolved_secure


def validate_bucket_name(bucket: str) -> str:
    value = (bucket or "").strip()
    if not _BUCKET_RE.fullmatch(value) or ".." in value or ".-" in value or "-." in value:
        raise MinioServiceError(
            "Bucket 名称需为 3-63 位小写字母、数字、点或连字符，且首尾必须是字母或数字"
        )
    try:
        parsed_ip = ipaddress.ip_address(value)
    except ValueError:
        parsed_ip = None
    if parsed_ip and parsed_ip.version == 4:
        raise MinioServiceError("Bucket 名称不能是 IPv4 地址")
    return value


def validate_object_key(key: str) -> str:
    value = (key or "").strip()
    if not value or value.startswith("/") or "\x00" in value or len(value.encode("utf-8")) > 1024:
        raise MinioServiceError("对象 Key 无效；不能以 / 开头，且 UTF-8 长度不能超过 1024 字节")
    return value


def build_client(*, endpoint: str, access_key: str, secret_key: str, secure: bool,
                 region: str = "us-east-1", timeout_seconds: int = 10) -> Minio:
    if not access_key or not secret_key:
        raise MinioServiceError("请填写 MinIO Access Key 和 Secret Key")
    timeout = urllib3.Timeout(connect=timeout_seconds, read=timeout_seconds)
    retries = urllib3.Retry(total=0, connect=0, read=0, redirect=0)
    http_client = urllib3.PoolManager(timeout=timeout, retries=retries)
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        region=region or None,
        http_client=http_client,
    )


_client_lock = threading.Lock()
_cached_client: Minio | None = None
_cached_signature: tuple[Any, ...] | None = None


def close_client(client: Minio | None) -> None:
    if client is None:
        return
    http = getattr(client, "_http", None)
    clear = getattr(http, "clear", None)
    if callable(clear):
        clear()


def reset_configured_client() -> None:
    global _cached_client, _cached_signature
    with _client_lock:
        close_client(_cached_client)
        _cached_client = None
        _cached_signature = None


def configured_client(config: MinioConfig) -> Minio:
    global _cached_client, _cached_signature
    signature = (
        config.endpoint,
        config.secure,
        config.region,
        config.access_key_encrypted,
        config.secret_key_encrypted,
    )
    with _client_lock:
        if _cached_client is not None and signature == _cached_signature:
            return _cached_client
        access_key, secret_key = credentials_from_config(config)
        close_client(_cached_client)
        _cached_client = build_client(
            endpoint=config.endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=config.secure,
            region=config.region,
        )
        _cached_signature = signature
        return _cached_client


def generate_mcp_token() -> tuple[str, str, str]:
    token = secrets.token_urlsafe(36)
    return token, hashlib.sha256(token.encode()).hexdigest(), token[-6:]


def token_matches(config: MinioConfig, token: str) -> bool:
    if not config.mcp_token_hash or not token:
        return False
    candidate = hashlib.sha256(token.encode()).hexdigest()
    return hmac.compare_digest(config.mcp_token_hash, candidate)


def credentials_from_config(config: MinioConfig) -> tuple[str, str]:
    try:
        access_key = decrypt(config.access_key_encrypted)
        secret_key = decrypt(config.secret_key_encrypted)
    except Exception as exc:
        raise MinioServiceError("已保存的 MinIO 凭据无法解密，请管理员重新配置") from exc
    if not access_key or not secret_key:
        raise MinioServiceError("MinIO 凭据尚未配置")
    return access_key, secret_key


def audit_operation(db: Session, *, actor_type: str, actor_id: str | None,
                    operation: str, bucket: str | None = None,
                    object_key: str | None = None, success: bool = True,
                    details: dict[str, Any] | None = None) -> None:
    safe_details = dict(details or {})
    for key in list(safe_details):
        if any(marker in key.lower() for marker in ("secret", "password", "token", "access_key")):
            safe_details.pop(key, None)
    db.add(MinioOperationAudit(
        actor_type=actor_type,
        actor_id=actor_id,
        operation=operation,
        bucket=bucket,
        object_key=object_key,
        success=success,
        details=safe_details,
    ))
    db.commit()


class ConfiguredMinioService:
    def __init__(self, config: MinioConfig, client: Minio):
        self.config = config
        self.client = client

    @classmethod
    def from_db(cls, db: Session, *, require_enabled: bool = True) -> "ConfiguredMinioService":
        config = db.query(MinioConfig).filter(MinioConfig.id == "default").first()
        if not config or not config.connected:
            raise MinioServiceError("MinIO 尚未完成连接配置")
        if require_enabled and not config.enabled:
            raise MinioServiceError("MinIO 已停用")
        return cls(config, configured_client(config))

    def _require(self, permission: str) -> None:
        field = {
            "read": self.config.read_enabled,
            "write": self.config.write_enabled,
            "delete": self.config.delete_enabled,
        }[permission]
        if not field:
            raise MinioServiceError(f"管理员未开放 MinIO {permission} 权限")

    def status(self, *, verify: bool = True) -> dict[str, Any]:
        buckets = self.client.list_buckets() if verify else []
        return {
            "connected": True,
            "enabled": self.config.enabled,
            "endpoint": self.config.endpoint,
            "secure": self.config.secure,
            "default_bucket": self.config.default_bucket,
            "permissions": {
                "read": self.config.read_enabled,
                "write": self.config.write_enabled,
                "delete": self.config.delete_enabled,
            },
            "bucket_count": len(buckets),
        }

    def list_buckets(self) -> list[dict[str, Any]]:
        self._require("read")
        return [
            {"name": item.name, "creation_date": item.creation_date}
            for item in self.client.list_buckets()
        ]

    def create_bucket(self, bucket: str, region: str | None = None) -> dict[str, Any]:
        self._require("write")
        name = validate_bucket_name(bucket)
        if not self.client.bucket_exists(name):
            self.client.make_bucket(name, location=region or self.config.region or None)
        return {"bucket": name, "created": True}

    def delete_bucket(self, bucket: str) -> dict[str, Any]:
        self._require("delete")
        name = validate_bucket_name(bucket)
        self.client.remove_bucket(name)
        return {"bucket": name, "deleted": True}

    @staticmethod
    def _object_item(bucket: str, item: Any) -> dict[str, Any]:
        return {
            "bucket": bucket,
            "key": item.object_name,
            "size": item.size,
            "etag": (item.etag or "").strip('"'),
            "last_modified": item.last_modified,
            "content_type": getattr(item, "content_type", None),
            "version_id": getattr(item, "version_id", None),
            "is_dir": bool(getattr(item, "is_dir", False)),
        }

    def list_objects(self, *, bucket: str, prefix: str = "", search: str = "",
                     limit: int = 100, cursor: str = "", max_scan: int = 5000) -> dict[str, Any]:
        self._require("read")
        name = validate_bucket_name(bucket)
        limit = max(1, min(int(limit), 500))
        keyword = (search or "").strip().lower()
        result: list[dict[str, Any]] = []
        scanned = 0
        last_key = ""
        truncated = False
        objects = self.client.list_objects(
            name,
            prefix=prefix or None,
            recursive=True,
            start_after=cursor or None,
        )
        for item in objects:
            scanned += 1
            last_key = item.object_name
            if keyword and keyword not in item.object_name.lower():
                if scanned >= max_scan:
                    truncated = True
                    break
                continue
            result.append(self._object_item(name, item))
            if len(result) >= limit:
                truncated = True
                break
        return {
            "bucket": name,
            "prefix": prefix,
            "search": search,
            "objects": result,
            "count": len(result),
            "scanned": scanned,
            "truncated": truncated,
            "next_cursor": last_key if truncated else None,
        }

    def stat_object(self, bucket: str, key: str) -> dict[str, Any]:
        self._require("read")
        name, object_key = validate_bucket_name(bucket), validate_object_key(key)
        item = self.client.stat_object(name, object_key)
        payload = self._object_item(name, item)
        payload["metadata"] = dict(item.metadata or {})
        return payload

    def upload_stream(self, *, bucket: str, key: str, data: BinaryIO, length: int,
                      content_type: str = "application/octet-stream") -> dict[str, Any]:
        self._require("write")
        name, object_key = validate_bucket_name(bucket), validate_object_key(key)
        if not self.client.bucket_exists(name):
            self.client.make_bucket(name, location=self.config.region or None)
        result = self.client.put_object(
            name, object_key, data, length=length, content_type=content_type or "application/octet-stream",
        )
        return {
            "bucket": name,
            "key": object_key,
            "size": length,
            "etag": (result.etag or "").strip('"'),
            "version_id": result.version_id,
            "uri": f"s3://{name}/{object_key}",
        }

    def upload_bytes(self, *, bucket: str, key: str, data: bytes,
                     content_type: str = "application/octet-stream") -> dict[str, Any]:
        return self.upload_stream(
            bucket=bucket, key=key, data=io.BytesIO(data), length=len(data), content_type=content_type,
        )

    def read_object(self, *, bucket: str, key: str, max_bytes: int = 1_000_000) -> dict[str, Any]:
        self._require("read")
        name, object_key = validate_bucket_name(bucket), validate_object_key(key)
        stat = self.client.stat_object(name, object_key)
        content_type = (stat.content_type or "application/octet-stream").split(";", 1)[0].lower()
        suffix = "." + object_key.rsplit(".", 1)[-1].lower() if "." in object_key else ""
        is_text = content_type.startswith("text/") or content_type in {
            "application/json", "application/x-ndjson", "application/xml",
            "application/yaml", "application/x-yaml", "application/javascript",
        } or suffix in _TEXT_EXTENSIONS
        if not is_text:
            raise MinioServiceError("该对象不是可安全预览的文本文件；请使用元数据或预签名下载链接")
        response = self.client.get_object(name, object_key)
        try:
            data = response.read(max_bytes + 1)
        finally:
            response.close()
            response.release_conn()
        truncated = len(data) > max_bytes or (stat.size or 0) > max_bytes
        data = data[:max_bytes]
        return {
            "bucket": name,
            "key": object_key,
            "content_type": stat.content_type,
            "size": stat.size,
            "content": data.decode("utf-8", errors="replace"),
            "truncated": truncated,
        }

    def get_stream(self, *, bucket: str, key: str):
        self._require("read")
        return self.client.get_object(validate_bucket_name(bucket), validate_object_key(key))

    def delete_object(self, *, bucket: str, key: str) -> dict[str, Any]:
        self._require("delete")
        name, object_key = validate_bucket_name(bucket), validate_object_key(key)
        self.client.remove_object(name, object_key)
        return {"bucket": name, "key": object_key, "deleted": True}

    def copy_object(self, *, source_bucket: str, source_key: str,
                    destination_bucket: str, destination_key: str) -> dict[str, Any]:
        self._require("read")
        self._require("write")
        src_bucket = validate_bucket_name(source_bucket)
        src_key = validate_object_key(source_key)
        dst_bucket = validate_bucket_name(destination_bucket)
        dst_key = validate_object_key(destination_key)
        if not self.client.bucket_exists(dst_bucket):
            self.client.make_bucket(dst_bucket, location=self.config.region or None)
        result = self.client.copy_object(dst_bucket, dst_key, CopySource(src_bucket, src_key))
        return {
            "source": f"s3://{src_bucket}/{src_key}",
            "destination": f"s3://{dst_bucket}/{dst_key}",
            "etag": (result.etag or "").strip('"'),
            "version_id": result.version_id,
        }

    def move_object(self, **kwargs: str) -> dict[str, Any]:
        self._require("delete")
        result = self.copy_object(**kwargs)
        self.client.remove_object(
            validate_bucket_name(kwargs["source_bucket"]),
            validate_object_key(kwargs["source_key"]),
        )
        result["moved"] = True
        return result

    def presign(self, *, bucket: str, key: str, method: str = "GET",
                expires_seconds: int = 3600) -> dict[str, Any]:
        method = method.upper()
        self._require("read" if method == "GET" else "write")
        name, object_key = validate_bucket_name(bucket), validate_object_key(key)
        expires = timedelta(seconds=max(60, min(int(expires_seconds), 604800)))
        if method == "GET":
            url = self.client.presigned_get_object(name, object_key, expires=expires)
        elif method == "PUT":
            url = self.client.presigned_put_object(name, object_key, expires=expires)
        else:
            raise MinioServiceError("预签名方法仅支持 GET 或 PUT")
        return {
            "bucket": name,
            "key": object_key,
            "method": method,
            "expires_seconds": int(expires.total_seconds()),
            "url": url,
        }


def minio_tool_manifest() -> list[dict[str, Any]]:
    def tool(name: str, description: str, properties: dict[str, Any] | None = None,
             required: list[str] | None = None) -> dict[str, Any]:
        return {
            "name": name,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
                "additionalProperties": False,
            },
        }

    bucket = {"type": "string", "description": "MinIO bucket 名称"}
    key = {"type": "string", "description": "对象 Key（含目录前缀）"}
    return [
        tool("minio_status", "检查平台 MinIO 连接、权限和 bucket 数量。"),
        tool("minio_list_buckets", "列出 MinIO 中可访问的全部 buckets。"),
        tool("minio_create_bucket", "创建 bucket；需要管理员开放写权限。", {"bucket": bucket}, ["bucket"]),
        tool("minio_delete_bucket", "删除空 bucket；需要管理员开放删除权限。", {"bucket": bucket}, ["bucket"]),
        tool("minio_list_objects", "按 bucket、前缀和文件名关键字检索对象，支持游标分页。", {
            "bucket": bucket,
            "prefix": {"type": "string", "description": "可选对象前缀"},
            "search": {"type": "string", "description": "可选文件名/Key 子串"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            "cursor": {"type": "string", "description": "上一页 next_cursor"},
        }, ["bucket"]),
        tool("minio_get_object_metadata", "查看对象大小、类型、ETag、时间和自定义元数据。", {"bucket": bucket, "key": key}, ["bucket", "key"]),
        tool("minio_read_object", "读取 UTF-8 文本对象；二进制对象应改用预签名链接。", {
            "bucket": bucket, "key": key,
            "max_bytes": {"type": "integer", "minimum": 1, "maximum": 1000000, "default": 200000},
        }, ["bucket", "key"]),
        tool("minio_upload_text", "上传或覆盖 UTF-8 文本对象；最多 2MB。", {
            "bucket": bucket, "key": key,
            "content": {"type": "string", "description": "文本内容"},
            "content_type": {"type": "string", "default": "text/plain; charset=utf-8"},
        }, ["bucket", "key", "content"]),
        tool("minio_upload_base64", "上传或覆盖 Base64 编码对象；解码后最多 5MB。", {
            "bucket": bucket, "key": key,
            "content_base64": {"type": "string", "description": "标准 Base64 数据"},
            "content_type": {"type": "string", "default": "application/octet-stream"},
        }, ["bucket", "key", "content_base64"]),
        tool("minio_copy_object", "复制对象，可跨 bucket。", {
            "source_bucket": bucket, "source_key": key,
            "destination_bucket": bucket, "destination_key": key,
        }, ["source_bucket", "source_key", "destination_bucket", "destination_key"]),
        tool("minio_move_object", "移动对象（复制成功后删除源对象）；需要删除权限。", {
            "source_bucket": bucket, "source_key": key,
            "destination_bucket": bucket, "destination_key": key,
        }, ["source_bucket", "source_key", "destination_bucket", "destination_key"]),
        tool("minio_delete_object", "删除对象；需要管理员开放删除权限。", {"bucket": bucket, "key": key}, ["bucket", "key"]),
        tool("minio_get_presigned_url", "生成限时 GET 下载或 PUT 上传 URL，适合大文件和二进制文件。", {
            "bucket": bucket, "key": key,
            "method": {"type": "string", "enum": ["GET", "PUT"], "default": "GET"},
            "expires_seconds": {"type": "integer", "minimum": 60, "maximum": 604800, "default": 3600},
        }, ["bucket", "key"]),
    ]


def execute_minio_tool(db: Session, name: str, arguments: dict[str, Any] | None,
                       *, actor_type: str = "mcp", actor_id: str | None = None) -> str:
    args = dict(arguments or {})
    service = ConfiguredMinioService.from_db(db)
    operation_args = {
        "bucket": args.get("bucket") or args.get("destination_bucket") or args.get("source_bucket"),
        "object_key": args.get("key") or args.get("destination_key") or args.get("source_key"),
    }
    try:
        if not service.config.mcp_enabled:
            raise MinioServiceError("MinIO MCP 已被管理员停用")
        if name == "minio_status":
            result = service.status()
        elif name == "minio_list_buckets":
            result = {"buckets": service.list_buckets()}
        elif name == "minio_create_bucket":
            result = service.create_bucket(args.get("bucket", ""))
        elif name == "minio_delete_bucket":
            result = service.delete_bucket(args.get("bucket", ""))
        elif name == "minio_list_objects":
            result = service.list_objects(
                bucket=args.get("bucket", ""), prefix=str(args.get("prefix") or ""),
                search=str(args.get("search") or ""), limit=int(args.get("limit") or 100),
                cursor=str(args.get("cursor") or ""),
            )
        elif name == "minio_get_object_metadata":
            result = service.stat_object(args.get("bucket", ""), args.get("key", ""))
        elif name == "minio_read_object":
            result = service.read_object(
                bucket=args.get("bucket", ""), key=args.get("key", ""),
                max_bytes=max(1, min(int(args.get("max_bytes") or 200000), 1_000_000)),
            )
        elif name == "minio_upload_text":
            content = str(args.get("content") or "")
            data = content.encode("utf-8")
            if len(data) > 2_000_000:
                raise MinioServiceError("文本内容超过 2MB MCP 上传上限")
            result = service.upload_bytes(
                bucket=args.get("bucket", ""), key=args.get("key", ""), data=data,
                content_type=str(args.get("content_type") or "text/plain; charset=utf-8"),
            )
        elif name == "minio_upload_base64":
            try:
                data = base64.b64decode(str(args.get("content_base64") or ""), validate=True)
            except Exception as exc:
                raise MinioServiceError("content_base64 不是有效的标准 Base64") from exc
            if len(data) > 5_000_000:
                raise MinioServiceError("对象超过 5MB MCP Base64 上传上限；请改用预签名 PUT URL")
            result = service.upload_bytes(
                bucket=args.get("bucket", ""), key=args.get("key", ""), data=data,
                content_type=str(args.get("content_type") or "application/octet-stream"),
            )
        elif name in {"minio_copy_object", "minio_move_object"}:
            method = service.move_object if name == "minio_move_object" else service.copy_object
            result = method(
                source_bucket=args.get("source_bucket", ""), source_key=args.get("source_key", ""),
                destination_bucket=args.get("destination_bucket", ""), destination_key=args.get("destination_key", ""),
            )
        elif name == "minio_delete_object":
            result = service.delete_object(bucket=args.get("bucket", ""), key=args.get("key", ""))
        elif name == "minio_get_presigned_url":
            result = service.presign(
                bucket=args.get("bucket", ""), key=args.get("key", ""),
                method=str(args.get("method") or "GET"),
                expires_seconds=int(args.get("expires_seconds") or 3600),
            )
        else:
            raise MinioServiceError(f"未知 MinIO 工具：{name}")
        audit_operation(db, actor_type=actor_type, actor_id=actor_id, operation=name, **operation_args)
        return json.dumps({"ok": True, "result": result}, ensure_ascii=False, default=str)
    except Exception as exc:
        audit_operation(
            db, actor_type=actor_type, actor_id=actor_id, operation=name,
            success=False, details={"error_type": exc.__class__.__name__}, **operation_args,
        )
        raise
