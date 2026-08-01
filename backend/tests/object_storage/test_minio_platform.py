from __future__ import annotations

import io
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.services.encryption_service import decrypt
from app.settings.object_storage import router as minio_router
from app.settings.object_storage.models import MinioConfig, MinioOperationAudit
from app.settings.object_storage.service import (
    ConfiguredMinioService,
    MinioServiceError,
    execute_minio_tool,
    normalize_endpoint,
    token_matches,
    validate_bucket_name,
)
from app.shared.database import Base
from app.shared import storage as shared_storage
from app.super_assistant import router as super_assistant_router
from app.super_assistant.models import SuperAssistantMcpServer


class _Response(io.BytesIO):
    def release_conn(self):
        return None


class FakeMinio:
    def __init__(self):
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.buckets = {"existing"}

    def list_buckets(self):
        return [SimpleNamespace(name=name, creation_date=datetime.now(timezone.utc)) for name in sorted(self.buckets)]

    def bucket_exists(self, bucket):
        return bucket in self.buckets

    def make_bucket(self, bucket, location=None):
        self.buckets.add(bucket)

    def remove_bucket(self, bucket):
        if any(item_bucket == bucket for item_bucket, _ in self.objects):
            raise MinioServiceError("bucket not empty")
        self.buckets.remove(bucket)

    def put_object(self, bucket, key, data, length, content_type):
        self.objects[(bucket, key)] = (data.read(length), content_type)
        return SimpleNamespace(etag="etag-upload", version_id=None)

    def list_objects(self, bucket, prefix=None, recursive=True, start_after=None):
        for (item_bucket, key), (data, content_type) in sorted(self.objects.items()):
            if item_bucket != bucket or (prefix and not key.startswith(prefix)) or (start_after and key <= start_after):
                continue
            yield SimpleNamespace(
                object_name=key, size=len(data), etag="etag-list", last_modified=datetime.now(timezone.utc),
                content_type=content_type, version_id=None, is_dir=False,
            )

    def stat_object(self, bucket, key):
        data, content_type = self.objects[(bucket, key)]
        return SimpleNamespace(
            object_name=key, size=len(data), etag="etag-stat", last_modified=datetime.now(timezone.utc),
            content_type=content_type, version_id=None, is_dir=False, metadata={"x-amz-meta-test": "yes"},
        )

    def get_object(self, bucket, key):
        return _Response(self.objects[(bucket, key)][0])

    def remove_object(self, bucket, key):
        self.objects.pop((bucket, key), None)

    def copy_object(self, bucket, key, source):
        self.objects[(bucket, key)] = self.objects[(source.bucket_name, source.object_name)]
        return SimpleNamespace(etag="etag-copy", version_id=None)

    def presigned_get_object(self, bucket, key, expires):
        return f"https://storage.invalid/{bucket}/{key}?get=1"

    def presigned_put_object(self, bucket, key, expires):
        return f"https://storage.invalid/{bucket}/{key}?put=1"


class _ObjectMiss(RuntimeError):
    code = "NoSuchKey"


class _PlatformStore:
    """Small contract fake for the shared platform-storage routing boundary."""

    def __init__(self, name: str):
        self.name = name
        self.calls: list[tuple[str, tuple]] = []
        self.failures: dict[str, Exception] = {}
        self.exists = True

    def _record(self, method: str, *args):
        self.calls.append((method, args))
        failure = self.failures.get(method)
        if failure is not None:
            raise failure

    def ensure_bucket(self, bucket: str) -> None:
        self._record("ensure_bucket", bucket)

    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str):
        self._record("put_bytes", bucket, key, data, content_type)
        return f"s3://{bucket}/{key}"

    def get_object(self, uri: str) -> bytes:
        self._record("get_object", uri)
        return f"{self.name}:bytes".encode()

    def get_stream(self, uri: str):
        self._record("get_stream", uri)
        return io.BytesIO(f"{self.name}:stream".encode())

    def presigned_get(self, uri: str, expires_seconds: int = 3600) -> str:
        self._record("presigned_get", uri, expires_seconds)
        return f"https://{self.name}.invalid/object"

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        self._record("list_prefix", bucket, prefix)
        return [f"s3://{bucket}/{self.name}/{prefix}object.bin"]

    def object_exists(self, uri: str) -> bool:
        self._record("object_exists", uri)
        return self.exists

    def delete_object(self, uri: str) -> None:
        self._record("delete_object", uri)


