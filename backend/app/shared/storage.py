"""
基于 MinIO 的对象存储服务。
桶: raw-datasets, curated-datasets, media, intermediate
"""
from __future__ import annotations

import io
import os
import tempfile
import time
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
    """MinIO object storage with a durable local-filesystem fallback.

    使用默认配置时全进程共享同一个客户端; 连接失败后 60 秒内不再重试,
    避免每次实例化都触发 urllib3 多次重连 (此前是测试与请求变慢的主因)。

    ``s3://`` and ``local://`` identify the physical backend.  This matters
    when MinIO recovers after an outage: an object written to the fallback must
    continue to be read from the shared local volume instead of accidentally
    being looked up only in MinIO.
    """

    _shared_client = None
    _shared_unavailable_until: float = 0.0
    _RETRY_INTERVAL = 60.0

    @classmethod
    def unavailable(cls) -> "StorageService":
        """Build a fail-closed service without attempting a second endpoint."""
        instance = cls.__new__(cls)
        instance._available = False
        instance._client = None
        instance._allow_local_fallback = False
        instance._connection_options = None
        instance._is_default = False
        instance._next_reconnect_at = float("inf")
        return instance

    def __init__(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        secure: bool | None = None,
        region: str | None = None,
        allow_local_fallback: bool | None = None,
    ):
        self._available = False
        self._client = None
        self._allow_local_fallback = (
            bool(settings.storage_local_fallback)
            if allow_local_fallback is None else bool(allow_local_fallback)
        )
        self._connection_options = {
            "endpoint": endpoint or settings.minio_endpoint,
            "access_key": access_key or settings.minio_access_key,
            "secret_key": secret_key or settings.minio_secret_key,
            "secure": secure if secure is not None else settings.minio_use_ssl,
            "region": region or None,
        }
        self._next_reconnect_at = 0.0
        if not _MINIO_AVAILABLE:
            logger.warning("MinIO client not installed — storage unavailable")
            self._next_reconnect_at = float("inf")
            return

        is_default = (
            endpoint is None and access_key is None and secret_key is None
            and secure is None and region is None and allow_local_fallback is None
        )
        self._is_default = is_default
        cls = StorageService
        if is_default:
            if cls._shared_client is not None:
                self._client = cls._shared_client
                self._available = True
                return
            if time.monotonic() < cls._shared_unavailable_until:
                self._next_reconnect_at = cls._shared_unavailable_until
                return
        try:
            client = Minio(**self._connection_options)
            client.list_buckets()  # 连接验证
            self._client = client
            self._available = True
            if is_default:
                cls._shared_client = client
            logger.info("MinIO connected")
        except Exception as e:
            logger.warning(f"MinIO unavailable: {e}")
            self._available = False
            self._next_reconnect_at = time.monotonic() + self._RETRY_INTERVAL
            if is_default:
                cls._shared_unavailable_until = self._next_reconnect_at

    # ── 本地文件系统 fallback ─────────────────────────────────────
    # The previous ``../../../../storage`` escaped the repository and resolved
    # to Desktop/storage. Keep the development fallback inside the backend.
    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    _LOCAL_BASE = str((_PROJECT_ROOT / "storage").resolve())

    @property
    def available(self) -> bool:
        return bool(self._available and self._client) or self._allow_local_fallback

    def _maybe_reconnect(self) -> None:
        """Reconnect after the retry window without replacing this service.

        The storage service is process-scoped.  Without an in-place retry, a
        MinIO outage during process startup would pin the process to local
        storage until its next restart.
        """
        if self._available and self._client:
            return
        options = getattr(self, "_connection_options", None)
        if not _MINIO_AVAILABLE or not options:
            return
        now = time.monotonic()
        if now < getattr(self, "_next_reconnect_at", 0.0):
            return
        try:
            client = Minio(**options)
            client.list_buckets()
        except Exception as exc:
            self._next_reconnect_at = now + self._RETRY_INTERVAL
            if getattr(self, "_is_default", False):
                StorageService._shared_unavailable_until = self._next_reconnect_at
            logger.warning("MinIO reconnect failed: %s", exc)
            return
        self._client = client
        self._available = True
        self._next_reconnect_at = 0.0
        if getattr(self, "_is_default", False):
            StorageService._shared_client = client
            StorageService._shared_unavailable_until = 0.0
        logger.info("MinIO reconnected")

    def _mark_minio_unavailable(
        self,
        exc: Exception,
        *,
        operation: str,
    ) -> bool:
        """Open the runtime circuit breaker for a real MinIO failure.

        A missing object/bucket is an application-level result and must not
        poison an otherwise healthy client.  Connection, service, credential,
        and other operational failures clear the client so subsequent calls
        use the local backend immediately instead of repeating a network
        timeout during the backoff window.
        """
        if self._is_not_found_error(exc):
            return False

        client = self._client
        self._client = None
        self._available = False
        deadline = time.monotonic() + self._RETRY_INTERVAL
        self._next_reconnect_at = deadline

        if getattr(self, "_is_default", False):
            if StorageService._shared_client is client:
                StorageService._shared_client = None
            StorageService._shared_unavailable_until = deadline

        http = getattr(client, "_http", None)
        clear = getattr(http, "clear", None)
        if callable(clear):
            try:
                clear()
            except Exception:
                logger.debug("Failed to clear MinIO HTTP pool", exc_info=True)

        logger.warning(
            "MinIO %s failed; local fallback active for %.0f seconds: %s",
            operation,
            self._RETRY_INTERVAL,
            exc,
        )
        return True

    def _require_available(self):
        if self._available and self._client:
            return
        if self._allow_local_fallback:
            return
        raise RuntimeError(
            "对象存储不可用，且当前配置禁止本地文件回退；本次操作未写入任何资产"
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

    @staticmethod
    def _validate_key(key: str) -> str:
        if (
            not key
            or Path(key).is_absolute()
            or "\\" in key
            or "\x00" in key
            or any(part in {".", ".."} for part in key.split("/"))
        ):
            raise ValueError(f"Invalid storage object key: {key!r}")
        return key

    def _local_path(self, bucket: str, key: str, *, create_parent: bool = False) -> str:
        bucket = self._validate_bucket(bucket)
        key = self._validate_key(key)

        base = self._configured_local_base()
        bucket_base = (base / bucket).resolve()
        candidate = (bucket_base / key).resolve()
        try:
            bucket_base.relative_to(base)
            candidate.relative_to(bucket_base)
        except ValueError as exc:
            raise ValueError(f"Storage object key escapes bucket root: {key!r}") from exc

        if create_parent:
            candidate.parent.mkdir(parents=True, exist_ok=True)
        return str(candidate)

    def _write_local(self, bucket: str, key: str, data: BinaryIO) -> None:
        """Atomically write one object below the configured local root."""
        destination = Path(self._local_path(bucket, key, create_parent=True))
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as target:
                temporary_name = target.name
                while True:
                    chunk = data.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_name, destination)
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def _local_exists(self, bucket: str, key: str) -> bool:
        return os.path.isfile(self._local_path(bucket, key))

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        return getattr(exc, "code", "") in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}

    def _ensure_minio_bucket(self, bucket: str) -> None:
        if not self._client:
            raise RuntimeError("MinIO is unavailable")
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)

    def ensure_bucket(self, bucket: str) -> None:
        """桶不存在则创建。"""
        bucket = self._validate_bucket(bucket)
        self._maybe_reconnect()
        if self._available and self._client:
            try:
                self._ensure_minio_bucket(bucket)
                return
            except Exception as exc:
                self._mark_minio_unavailable(exc, operation="bucket check")
                if not self._allow_local_fallback:
                    raise
                logger.warning(
                    "MinIO bucket check failed; using local storage for bucket %s",
                    bucket,
                    exc_info=True,
                )
        self._require_available()
        if self._allow_local_fallback:
            bucket_path = Path(
                self._local_path(bucket, "__bucket_probe__")
            ).parent
            bucket_path.mkdir(parents=True, exist_ok=True)
            return
        raise RuntimeError("对象存储不可用")

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
        """Upload an object and return a backend-specific storage URI."""
        bucket = self._validate_bucket(bucket)
        # Validate traversal even when the current write goes to MinIO.  A
        # future local fallback must never reinterpret an unsafe key.
        self._local_path(bucket, key)
        self._maybe_reconnect()

        source = data
        rewind_position: int | None = None
        staged: BinaryIO | None = None
        if self._allow_local_fallback and self._available and self._client:
            try:
                rewind_position = int(data.tell())
                data.seek(rewind_position)
            except (AttributeError, OSError, TypeError, ValueError):
                staged = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
                while True:
                    chunk = data.read(1024 * 1024)
                    if not chunk:
                        break
                    staged.write(chunk)
                staged.seek(0)
                source = staged
                rewind_position = 0

        if self._available and self._client:
            fallback_after_minio_error = False
            try:
                self._ensure_minio_bucket(bucket)
                # minio-py 在 length=-1 时使用 chunked read
                self._client.put_object(
                    bucket, key, source, length=length, content_type=content_type,
                    metadata=dict(metadata or {}),
                )
                return f"s3://{bucket}/{key}"
            except Exception as exc:
                self._mark_minio_unavailable(exc, operation="upload")
                if not self._allow_local_fallback:
                    raise
                logger.warning(
                    "MinIO upload failed; writing local fallback %s/%s",
                    bucket,
                    key,
                    exc_info=True,
                )
                fallback_after_minio_error = True
                if rewind_position is None:
                    raise RuntimeError(
                        "MinIO 上传失败，且输入流无法安全回退到本地存储"
                    )
                source.seek(rewind_position)
            finally:
                # Do not close the caller-owned input stream.
                if staged is not None and not fallback_after_minio_error:
                    staged.close()

        try:
            self._require_available()
            self._write_local(bucket, key, source)
            return f"local://{bucket}/{key}"
        finally:
            if staged is not None:
                staged.close()

    def put_bytes(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ) -> str:
        """Upload bytes, falling back atomically to the local shared volume."""
        return self.put_object(
            bucket,
            key,
            io.BytesIO(data),
            content_type,
            length=len(data),
            metadata=metadata,
        )

    def get_object(self, uri: str) -> bytes:
        """Read an object from the backend encoded in its URI.

        Historical fallback objects were incorrectly labelled ``s3://``.
        Therefore an S3 miss also checks the same key under the local root.
        """
        scheme, bucket, key = self._parse_uri_with_scheme(uri)
        local = self._local_path(bucket, key)
        if scheme == "local":
            if not os.path.isfile(local):
                raise FileNotFoundError(f"Local object not found: {uri}")
            with open(local, "rb") as stream:
                return stream.read()

        self._maybe_reconnect()
        if self._available and self._client:
            try:
                resp = self._client.get_object(bucket, key)
                try:
                    return resp.read()
                finally:
                    resp.close()
                    release = getattr(resp, "release_conn", None)
                    if callable(release):
                        release()
            except Exception as exc:
                self._mark_minio_unavailable(exc, operation="download")
                if not os.path.isfile(local):
                    raise
                logger.info("Reading historical local object labelled as S3: %s", uri)
        elif not os.path.isfile(local):
            self._require_available()
            raise FileNotFoundError(f"Object not found locally: {uri}")

        if os.path.isfile(local):
            with open(local, "rb") as f:
                return f.read()
        raise FileNotFoundError(f"Object not found locally: {uri}")

    def get_stream(self, uri: str) -> BinaryIO:
        """Return a stream from the backend encoded in the URI."""
        scheme, bucket, key = self._parse_uri_with_scheme(uri)
        local = self._local_path(bucket, key)
        if scheme == "local":
            return open(local, "rb")

        self._maybe_reconnect()
        if self._available and self._client:
            try:
                return self._client.get_object(bucket, key)
            except Exception as exc:
                self._mark_minio_unavailable(exc, operation="stream download")
                if not os.path.isfile(local):
                    raise
                logger.info("Streaming historical local object labelled as S3: %s", uri)
        elif not os.path.isfile(local):
            self._require_available()
        return open(local, "rb")

    def presigned_get(self, uri: str, expires_seconds: int = 3600) -> str:
        """生成下载用 presigned URL。"""
        from datetime import timedelta
        scheme, bucket, key = self._parse_uri_with_scheme(uri)
        if scheme != "s3":
            raise RuntimeError("local:// 对象不支持 MinIO presigned URL，请使用平台下载接口")
        self._maybe_reconnect()
        if not (self._available and self._client):
            self._require_available()
            raise RuntimeError("MinIO 不可用，无法生成 presigned URL")
        try:
            return self._client.presigned_get_object(
                bucket, key, expires=timedelta(seconds=expires_seconds)
            )
        except Exception as exc:
            self._mark_minio_unavailable(exc, operation="presigned URL")
            raise

    def delete_object(self, uri: str) -> None:
        """Delete an object from its encoded backend; missing is success."""
        scheme, bucket, key = self._parse_uri_with_scheme(uri)
        local = self._local_path(bucket, key)
        if scheme == "local":
            if os.path.isfile(local):
                os.remove(local)
            return

        self._maybe_reconnect()
        if self._available and self._client:
            try:
                self._client.remove_object(bucket, key)
            except S3Error as e:
                if not self._is_not_found_error(e):
                    self._mark_minio_unavailable(e, operation="delete")
                    raise
            except Exception as exc:
                self._mark_minio_unavailable(exc, operation="delete")
                raise
            # Also remove a legacy fallback object that was labelled s3://.
            if os.path.isfile(local):
                os.remove(local)
            return
        if os.path.isfile(local):
            os.remove(local)
            return
        self._require_available()

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        """Return MinIO and durable-local objects below ``prefix``."""
        bucket = self._validate_bucket(bucket)
        self._maybe_reconnect()
        result: list[str] = []
        if self._available and self._client:
            try:
                objects = self._client.list_objects(bucket, prefix=prefix, recursive=True)
                result.extend(f"s3://{bucket}/{obj.object_name}" for obj in objects)
            except Exception as exc:
                self._mark_minio_unavailable(exc, operation="list")
                if not self._allow_local_fallback:
                    raise
                logger.warning(
                    "MinIO listing failed; returning local objects for %s/%s",
                    bucket,
                    prefix,
                    exc_info=True,
                )

        # Resolve through the same bucket-root guard used by object operations;
        # a symlinked bucket must not turn listing into an out-of-root read.
        base = str(Path(self._local_path(bucket, "__list_probe__")).parent)
        if not os.path.isdir(base):
            if result or self._allow_local_fallback:
                return sorted(result)
            self._require_available()
            return []
        for root, _dirs, files in os.walk(base):
            for filename in files:
                if filename.startswith(".") and filename.endswith(".tmp"):
                    continue
                key = os.path.relpath(os.path.join(root, filename), base).replace(os.sep, "/")
                if key.startswith(prefix):
                    result.append(f"local://{bucket}/{key}")
        return sorted(set(result))

    def object_exists(self, uri: str) -> bool:
        """Check the encoded backend, with legacy s3-labelled-local support."""
        scheme, bucket, key = self._parse_uri_with_scheme(uri)
        local_exists = self._local_exists(bucket, key)
        if scheme == "local":
            return local_exists

        self._maybe_reconnect()
        if self._available and self._client:
            try:
                self._client.stat_object(bucket, key)
                return True
            except S3Error as exc:
                if not self._is_not_found_error(exc):
                    self._mark_minio_unavailable(exc, operation="stat")
                    if not self._allow_local_fallback and not local_exists:
                        raise
                return local_exists
            except Exception as exc:
                self._mark_minio_unavailable(exc, operation="stat")
                if local_exists:
                    return True
                if self._allow_local_fallback:
                    return False
                raise
        if local_exists:
            return True
        self._require_available()
        return False

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str]:
        """Parse either supported backend URI into ``(bucket, key)``.

        This method is also used by a latency-sensitive compatibility path.
        Keep its common case allocation-light: ``Path.is_absolute`` and
        repeated helper calls made 100k parses several times slower on CI.
        Suspicious dot-prefixed segments take the slower split/check branch,
        while ordinary generated object keys need only constant string scans.
        """
        raw = uri if isinstance(uri, str) else str(uri)
        if raw.startswith("s3://"):
            path = raw[5:]
        elif raw.startswith("local://"):
            path = raw[8:]
        else:
            raise ValueError(
                f"Invalid storage URI: {uri!r}. Expected s3://bucket/key "
                "or local://bucket/key"
            )

        bucket, separator, key = path.partition("/")
        if not separator or not bucket or not key:
            raise ValueError(f"Invalid storage URI: {uri!r}")
        if bucket in {".", ".."} or "\\" in bucket:
            raise ValueError(f"Invalid storage bucket: {bucket!r}")
        windows_absolute = (
            len(key) >= 3
            and key[1] == ":"
            and key[2] == "/"
            and key[0].isalpha()
        )
        if key[0] == "/" or windows_absolute or "\\" in key or "\x00" in key:
            raise ValueError(f"Invalid storage object key: {key!r}")
        if "/." in path and any(
            part in {".", ".."} for part in key.split("/")
        ):
            raise ValueError(f"Invalid storage object key: {key!r}")
        return bucket, key

    @staticmethod
    def _parse_uri_with_scheme(uri: str) -> tuple[str, str, str]:
        """Parse ``s3://`` or ``local://`` into ``(scheme, bucket, key)``."""
        raw = uri if isinstance(uri, str) else str(uri)
        if raw.startswith("s3://"):
            scheme = "s3"
        elif raw.startswith("local://"):
            scheme = "local"
        else:
            raise ValueError(
                f"Invalid storage URI: {uri!r}. Expected s3://bucket/key "
                "or local://bucket/key"
            )
        bucket, key = StorageService._parse_uri(raw)
        return scheme, bucket, key


