"""
基于 MinIO 的对象存储服务。
桶: raw-datasets, curated-datasets, media, intermediate
"""
from __future__ import annotations

import io
import os
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
    """Required MinIO object storage with legacy local-object read support.

    使用默认配置时全进程共享同一个客户端; 连接失败后 60 秒内不再重试,
    避免每次实例化都触发 urllib3 多次重连 (此前是测试与请求变慢的主因)。

    New objects are always written to MinIO. ``local://`` is accepted only so
    installations can read and migrate objects created by older fallback code.
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
    ):
        self._available = False
        self._client = None
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
            and secure is None and region is None
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

    # Historical local-object root. It is read-only compatibility for assets
    # written by older releases and is never selected for new writes.
    _PROJECT_ROOT = Path(__file__).resolve().parents[2]

    @property
    def available(self) -> bool:
        return bool(self._available and self._client)

    def _maybe_reconnect(self) -> None:
        """Reconnect after the retry window without replacing this service.

        The storage service is process-scoped. Without an in-place retry, a
        transient MinIO outage would pin the process to an unavailable client
        until its next restart.
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
        fail quickly instead of repeating a network timeout during the backoff
        window.
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
            "MinIO %s failed; storage unavailable for up to %.0f seconds: %s",
            operation,
            self._RETRY_INTERVAL,
            exc,
        )
        return True

    def _require_available(self):
        if self._available and self._client:
            return
        raise RuntimeError("MinIO 对象存储不可用；本次操作未写入任何资产")

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

    def _local_path(self, bucket: str, key: str) -> str:
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

        return str(candidate)

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
                raise
        self._require_available()

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
        self._validate_key(key)
        self._maybe_reconnect()
        self._require_available()
        try:
            self._ensure_minio_bucket(bucket)
            self._client.put_object(
                bucket, key, data, length=length, content_type=content_type,
                metadata=dict(metadata or {}),
            )
            return f"s3://{bucket}/{key}"
        except Exception as exc:
            self._mark_minio_unavailable(exc, operation="upload")
            raise

    def put_bytes(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ) -> str:
        """Upload bytes to required MinIO storage."""
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
        if scheme == "local":
            local = self._local_path(bucket, key)
            if not os.path.isfile(local):
                raise FileNotFoundError(f"Local object not found: {uri}")
            with open(local, "rb") as stream:
                return stream.read()

        self._maybe_reconnect()
        self._require_available()
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
            if self._is_not_found_error(exc):
                local = self._local_path(bucket, key)
                if os.path.isfile(local):
                    logger.info(
                        "Reading historical local object labelled as S3: %s", uri)
                    with open(local, "rb") as stream:
                        return stream.read()
            self._mark_minio_unavailable(exc, operation="download")
            raise

    def get_stream(self, uri: str) -> BinaryIO:
        """Return a stream from the backend encoded in the URI."""
        scheme, bucket, key = self._parse_uri_with_scheme(uri)
        if scheme == "local":
            local = self._local_path(bucket, key)
            return open(local, "rb")

        self._maybe_reconnect()
        self._require_available()
        try:
            return self._client.get_object(bucket, key)
        except Exception as exc:
            if self._is_not_found_error(exc):
                local = self._local_path(bucket, key)
                if os.path.isfile(local):
                    logger.info(
                        "Streaming historical local object labelled as S3: %s", uri)
                    return open(local, "rb")
            self._mark_minio_unavailable(exc, operation="stream download")
            raise

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
        if scheme == "local":
            local = self._local_path(bucket, key)
            if os.path.isfile(local):
                os.remove(local)
            return

        self._maybe_reconnect()
        self._require_available()
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
        local = self._local_path(bucket, key)
        if os.path.isfile(local):
            os.remove(local)

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        """Return MinIO objects plus readable legacy local objects."""
        bucket = self._validate_bucket(bucket)
        self._maybe_reconnect()
        self._require_available()
        try:
            objects = self._client.list_objects(bucket, prefix=prefix, recursive=True)
            result = [f"s3://{bucket}/{obj.object_name}" for obj in objects]
        except Exception as exc:
            self._mark_minio_unavailable(exc, operation="list")
            raise

        # Resolve through the same bucket-root guard used by object operations;
        # a symlinked bucket must not turn listing into an out-of-root read.
        base = str(Path(self._local_path(bucket, "__list_probe__")).parent)
        if not os.path.isdir(base):
            return sorted(result)
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
        if scheme == "local":
            return self._local_exists(bucket, key)

        self._maybe_reconnect()
        self._require_available()
        try:
            self._client.stat_object(bucket, key)
            return True
        except S3Error as exc:
            if not self._is_not_found_error(exc):
                self._mark_minio_unavailable(exc, operation="stat")
                raise
            return self._local_exists(bucket, key)
        except Exception as exc:
            self._mark_minio_unavailable(exc, operation="stat")
            raise

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


