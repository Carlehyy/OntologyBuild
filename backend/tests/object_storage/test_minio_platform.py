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


def test_managed_storage_fails_closed_when_credentials_cannot_decrypt(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'storage.db'}")
    Base.metadata.create_all(bind=engine, tables=[MinioConfig.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(_config(access_key_encrypted="invalid", secret_key_encrypted="invalid"))
        db.commit()

    monkeypatch.setattr("app.database.SessionLocal", Session)
    monkeypatch.setattr(shared_storage.settings, "storage_local_fallback", True)
    shared_storage.reset_storage_service()
    try:
        service = shared_storage.get_storage_service()
        assert service.available is False
        with pytest.raises(RuntimeError, match="禁止本地文件回退"):
            service.put_bytes("raw-datasets", "must-not-write.txt", b"data")
    finally:
        shared_storage.reset_storage_service()


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