def _config(**overrides):
    values = {
        "id": "default",
        "enabled": True,
        "endpoint": "minio.invalid:9000",
        "secure": False,
        "region": "us-east-1",
        "default_bucket": "existing",
        "access_key_encrypted": "unused",
        "secret_key_encrypted": "unused",
        "read_enabled": True,
        "write_enabled": True,
        "delete_enabled": False,
        "mcp_enabled": True,
        "connected": True,
    }
    values.update(overrides)
    return MinioConfig(**values)


def test_endpoint_and_bucket_validation():
    assert normalize_endpoint("http://minio.example:9000", secure=True) == ("minio.example:9000", False)
    assert normalize_endpoint("minio.example:9000", secure=True) == ("minio.example:9000", True)
    with pytest.raises(MinioServiceError, match="browser"):
        normalize_endpoint("http://minio.example:9001/browser", secure=False)
    assert validate_bucket_name("openontology-files") == "openontology-files"
    with pytest.raises(MinioServiceError):
        validate_bucket_name("192.168.1.2")


def test_service_round_trip_search_copy_preview_and_permissions():
    fake = FakeMinio()
    service = ConfiguredMinioService(_config(), fake)
    uploaded = service.upload_bytes(
        bucket="existing", key="docs/hello.txt", data="你好 MinIO".encode(), content_type="text/plain",
    )
    assert uploaded["uri"] == "s3://existing/docs/hello.txt"
    listing = service.list_objects(bucket="existing", search="HELLO")
    assert [item["key"] for item in listing["objects"]] == ["docs/hello.txt"]
    assert service.read_object(bucket="existing", key="docs/hello.txt")["content"] == "你好 MinIO"
    copied = service.copy_object(
        source_bucket="existing", source_key="docs/hello.txt",
        destination_bucket="existing", destination_key="archive/hello.txt",
    )
    assert copied["destination"] == "s3://existing/archive/hello.txt"
    assert "get=1" in service.presign(bucket="existing", key="docs/hello.txt")["url"]
    with pytest.raises(MinioServiceError, match="delete"):
        service.delete_object(bucket="existing", key="docs/hello.txt")