class LegacyManagedStorageAccess:
    """Read/delete-only access to objects written by an old endpoint regression.

    A previous development-mode implementation could redirect platform objects
    to the administrator-managed MinIO row. Stable runtime writes must never use
    that row, but regression-era dataset versions, file assets, media objects,
    and file-connector objects still need an explicit migration path.
    Deliberately do not expose any upload method from this adapter.
    """

    def __init__(self, storage: StorageService):
        self._storage = storage

    def get_object(self, uri: str) -> bytes:
        return self._storage.get_object(uri)

    def get_stream(self, uri: str) -> BinaryIO:
        return self._storage.get_stream(uri)

    def presigned_get(self, uri: str, expires_seconds: int = 3600) -> str:
        return self._storage.presigned_get(uri, expires_seconds)

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        return self._storage.list_prefix(bucket, prefix)

    def object_exists(self, uri: str) -> bool:
        return self._storage.object_exists(uri)

    def delete_object(self, uri: str) -> None:
        self._storage.delete_object(uri)


class PlatformStorageAccess:
    """Authoritative writes plus explicit read/delete migration compatibility.

    Every mutating write is delegated only to the environment MinIO. Historical
    objects written to the former administrator-managed endpoint remain
    readable only after the healthy authoritative endpoint has positively
    reported a miss. Operational failures never activate the legacy endpoint.
    """

    def __init__(self, authoritative: StorageService):
        self._authoritative = authoritative

    def __getattr__(self, name: str):
        return getattr(self._authoritative, name)

    def _legacy(self) -> LegacyManagedStorageAccess | None:
        return get_legacy_managed_storage_access()

    def get_object(self, uri: str) -> bytes:
        try:
            return self._authoritative.get_object(uri)
        except Exception as exc:
            if (
                not uri.startswith("s3://")
                or not StorageService._is_not_found_error(exc)
            ):
                raise
            legacy = self._legacy()
            if legacy is None:
                raise
            return legacy.get_object(uri)

    def get_stream(self, uri: str) -> BinaryIO:
        try:
            return self._authoritative.get_stream(uri)
        except Exception as exc:
            if (
                not uri.startswith("s3://")
                or not StorageService._is_not_found_error(exc)
            ):
                raise
            legacy = self._legacy()
            if legacy is None:
                raise
            return legacy.get_stream(uri)

    def presigned_get(self, uri: str, expires_seconds: int = 3600) -> str:
        if uri.startswith("local://"):
            return self._authoritative.presigned_get(uri, expires_seconds)
        if self._authoritative.object_exists(uri):
            return self._authoritative.presigned_get(uri, expires_seconds)
        legacy = self._legacy()
        if legacy is None:
            raise FileNotFoundError(uri)
        return legacy.presigned_get(uri, expires_seconds)

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        # The authoritative listing must succeed first. A MinIO outage is not
        # permission to return a legacy-only partial result.
        result = self._authoritative.list_prefix(bucket, prefix)
        legacy = self._legacy()
        if legacy is not None:
            result.extend(legacy.list_prefix(bucket, prefix))
        return sorted(set(result))

    def object_exists(self, uri: str) -> bool:
        if uri.startswith("local://"):
            return self._authoritative.object_exists(uri)
        if self._authoritative.object_exists(uri):
            return True
        legacy = self._legacy()
        return legacy.object_exists(uri) if legacy is not None else False

    def delete_object(self, uri: str) -> None:
        if uri.startswith("local://"):
            self._authoritative.delete_object(uri)
            return

        # ``remove_object`` itself does not report whether the key existed, so
        # first use the authoritative service's strict stat contract. A
        # connection/auth/service failure raises here and must retain the
        # caller's durable deletion task instead of touching the old endpoint.
        if self._authoritative.object_exists(uri):
            self._authoritative.delete_object(uri)
            return

        # A false result means the healthy environment MinIO positively
        # reported a miss (and no historical local object matched). Only now
        # may deletion be routed to the read/delete-only regression adapter.
        legacy = self._legacy()
        if legacy is not None:
            legacy.delete_object(uri)


