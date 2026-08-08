"""超级助手内置 MinIO MCP 的工作区对象存储服务。

连接固定来自部署环境变量（``app.config.settings.minio_*``），与平台权威对象
存储同一个 MinIO；所有工具调用被服务端锁定到单一工作区桶
（``settings.minio_mcp_bucket``），模型无法接触平台数据桶或其他桶。
"""
from __future__ import annotations

import base64
import io
import ipaddress
import json
import re
import threading
from datetime import timedelta
from typing import Any, BinaryIO

import urllib3
from minio import Minio
from minio.commonconfig import CopySource
from sqlalchemy.orm import Session

from app.config import settings
from app.settings.object_storage.models import MinioOperationAudit


_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv",
    ".xml", ".yaml", ".yml", ".html", ".htm", ".css", ".js", ".ts",
    ".tsx", ".jsx", ".py", ".sql", ".log", ".ini", ".toml", ".conf",
}
# 早期版本的工具 schema 允许模型自选 bucket；工作区化之后这些参数一律拒绝，
# 让存量会话里的模型拿到明确错误并自我纠正。
_REJECTED_BUCKET_ARGS = ("bucket", "source_bucket", "destination_bucket")


class MinioServiceError(ValueError):
    pass


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
        raise MinioServiceError("MinIO Access Key 和 Secret Key 未在部署环境中配置")
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


def close_client(client: Minio | None) -> None:
    if client is None:
        return
    http = getattr(client, "_http", None)
    clear = getattr(http, "clear", None)
    if callable(clear):
        clear()


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