# 管理员配置的对象存储与部署环境自带的历史数据集对象存储必须保持两个
# 独立身份。管理员 MinIO 服务于文件资产、HTTP 管理接口和 MCP；环境端点仅
# 用于兼容读取迁移前已经写入对象存储的数据集版本。
_storage_service: StorageService | None = None
_environment_storage_service: StorageService | None = None


def get_environment_storage_service() -> StorageService:
    """Return the deployment-configured object store without consulting DB config.

    New tabular dataset versions no longer write here.  The explicit accessor is
    retained for legacy dataset reads so changing the administrator-managed MinIO
    endpoint cannot strand versions created before that change.
    """
    global _environment_storage_service
    if _environment_storage_service is None:
        _environment_storage_service = StorageService()
    return _environment_storage_service


def get_storage_service() -> StorageService:
    """Return the shared client, preferring a verified administrator config."""
    global _storage_service
    if settings.require_external_dependencies:
        # The committed production dependency manifest is authoritative in
        # fail-closed mode. A stale administrator row must not silently route
        # runtime objects to a different MinIO endpoint.
        _storage_service = get_environment_storage_service()
        return _storage_service
    if _storage_service is None:
        config = None
        try:
            from app.database import SessionLocal
            from app.settings.object_storage.models import MinioConfig

            db = SessionLocal()
            try:
                config = db.query(MinioConfig).filter(
                    MinioConfig.id == "default",
                    MinioConfig.enabled.is_(True),
                    MinioConfig.connected.is_(True),
                ).first()
            finally:
                db.close()
        except Exception as exc:
            # Old databases may not yet have the new configuration table.
            logger.debug("Database MinIO configuration unavailable: %s", exc)
        else:
            if config:
                try:
                    from app.services.encryption_service import decrypt

                    _storage_service = StorageService(
                        endpoint=config.endpoint,
                        access_key=decrypt(config.access_key_encrypted),
                        secret_key=decrypt(config.secret_key_encrypted),
                        secure=config.secure,
                        region=config.region,
                        # The URI records the backend, so a managed endpoint
                        # outage can safely use the same durable local fallback
                        # without making those objects look like MinIO data.
                        allow_local_fallback=settings.storage_local_fallback,
                    )
                except Exception as exc:
                    # Credential decryption/configuration failures remain closed:
                    # unlike an endpoint outage, the intended identity is unknown.
                    logger.warning("Managed MinIO configuration is unusable: %s", exc)
                    _storage_service = StorageService.unavailable()
        if _storage_service is None:
            _storage_service = get_environment_storage_service()
    return _storage_service


def reset_storage_service() -> None:
    """Drop the cached client after an administrator changes configuration."""
    global _storage_service
    if (_storage_service is not None
            and _storage_service is not _environment_storage_service):
        client = getattr(_storage_service, "_client", None)
        http = getattr(client, "_http", None)
        clear = getattr(http, "clear", None)
        if callable(clear):
            clear()
    _storage_service = None