def test_mcp_tool_round_trip_writes_audit(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp.db'}")
    Base.metadata.create_all(bind=engine, tables=[MinioConfig.__table__, MinioOperationAudit.__table__])
    Session = sessionmaker(bind=engine)
    fake = FakeMinio()
    monkeypatch.setattr("app.settings.object_storage.service.configured_client", lambda _config: fake)
    with Session() as db:
        db.add(_config())
        db.commit()
        output = execute_minio_tool(db, "minio_upload_text", {
            "bucket": "existing", "key": "mcp/note.md", "content": "MCP upload",
        }, actor_type="test", actor_id="user-1")
        assert '"ok": true' in output
        read = execute_minio_tool(db, "minio_read_object", {
            "bucket": "existing", "key": "mcp/note.md",
        })
        assert "MCP upload" in read
        config = db.get(MinioConfig, "default")
        config.mcp_enabled = False
        db.commit()
        with pytest.raises(MinioServiceError, match="管理员停用"):
            execute_minio_tool(db, "minio_status", {})
        audits = db.query(MinioOperationAudit).order_by(MinioOperationAudit.created_at).all()
        assert len(audits) == 3
        assert audits[-1].operation == "minio_status"
        assert audits[-1].success is False


def test_platform_storage_never_uses_database_managed_minio(monkeypatch):
    authoritative = _PlatformStore("environment")
    legacy_resolutions = 0

    def resolve_legacy():
        nonlocal legacy_resolutions
        legacy_resolutions += 1
        raise AssertionError("new writes must not resolve database-managed MinIO")

    monkeypatch.setattr(shared_storage, "_storage_service", None)
    monkeypatch.setattr(
        shared_storage,
        "get_environment_storage_service",
        lambda: authoritative,
    )
    monkeypatch.setattr(
        shared_storage, "get_legacy_managed_storage_access", resolve_legacy,
    )

    platform = shared_storage.get_storage_service()

    assert isinstance(platform, shared_storage.PlatformStorageAccess)
    assert platform.put_bytes(
        "media", "new/report.pdf", b"%PDF", "application/pdf",
    ) == "s3://media/new/report.pdf"
    platform.ensure_bucket("media")
    assert authoritative.calls == [
        (
            "put_bytes",
            ("media", "new/report.pdf", b"%PDF", "application/pdf"),
        ),
        ("ensure_bucket", ("media",)),
    ]
    assert legacy_resolutions == 0


def test_platform_storage_uses_legacy_reads_only_after_authoritative_miss(
    monkeypatch,
):
    uri = "s3://media/regression/object.bin"
    authoritative = _PlatformStore("environment")
    authoritative.failures.update({
        "get_object": _ObjectMiss("missing"),
        "get_stream": _ObjectMiss("missing"),
    })
    authoritative.exists = False
    legacy_store = _PlatformStore("legacy")
    legacy = shared_storage.LegacyManagedStorageAccess(legacy_store)
    monkeypatch.setattr(
        shared_storage, "get_legacy_managed_storage_access", lambda: legacy,
    )
    platform = shared_storage.PlatformStorageAccess(authoritative)

    assert platform.get_object(uri) == b"legacy:bytes"
    with platform.get_stream(uri) as stream:
        assert stream.read() == b"legacy:stream"
    assert platform.object_exists(uri) is True
    assert platform.presigned_get(uri, 45) == "https://legacy.invalid/object"
    assert platform.list_prefix("media", "regression/") == [
        "s3://media/environment/regression/object.bin",
        "s3://media/legacy/regression/object.bin",
    ]
    platform.delete_object(uri)

    assert ("delete_object", (uri,)) not in authoritative.calls
    assert ("delete_object", (uri,)) in legacy_store.calls


@pytest.mark.parametrize(
    ("operation", "failing_method", "authoritative_exists"),
    [
        ("get", "get_object", True),
        ("stream", "get_stream", True),
        ("exists", "object_exists", True),
        ("list", "list_prefix", True),
        ("presign_stat", "object_exists", True),
        ("presign", "presigned_get", True),
        ("delete_stat", "object_exists", True),
        ("delete", "delete_object", True),
    ],
)
def test_platform_storage_operational_failure_never_uses_legacy_endpoint(
    operation, failing_method, authoritative_exists, monkeypatch,
):
    uri = "s3://media/current/object.bin"
    authoritative = _PlatformStore("environment")
    authoritative.exists = authoritative_exists
    authoritative.failures[failing_method] = PermissionError(
        "environment MinIO rejected request")
    legacy_resolutions = 0

    def resolve_legacy():
        nonlocal legacy_resolutions
        legacy_resolutions += 1
        return shared_storage.LegacyManagedStorageAccess(
            _PlatformStore("legacy"))

    monkeypatch.setattr(
        shared_storage, "get_legacy_managed_storage_access", resolve_legacy,
    )
    platform = shared_storage.PlatformStorageAccess(authoritative)
    calls = {
        "get": lambda: platform.get_object(uri),
        "stream": lambda: platform.get_stream(uri),
        "exists": lambda: platform.object_exists(uri),
        "list": lambda: platform.list_prefix("media", "current/"),
        "presign_stat": lambda: platform.presigned_get(uri),
        "presign": lambda: platform.presigned_get(uri),
        "delete_stat": lambda: platform.delete_object(uri),
        "delete": lambda: platform.delete_object(uri),
    }

    with pytest.raises(PermissionError, match="environment MinIO rejected"):
        calls[operation]()

    assert legacy_resolutions == 0


def test_platform_storage_authoritative_hit_hides_legacy_duplicate(monkeypatch):
    uri = "s3://media/current/object.bin"
    authoritative = _PlatformStore("environment")
    legacy_resolutions = 0

    def resolve_legacy():
        nonlocal legacy_resolutions
        legacy_resolutions += 1
        return shared_storage.LegacyManagedStorageAccess(
            _PlatformStore("legacy"))

    monkeypatch.setattr(
        shared_storage, "get_legacy_managed_storage_access", resolve_legacy,
    )
    platform = shared_storage.PlatformStorageAccess(authoritative)

    assert platform.get_object(uri) == b"environment:bytes"
    with platform.get_stream(uri) as stream:
        assert stream.read() == b"environment:stream"
    assert platform.object_exists(uri) is True
    assert platform.presigned_get(uri) == "https://environment.invalid/object"
    platform.delete_object(uri)

    assert legacy_resolutions == 0
    assert ("delete_object", (uri,)) in authoritative.calls


@pytest.mark.parametrize(
    ("operation", "legacy_method"),
    [
        ("get", "get_object"),
        ("stream", "get_stream"),
        ("exists", "object_exists"),
        ("list", "list_prefix"),
        ("presign", "presigned_get"),
        ("delete", "delete_object"),
    ],
)
def test_legacy_endpoint_failure_never_returns_a_partial_compatibility_result(
    operation, legacy_method, monkeypatch,
):
    uri = "s3://media/regression/object.bin"
    authoritative = _PlatformStore("environment")
    authoritative.exists = False
    authoritative.failures.update({
        "get_object": _ObjectMiss("missing"),
        "get_stream": _ObjectMiss("missing"),
    })
    legacy_store = _PlatformStore("legacy")
    legacy_store.failures[legacy_method] = ConnectionError(
        "legacy MinIO migration endpoint unavailable")
    monkeypatch.setattr(
        shared_storage,
        "get_legacy_managed_storage_access",
        lambda: shared_storage.LegacyManagedStorageAccess(legacy_store),
    )
    platform = shared_storage.PlatformStorageAccess(authoritative)
    calls = {
        "get": lambda: platform.get_object(uri),
        "stream": lambda: platform.get_stream(uri),
        "exists": lambda: platform.object_exists(uri),
        "list": lambda: platform.list_prefix("media", "regression/"),
        "presign": lambda: platform.presigned_get(uri),
        "delete": lambda: platform.delete_object(uri),
    }

    with pytest.raises(ConnectionError, match="migration endpoint unavailable"):
        calls[operation]()


def test_platform_storage_local_uri_never_resolves_database_endpoint(monkeypatch):
    uri = "local://media/legacy/object.bin"
    authoritative = _PlatformStore("environment")
    authoritative.exists = False
    authoritative.failures["get_object"] = FileNotFoundError(uri)
    legacy_resolutions = 0

    def resolve_legacy():
        nonlocal legacy_resolutions
        legacy_resolutions += 1
        return shared_storage.LegacyManagedStorageAccess(
            _PlatformStore("legacy"))

    monkeypatch.setattr(
        shared_storage, "get_legacy_managed_storage_access", resolve_legacy,
    )
    platform = shared_storage.PlatformStorageAccess(authoritative)

    with pytest.raises(FileNotFoundError):
        platform.get_object(uri)
    assert platform.object_exists(uri) is False
    platform.delete_object(uri)

    assert legacy_resolutions == 0


def test_legacy_managed_storage_access_exposes_only_read_and_delete(
    tmp_path, monkeypatch,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-storage.db'}")
    Base.metadata.create_all(bind=engine, tables=[MinioConfig.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(_config())
        db.commit()

    class LegacyClient:
        def __init__(self):
            self.deleted: list[str] = []

        def get_object(self, uri: str) -> bytes:
            return f"legacy:{uri}".encode()

        def get_stream(self, uri: str):
            return io.BytesIO(f"stream:{uri}".encode())

        def presigned_get(self, uri: str, expires_seconds: int = 3600):
            return f"https://legacy.invalid/{expires_seconds}"

        def list_prefix(self, bucket: str, prefix: str) -> list[str]:
            return [f"s3://{bucket}/{prefix}legacy.bin"]

        def object_exists(self, uri: str) -> bool:
            return uri.endswith("legacy.bin")

        def delete_object(self, uri: str) -> None:
            self.deleted.append(uri)

    legacy_client = LegacyClient()
    constructor_options = {}

    def build_legacy_client(**options):
        constructor_options.update(options)
        return legacy_client

    monkeypatch.setattr("app.database.SessionLocal", Session)
    monkeypatch.setattr(
        "app.services.encryption_service.decrypt", lambda _value: "decrypted")
    monkeypatch.setattr(shared_storage, "StorageService", build_legacy_client)
    monkeypatch.setattr(shared_storage, "_legacy_managed_storage_access", None)
    monkeypatch.setattr(shared_storage, "_legacy_managed_storage_resolved", False)

    access = shared_storage.get_legacy_managed_storage_access()

    assert access is not None
    assert access.get_object("s3://raw-datasets/legacy.bin") == (
        b"legacy:s3://raw-datasets/legacy.bin")
    with access.get_stream("s3://raw-datasets/legacy.bin") as stream:
        assert stream.read() == b"stream:s3://raw-datasets/legacy.bin"
    assert access.presigned_get(
        "s3://raw-datasets/legacy.bin", 45,
    ) == "https://legacy.invalid/45"
    assert access.list_prefix("raw-datasets", "old/") == [
        "s3://raw-datasets/old/legacy.bin"]
    assert access.object_exists("s3://raw-datasets/legacy.bin") is True
    access.delete_object("s3://raw-datasets/legacy.bin")
    assert legacy_client.deleted == ["s3://raw-datasets/legacy.bin"]
    assert not hasattr(access, "put_object")
    assert not hasattr(access, "put_bytes")
    assert constructor_options == {
        "endpoint": "minio.invalid:9000",
        "access_key": "decrypted",
        "secret_key": "decrypted",
        "secure": False,
        "region": "us-east-1",
    }


def test_legacy_storage_resolution_failure_is_not_cached_as_absent(
    monkeypatch,
):
    attempts = 0

    def unavailable_session():
        nonlocal attempts
        attempts += 1
        raise ConnectionError("database unavailable with secret material")

    monkeypatch.setattr("app.database.SessionLocal", unavailable_session)
    monkeypatch.setattr(shared_storage, "_legacy_managed_storage_access", None)
    monkeypatch.setattr(shared_storage, "_legacy_managed_storage_resolved", False)

    for _ in range(2):
        with pytest.raises(
            RuntimeError,
            match="Legacy managed MinIO configuration is unavailable",
        ):
            shared_storage.get_legacy_managed_storage_access()

    assert attempts == 2
    assert shared_storage._legacy_managed_storage_resolved is False


def test_admin_connection_saves_encrypted_credentials_and_token_once(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'router.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine, tables=[User.__table__, MinioConfig.__table__, MinioOperationAudit.__table__])
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    admin = User(
        id="admin-1", username="admin", email="admin@example.com",
        password_hash="unused", role="admin",
    )
    with Session() as db:
        db.add(admin)
        db.commit()

    def override_db():
        with Session() as db:
            yield db

    fake = FakeMinio()
    monkeypatch.setattr(minio_router, "build_client", lambda **_kwargs: fake)
    monkeypatch.setattr(minio_router, "close_client", lambda _client: None)
    app = FastAPI()
    app.include_router(minio_router.router, prefix="/api/v1/settings")
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: admin
    client = TestClient(app)
    body = {
        "enabled": True,
        "endpoint": "http://minio.invalid:9000",
        "secure": False,
        "region": "us-east-1",
        "default_bucket": "openontology",
        "access_key": "plain-access",
        "secret_key": "plain-secret",
        "read_enabled": True,
        "write_enabled": True,
        "delete_enabled": False,
        "mcp_enabled": True,
    }
    first = client.post("/api/v1/settings/minio-config/test", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["ok"] is True
    token = first.json()["mcp_token"]
    assert token and "plain-secret" not in first.text and "plain-access" not in first.text

    with Session() as db:
        saved = db.get(MinioConfig, "default")
        assert saved.secret_key_encrypted != "plain-secret"
        assert decrypt(saved.secret_key_encrypted) == "plain-secret"
        assert token_matches(saved, token)

    body["access_key"] = ""
    body["secret_key"] = ""
    second = client.post("/api/v1/settings/minio-config/test", json=body)
    assert second.json()["ok"] is True
    assert second.json()["mcp_token"] is None
    public = client.get("/api/v1/settings/minio-config").json()
    assert public["has_secret_key"] is True
    assert "encrypted" not in public and "plain-secret" not in str(public)


def test_super_assistant_installs_trusted_builtin_without_credentials(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'assistant.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine, tables=[
        User.__table__, MinioConfig.__table__, MinioOperationAudit.__table__,
        SuperAssistantMcpServer.__table__,
    ])
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    admin = User(
        id="admin-2", username="assistant-owner", email="owner@example.com",
        password_hash="unused", role="admin",
    )
    with Session() as db:
        db.add(admin)
        db.add(_config(mcp_token_hash="hash-is-not-used-by-builtin"))
        db.commit()

    def override_db():
        with Session() as db:
            yield db

    fake_service = SimpleNamespace(status=lambda: {"connected": True})
    monkeypatch.setattr(
        super_assistant_router.ConfiguredMinioService,
        "from_db",
        classmethod(lambda cls, db: fake_service),
    )
    app = FastAPI()
    app.include_router(super_assistant_router.router, prefix="/api/v2/super-assistant")
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: admin
    client = TestClient(app)

    installed = client.post("/api/v2/super-assistant/mcp-servers/platform-minio")
    assert installed.status_code == 200, installed.text
    payload = installed.json()
    assert payload["builtin_key"] == "minio"
    assert payload["url"] == "builtin://minio"
    assert len(payload["tool_manifest"]) == 13
    assert payload["header_names"] == []

    connection_edit = client.patch(
        f"/api/v2/super-assistant/mcp-servers/{payload['id']}",
        json={"url": "https://attacker.invalid/mcp"},
    )
    assert connection_edit.status_code == 400
    toggled = client.patch(
        f"/api/v2/super-assistant/mcp-servers/{payload['id']}",
        json={"enabled": False, "require_confirmation": False},
    )
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False

    with Session() as db:
        saved = db.get(SuperAssistantMcpServer, payload["id"])
        assert saved.builtin_key == "minio"
        assert saved.headers_encrypted is None
