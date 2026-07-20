"""Opt-in live MinIO HTTP + MCP round-trip.

Run with LIVE_MINIO_ENDPOINT, LIVE_MINIO_ACCESS_KEY and LIVE_MINIO_SECRET_KEY.
The test creates a unique bucket and removes every object and bucket in finally.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.settings.object_storage import mcp_server, router as minio_router
from app.settings.object_storage.models import MinioConfig, MinioOperationAudit
from app.settings.object_storage.service import execute_minio_tool, reset_configured_client
from app.shared.database import Base


def _live_settings() -> tuple[str, str, str]:
    values = (
        os.getenv("LIVE_MINIO_ENDPOINT", ""),
        os.getenv("LIVE_MINIO_ACCESS_KEY", ""),
        os.getenv("LIVE_MINIO_SECRET_KEY", ""),
    )
    if not all(values):
        pytest.skip("live MinIO environment is not configured")
    return values


def test_live_minio_http_and_mcp_round_trip(tmp_path, monkeypatch):
    endpoint, access_key, secret_key = _live_settings()
    bucket = f"openontology-e2e-{uuid.uuid4().hex[:12]}"
    engine = create_engine(
        f"sqlite:///{tmp_path / 'live.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine, tables=[
        User.__table__, MinioConfig.__table__, MinioOperationAudit.__table__,
    ])
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    admin = User(
        id="live-admin", username="live-admin", email="live@example.com",
        password_hash="unused", role="admin",
    )
    with Session() as db:
        db.add(admin)
        db.commit()

    def override_db():
        with Session() as db:
            yield db

    app = FastAPI()
    app.include_router(minio_router.router, prefix="/api/v1/settings")
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: admin
    client = TestClient(app)
    monkeypatch.setattr(mcp_server, "SessionLocal", Session)

    configured = client.post("/api/v1/settings/minio-config/test", json={
        "enabled": True,
        "endpoint": endpoint,
        "secure": endpoint.lower().startswith("https://"),
        "region": "us-east-1",
        "default_bucket": bucket,
        "access_key": access_key,
        "secret_key": secret_key,
        "read_enabled": True,
        "write_enabled": True,
        "delete_enabled": True,
        "mcp_enabled": True,
        "create_default_bucket": True,
        "timeout_seconds": 15,
    })
    assert configured.status_code == 200, configured.text
    assert configured.json()["ok"] is True, configured.text
    token = configured.json()["mcp_token"]
    assert token

    try:
        uploaded = client.post("/api/v1/settings/minio/objects/text", json={
            "bucket": bucket,
            "key": "http/hello.txt",
            "content": "OpenOntology live MinIO HTTP round-trip",
            "content_type": "text/plain; charset=utf-8",
        })
        assert uploaded.status_code == 201, uploaded.text
        listing = client.get("/api/v1/settings/minio/objects", params={
            "bucket": bucket, "search": "HELLO", "limit": 20,
        })
        assert [item["key"] for item in listing.json()["objects"]] == ["http/hello.txt"]
        preview = client.get("/api/v1/settings/minio/objects/preview", params={
            "bucket": bucket, "key": "http/hello.txt",
        })
        assert "HTTP round-trip" in preview.json()["content"]
        downloaded = client.get("/api/v1/settings/minio/objects/download", params={
            "bucket": bucket, "key": "http/hello.txt",
        })
        assert downloaded.content == b"OpenOntology live MinIO HTTP round-trip"

        copied = client.post("/api/v1/settings/minio/objects/copy", json={
            "source_bucket": bucket, "source_key": "http/hello.txt",
            "destination_bucket": bucket, "destination_key": "http/copied.txt",
        })
        assert copied.status_code == 200, copied.text
        moved = client.post("/api/v1/settings/minio/objects/move", json={
            "source_bucket": bucket, "source_key": "http/copied.txt",
            "destination_bucket": bucket, "destination_key": "http/moved.txt",
        })
        assert moved.json()["moved"] is True
        presigned = client.post("/api/v1/settings/minio/objects/presign", json={
            "bucket": bucket, "key": "http/hello.txt", "method": "GET", "expires_seconds": 300,
        })
        assert presigned.status_code == 200 and presigned.json()["url"].startswith("http")

        with Session() as db:
            mcp_upload = execute_minio_tool(db, "minio_upload_text", {
                "bucket": bucket, "key": "mcp/note.md", "content": "live MCP upload and read",
            }, actor_type="live_test", actor_id=admin.id)
            assert '"ok": true' in mcp_upload
            mcp_read = execute_minio_tool(db, "minio_read_object", {
                "bucket": bucket, "key": "mcp/note.md",
            }, actor_type="live_test", actor_id=admin.id)
            assert "live MCP upload and read" in mcp_read
            mcp_search = execute_minio_tool(db, "minio_list_objects", {
                "bucket": bucket, "search": "note.md",
            }, actor_type="live_test", actor_id=admin.id)
            assert "mcp/note.md" in mcp_search

        mcp_server.validate_bearer_token(token)
        public_config = client.get("/api/v1/settings/minio-config")
        assert token not in public_config.text and secret_key not in public_config.text
    finally:
        # Cleanup is intentionally best-effort so a failed assertion does not
        # leave test data behind on the shared live MinIO instance.
        for key in ("http/hello.txt", "http/copied.txt", "http/moved.txt", "mcp/note.md"):
            client.delete("/api/v1/settings/minio/objects", params={"bucket": bucket, "key": key})
        client.delete(f"/api/v1/settings/minio/buckets/{bucket}")
        reset_configured_client()
