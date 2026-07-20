"""
基于 MinIO 的对象存储服务。
桶: raw-datasets, curated-datasets, media, intermediate
"""
from __future__ import annotations

import io
import os
from pathlib import Path
from typing import BinaryIO, Mapping

import logging

try:
    from minio import Minio
    from minio.error import S3Error
    _MINIO_AVAILABLE = True
except ImportError:
    Minio = None  # type: ignore
    S3Error = Exception  # type: ignore
    _MINIO_AVAILABLE = False

from app.config import settings

logger = logging.getLogger(__name__)

BUCKETS = ["raw-datasets", "curated-datasets", "media", "intermediate"]


class StorageService:
    """MinIO object storage with an explicit development-only local fallback.

    使用默认配置时全进程共享同一个客户端; 连接失败后 60 秒内不再重试,
    避免每次实例化都触发 urllib3 多次重连 (此前是测试与请求变慢的主因)。
    """

    _shared_client = None
    _shared_unavailable_until: float = 0.0
    _RETRY_INTERVAL = 60.0

    def __init__(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        secure: bool | None = None,
    ):
        import time
        self._available = False
        self._client = None
        self._allow_local_fallback = bool(settings.storage_local_fallback)
        if not _MINIO_AVAILABLE:
            logger.warning("MinIO client not installed — storage unavailable")
            return

        is_default = endpoint is None and access_key is None and secret_key is None and secure is None
        cls = StorageService
        if is_default:
            if cls._shared_client is not None:
                self._client = cls._shared_client
                self._available = True
                return
            if time.monotonic() < cls._shared_unavailable_until:
                return
        try:
            client = Minio(
                endpoint or settings.minio_endpoint,
                access_key=access_key or settings.minio_access_key,
                secret_key=secret_key or settings.minio_secret_key,
                secure=secure if secure is not None else settings.minio_use_ssl,
            )
            client.list_buckets()  # 连接验证
            self._client = client
            self._available = True
            if is_default:
                cls._shared_client = client
            logger.info("MinIO connected")
        except Exception as e:
            logger.warning(f"MinIO unavailable: {e}")
            self._available = False
            if is_default:
                cls._shared_unavailable_until = time.monotonic() + cls._RETRY_INTERVAL

    # ── 本地文件系统 fallback ─────────────────────────────────────
    # The previous ``../../../../storage`` escaped the repository and resolved
    # to Desktop/storage. Keep the development fallback inside the backend.
    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    _LOCAL_BASE = str((_PROJECT_ROOT / "storage").resolve())

    @property
    def available(self) -> bool:
        return bool(self._available and self._client) or self._allow_local_fallback

    def _require_available(self):
        if self._available and self._client:
            return
        if self._allow_local_fallback:
            return
        raise RuntimeError(
            "对象存储不可用，且生产环境禁止本地文件回退；本次操作未写入任何资产"
        )

    @classmethod
    def _configured_local_base(cls) -> Path:
        configured = Path(settings.storage_local_dir).expanduser()
        if not configured.is_absolute():
            configured = cls._PROJECT_ROOT / configured
        return configured.resolve()

    @staticmethod
    def _validate_bucket(bucket: str) -> str:
        if not bucket or bucket in {".", ".."} or "/" in bucket or "\\" in bucket:
            raise ValueError(f"Invalid storage bucket: {bucket!r}")
        return bucket

    def _local_path(self, bucket: str, key: str, *, create_parent: bool = False) -> str:
        bucket = self._validate_bucket(bucket)
        if not key or Path(key).is_absolute():
            raise ValueError(f"Invalid storage object key: {key!r}")

        base = self._configured_local_base()
        candidate = (base / bucket / key).resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"Storage object key escapes local root: {key!r}") from exc

        if create_parent:
            candidate.parent.mkdir(parents=True, exist_ok=True)
        return str(candidate)

    def ensure_bucket(self, bucket: str) -> None:
        """桶不存在则创建。"""
        self._require_available()
        bucket = self._validate_bucket(bucket)
        if not self._client:
            (self._configured_local_base() / bucket).mkdir(parents=True, exist_ok=True)
            return
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)

    def ensure_default_buckets(self) -> None:
        """初始化全部 4 个默认桶。"""
        for b in BUCKETS:
            self.ensure_bucket(b)

    def put_object(
        self,
        bucket: str,
        key: str,
        data: BinaryIO,
        content_type: str = "application/octet-stream",
        length: int = -1,
        metadata: Mapping[str, str] | None = None,
    ) -> str:
        """上传对象并返回 URI(s3://bucket/key)。"""
        self.ensure_bucket(bucket)
        if self._available and self._client:
            # minio-py 在 length=-1 时使用 chunked read
            self._client.put_object(
                bucket, key, data, length=length, content_type=content_type,
                metadata=dict(metadata or {}),
            )
        else:
            self._require_available()
            local = self._local_path(bucket, key, create_parent=True)
            with open(local, "wb") as target:
                while True:
                    chunk = data.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
        return f"s3://{bucket}/{key}"

    def put_bytes(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ) -> str:
        """上传 bytes。MinIO 未连接时回退本地文件。"""
        if self._available and self._client:
            return self.put_object(
                bucket, key, io.BytesIO(data), content_type,
                length=len(data), metadata=metadata,
            )
        self._require_available()
        # Explicit development/test fallback.
        local = self._local_path(bucket, key, create_parent=True)
        with open(local, "wb") as f:
            f.write(data)
        return f"s3://{bucket}/{key}"

    def get_object(self, uri: str) -> bytes:
        """按 s3://bucket/key URI 下载对象。含本地回退。"""
        bucket, key = self._parse_uri(uri)
        if self._available and self._client:
            resp = self._client.get_object(bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()
        self._require_available()
        # Explicit development/test fallback.
        local = self._local_path(bucket, key)
        if os.path.exists(local):
            with open(local, "rb") as f:
                return f.read()
        raise FileNotFoundError(f"Object not found locally: {uri}")

    def get_stream(self, uri: str) -> BinaryIO:
        """按 s3://bucket/key URI 返回流。"""
        bucket, key = self._parse_uri(uri)
        if self._available and self._client:
            return self._client.get_object(bucket, key)
        self._require_available()
        return open(self._local_path(bucket, key), "rb")

    def presigned_get(self, uri: str, expires_seconds: int = 3600) -> str:
        """生成下载用 presigned URL。"""
        from datetime import timedelta
        bucket, key = self._parse_uri(uri)
        if not (self._available and self._client):
            self._require_available()
            raise RuntimeError("本地开发存储不支持 presigned URL")
        url = self._client.presigned_get_object(
            bucket, key, expires=timedelta(seconds=expires_seconds)
        )
        return url

    def delete_object(self, uri: str) -> None:
        """删除对象。对象已不存在视为成功；MinIO 未连接时回退本地文件。"""
        bucket, key = self._parse_uri(uri)
        if self._available and self._client:
            try:
                self._client.remove_object(bucket, key)
            except S3Error as e:
                if getattr(e, "code", "") not in ("NoSuchKey", "NoSuchBucket"):
                    raise
            return
        self._require_available()
        # 本地回退（不走 _local_path，删除不该顺手建目录）
        local = self._local_path(bucket, key)
        if os.path.exists(local):
            os.remove(local)

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        """返回 prefix 下的对象键列表。"""
        if self._available and self._client:
            objects = self._client.list_objects(bucket, prefix=prefix, recursive=True)
            return [f"s3://{bucket}/{obj.object_name}" for obj in objects]
        self._require_available()
        bucket = self._validate_bucket(bucket)
        base = str(self._configured_local_base() / bucket)
        if not os.path.isdir(base):
            return []
        result: list[str] = []
        for root, _dirs, files in os.walk(base):
            for filename in files:
                key = os.path.relpath(os.path.join(root, filename), base).replace(os.sep, "/")
                if key.startswith(prefix):
                    result.append(f"s3://{bucket}/{key}")
        return sorted(result)

    def object_exists(self, uri: str) -> bool:
        """检查对象是否存在。MinIO 不可用时回退本地文件系统。"""
        bucket, key = self._parse_uri(uri)
        if self._available and self._client:
            try:
                self._client.stat_object(bucket, key)
                return True
            except S3Error:
                return False
        self._require_available()
        return os.path.exists(self._local_path(bucket, key))

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str]:
        """s3://bucket/key → (bucket, key)"""
        if not uri.startswith("s3://"):
            raise ValueError(f"Invalid storage URI: {uri!r}. Expected s3://bucket/key")
        path = uri[5:]
        bucket, _, key = path.partition("/")
        if not bucket or not key:
            raise ValueError(f"Invalid storage URI: {uri!r}")
        return bucket, key


# 单例实例 (供 FastAPI 依赖注入使用)
_storage_service: StorageService | None = None


def get_storage_service() -> StorageService:
    global _storage_service
    if _storage_service is None:
        _storage_service = StorageService()
    return _storage_service