# 平台对象持久化只认部署环境中的 MinIO。系统设置里的 MinIO 管理/MCP 客户端
# 是独立能力，不能在运行中暗中替换这里的权威端点并把对象分散到第二个后端。
_storage_service: PlatformStorageAccess | None = None
_environment_storage_service: StorageService | None = None
_legacy_managed_storage_access: LegacyManagedStorageAccess | None = None
_legacy_managed_storage_resolved = False


def get_environment_storage_service() -> StorageService:
    """Return the authoritative deployment store without consulting DB config."""
    global _environment_storage_service
    if _environment_storage_service is None:
        _environment_storage_service = StorageService()
    return _environment_storage_service


def get_storage_service() -> PlatformStorageAccess:
    """Return authoritative storage with read/delete-only migration access."""
    global _storage_service
    if _storage_service is None:
        # One authoritative endpoint prevents objects from being split between
        # an env-configured store and a later administrator override.
        _storage_service = PlatformStorageAccess(
            get_environment_storage_service()
        )
    return _storage_service


def clear_environment_storage_backoff() -> None:
    """Let business storage reconnect after readiness proves MinIO recovered."""
    StorageService._shared_unavailable_until = 0.0
    if _environment_storage_service is not None:
        _environment_storage_service._next_reconnect_at = 0.0


def get_legacy_managed_storage_access() -> LegacyManagedStorageAccess | None:
    """Return explicit read/delete-only access for regression-era objects.

    This accessor is never consulted for new writes and must only be tried after
    the authoritative environment MinIO has positively reported an object miss
    (or returned bytes whose stored checksum proves they are not the right
    immutable version). An environment connectivity failure is not permission
    to fall back to this endpoint.
    """
    global _legacy_managed_storage_access, _legacy_managed_storage_resolved
    if _legacy_managed_storage_resolved:
        return _legacy_managed_storage_access

    try:
        from app.database import SessionLocal
        from app.services.encryption_service import decrypt
        from app.settings.object_storage.models import MinioConfig

        db = SessionLocal()
        try:
            config = db.query(MinioConfig).filter(
                MinioConfig.id == "default",
                MinioConfig.enabled.is_(True),
                MinioConfig.connected.is_(True),
            ).first()
            if config is None:
                _legacy_managed_storage_resolved = True
                return None
            storage = StorageService(
                endpoint=config.endpoint,
                access_key=decrypt(config.access_key_encrypted),
                secret_key=decrypt(config.secret_key_encrypted),
                secure=config.secure,
                region=config.region,
            )
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            "Legacy managed MinIO configuration resolution failed (%s)",
            type(exc).__name__,
        )
        # Do not cache a transient database/decryption failure as "no legacy
        # endpoint". In particular, deletion outbox processing must retain the
        # task rather than silently orphaning a regression-era object.
        raise RuntimeError(
            "Legacy managed MinIO configuration is unavailable"
        ) from exc

    _legacy_managed_storage_access = LegacyManagedStorageAccess(storage)
    _legacy_managed_storage_resolved = True
    return _legacy_managed_storage_access


def reset_storage_service() -> None:
    """Reset compatibility accessors without changing the environment client."""
    global _storage_service
    global _legacy_managed_storage_access, _legacy_managed_storage_resolved
    _storage_service = None
    legacy = _legacy_managed_storage_access
    legacy_client = getattr(getattr(legacy, "_storage", None), "_client", None)
    legacy_http = getattr(legacy_client, "_http", None)
    legacy_clear = getattr(legacy_http, "clear", None)
    if callable(legacy_clear):
        legacy_clear()
    _legacy_managed_storage_access = None
    _legacy_managed_storage_resolved = False