class WorkspaceMinioService:
    """Bucket-locked MinIO workspace for the built-in assistant MCP tools.

    连接参数只来自部署环境变量；bucket 由服务端强制，工具入参里没有 bucket
    概念。删除/移动需要部署方显式放开（``minio_mcp_allow_delete``）。
    """

    def __init__(self, *, endpoint: str, access_key: str, secret_key: str,
                 secure: bool, bucket: str, allow_delete: bool = False,
                 timeout_seconds: int = 10):
        self._bucket = validate_bucket_name(bucket)
        self._allow_delete = bool(allow_delete)
        self._client = build_client(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            timeout_seconds=timeout_seconds,
        )
        self._bucket_lock = threading.Lock()
        self._bucket_ready = False

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def allow_delete(self) -> bool:
        return self._allow_delete

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        with self._bucket_lock:
            if self._bucket_ready:
                return
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
            self._bucket_ready = True

    def _require_delete(self) -> None:
        if not self._allow_delete:
            raise MinioServiceError(
                "平台未开放 MinIO 删除能力（MINIO_MCP_ALLOW_DELETE 未开启）"
            )

    def status(self) -> dict[str, Any]:
        self._ensure_bucket()
        return {
            "connected": True,
            "bucket": self._bucket,
            "allow_delete": self._allow_delete,
        }

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

    def list_objects(self, *, prefix: str = "", search: str = "",
                     limit: int = 100, cursor: str = "", max_scan: int = 5000) -> dict[str, Any]:
        self._ensure_bucket()
        limit = max(1, min(int(limit), 500))
        keyword = (search or "").strip().lower()
        result: list[dict[str, Any]] = []
        scanned = 0
        last_key = ""
        truncated = False
        objects = self._client.list_objects(
            self._bucket,
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
            result.append(self._object_item(self._bucket, item))
            if len(result) >= limit:
                truncated = True
                break
        return {
            "bucket": self._bucket,
            "prefix": prefix,
            "search": search,
            "objects": result,
            "count": len(result),
            "scanned": scanned,
            "truncated": truncated,
            "next_cursor": last_key if truncated else None,
        }

    def stat_object(self, key: str) -> dict[str, Any]:
        object_key = validate_object_key(key)
        self._ensure_bucket()
        item = self._client.stat_object(self._bucket, object_key)
        payload = self._object_item(self._bucket, item)
        payload["metadata"] = dict(item.metadata or {})
        return payload

    def upload_stream(self, *, key: str, data: BinaryIO, length: int,
                      content_type: str = "application/octet-stream") -> dict[str, Any]:
        object_key = validate_object_key(key)
        self._ensure_bucket()
        result = self._client.put_object(
            self._bucket, object_key, data, length=length,
            content_type=content_type or "application/octet-stream",
        )
        return {
            "bucket": self._bucket,
            "key": object_key,
            "size": length,
            "etag": (result.etag or "").strip('"'),
            "version_id": result.version_id,
            "uri": f"s3://{self._bucket}/{object_key}",
        }

    def upload_bytes(self, *, key: str, data: bytes,
                     content_type: str = "application/octet-stream") -> dict[str, Any]:
        return self.upload_stream(
            key=key, data=io.BytesIO(data), length=len(data), content_type=content_type,
        )

    def read_object(self, *, key: str, max_bytes: int = 1_000_000) -> dict[str, Any]:
        object_key = validate_object_key(key)
        self._ensure_bucket()
        stat = self._client.stat_object(self._bucket, object_key)
        content_type = (stat.content_type or "application/octet-stream").split(";", 1)[0].lower()
        suffix = "." + object_key.rsplit(".", 1)[-1].lower() if "." in object_key else ""
        is_text = content_type.startswith("text/") or content_type in {
            "application/json", "application/x-ndjson", "application/xml",
            "application/yaml", "application/x-yaml", "application/javascript",
        } or suffix in _TEXT_EXTENSIONS
        if not is_text:
            raise MinioServiceError("该对象不是可安全预览的文本文件；请使用元数据或预签名下载链接")
        response = self._client.get_object(self._bucket, object_key)
        try:
            data = response.read(max_bytes + 1)
        finally:
            response.close()
            response.release_conn()
        truncated = len(data) > max_bytes or (stat.size or 0) > max_bytes
        data = data[:max_bytes]
        return {
            "bucket": self._bucket,
            "key": object_key,
            "content_type": stat.content_type,
            "size": stat.size,
            "content": data.decode("utf-8", errors="replace"),
            "truncated": truncated,
        }

    def delete_object(self, *, key: str) -> dict[str, Any]:
        self._require_delete()
        object_key = validate_object_key(key)
        self._ensure_bucket()
        self._client.remove_object(self._bucket, object_key)
        return {"bucket": self._bucket, "key": object_key, "deleted": True}

    def copy_object(self, *, source_key: str, destination_key: str) -> dict[str, Any]:
        src_key = validate_object_key(source_key)
        dst_key = validate_object_key(destination_key)
        self._ensure_bucket()
        result = self._client.copy_object(
            self._bucket, dst_key, CopySource(self._bucket, src_key),
        )
        return {
            "source": f"s3://{self._bucket}/{src_key}",
            "destination": f"s3://{self._bucket}/{dst_key}",
            "etag": (result.etag or "").strip('"'),
            "version_id": result.version_id,
        }

    def move_object(self, *, source_key: str, destination_key: str) -> dict[str, Any]:
        self._require_delete()
        result = self.copy_object(source_key=source_key, destination_key=destination_key)
        self._client.remove_object(self._bucket, validate_object_key(source_key))
        result["moved"] = True
        return result

    def presign(self, *, key: str, method: str = "GET",
                expires_seconds: int = 3600) -> dict[str, Any]:
        method = method.upper()
        object_key = validate_object_key(key)
        self._ensure_bucket()
        expires = timedelta(seconds=max(60, min(int(expires_seconds), 604800)))
        if method == "GET":
            url = self._client.presigned_get_object(self._bucket, object_key, expires=expires)
        elif method == "PUT":
            url = self._client.presigned_put_object(self._bucket, object_key, expires=expires)
        else:
            raise MinioServiceError("预签名方法仅支持 GET 或 PUT")
        return {
            "bucket": self._bucket,
            "key": object_key,
            "method": method,
            "expires_seconds": int(expires.total_seconds()),
            "url": url,
        }


_workspace_lock = threading.Lock()
_workspace_service: WorkspaceMinioService | None = None


def get_workspace_minio_service() -> WorkspaceMinioService:
    """进程级单例；连接参数固定取部署环境变量。"""
    global _workspace_service
    with _workspace_lock:
        if _workspace_service is None:
            _workspace_service = WorkspaceMinioService(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_use_ssl,
                bucket=settings.minio_mcp_bucket,
                allow_delete=settings.minio_mcp_allow_delete,
            )
        return _workspace_service


def reset_workspace_minio_service() -> None:
    """测试/配置变更时重建单例。"""
    global _workspace_service
    with _workspace_lock:
        close_client(getattr(_workspace_service, "_client", None))
        _workspace_service = None


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

    key = {"type": "string", "description": "对象 Key（含目录前缀）"}
    return [
        tool("minio_status", "检查平台 MinIO 工作区连接与桶状态。"),
        tool("minio_list_objects", "按前缀和文件名关键字检索工作区对象，支持游标分页。", {
            "prefix": {"type": "string", "description": "可选对象前缀"},
            "search": {"type": "string", "description": "可选文件名/Key 子串"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            "cursor": {"type": "string", "description": "上一页 next_cursor"},
        }),
        tool("minio_get_object_metadata", "查看对象大小、类型、ETag、时间和自定义元数据。", {"key": key}, ["key"]),
        tool("minio_read_object", "读取 UTF-8 文本对象；二进制对象应改用预签名链接。", {
            "key": key,
            "max_bytes": {"type": "integer", "minimum": 1, "maximum": 1000000, "default": 200000},
        }, ["key"]),
        tool("minio_upload_text", "上传或覆盖 UTF-8 文本对象；最多 2MB。", {
            "key": key,
            "content": {"type": "string", "description": "文本内容"},
            "content_type": {"type": "string", "default": "text/plain; charset=utf-8"},
        }, ["key", "content"]),
        tool("minio_upload_base64", "上传或覆盖 Base64 编码对象；解码后最多 5MB。", {
            "key": key,
            "content_base64": {"type": "string", "description": "标准 Base64 数据"},
            "content_type": {"type": "string", "default": "application/octet-stream"},
        }, ["key", "content_base64"]),
        tool("minio_copy_object", "在工作区内复制对象。", {
            "source_key": key, "destination_key": key,
        }, ["source_key", "destination_key"]),
        tool("minio_move_object", "在工作区内移动对象（复制成功后删除源对象）；需要平台开放删除能力。", {
            "source_key": key, "destination_key": key,
        }, ["source_key", "destination_key"]),
        tool("minio_delete_object", "删除工作区对象；需要平台开放删除能力。", {"key": key}, ["key"]),
        tool("minio_get_presigned_url", "生成限时 GET 下载或 PUT 上传 URL，适合大文件和二进制文件。", {
            "key": key,
            "method": {"type": "string", "enum": ["GET", "PUT"], "default": "GET"},
            "expires_seconds": {"type": "integer", "minimum": 60, "maximum": 604800, "default": 3600},
        }, ["key"]),
    ]


def execute_minio_tool(db: Session, name: str, arguments: dict[str, Any] | None,
                       *, actor_type: str = "mcp", actor_id: str | None = None) -> str:
    args = dict(arguments or {})
    service = get_workspace_minio_service()
    operation_args = {
        "bucket": service.bucket,
        "object_key": args.get("key") or args.get("destination_key") or args.get("source_key"),
    }
    try:
        rejected = [arg for arg in _REJECTED_BUCKET_ARGS if arg in args]
        if rejected:
            raise MinioServiceError(
                f"MinIO 工具固定操作平台工作区桶 {service.bucket}，"
                f"请移除参数：{', '.join(rejected)}"
            )
        if name == "minio_status":
            result = service.status()
        elif name == "minio_list_objects":
            result = service.list_objects(
                prefix=str(args.get("prefix") or ""),
                search=str(args.get("search") or ""),
                limit=int(args.get("limit") or 100),
                cursor=str(args.get("cursor") or ""),
            )
        elif name == "minio_get_object_metadata":
            result = service.stat_object(args.get("key", ""))
        elif name == "minio_read_object":
            result = service.read_object(
                key=args.get("key", ""),
                max_bytes=max(1, min(int(args.get("max_bytes") or 200000), 1_000_000)),
            )
        elif name == "minio_upload_text":
            content = str(args.get("content") or "")
            data = content.encode("utf-8")
            if len(data) > 2_000_000:
                raise MinioServiceError("文本内容超过 2MB MCP 上传上限")
            result = service.upload_bytes(
                key=args.get("key", ""), data=data,
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
                key=args.get("key", ""), data=data,
                content_type=str(args.get("content_type") or "application/octet-stream"),
            )
        elif name in {"minio_copy_object", "minio_move_object"}:
            method = service.move_object if name == "minio_move_object" else service.copy_object
            result = method(
                source_key=args.get("source_key", ""),
                destination_key=args.get("destination_key", ""),
            )
        elif name == "minio_delete_object":
            result = service.delete_object(key=args.get("key", ""))
        elif name == "minio_get_presigned_url":
            result = service.presign(
                key=args.get("key", ""),
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
