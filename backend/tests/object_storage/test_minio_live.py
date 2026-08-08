"""Opt-in live round-trip for the env-backed MinIO MCP workspace service.

Run with LIVE_MINIO_ENDPOINT, LIVE_MINIO_ACCESS_KEY and LIVE_MINIO_SECRET_KEY.
The test uses a unique workspace bucket and removes every object and the bucket
itself in finally.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.settings.object_storage.models import MinioOperationAudit
from app.settings.object_storage.service import (
    WorkspaceMinioService,
    execute_minio_tool,
)
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


def test_live_workspace_service_round_trip(tmp_path, monkeypatch):
    endpoint, access_key, secret_key = _live_settings()
    bucket = f"openontology-e2e-{uuid.uuid4().hex[:12]}"
    service = WorkspaceMinioService(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=endpoint.lower().startswith("https"),
        bucket=bucket,
        allow_delete=True,
        timeout_seconds=15,
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'live.db'}")
    Base.metadata.create_all(bind=engine, tables=[MinioOperationAudit.__table__])
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(
        "app.settings.object_storage.service.get_workspace_minio_service",
        lambda: service,
    )

    try:
        status = service.status()
        assert status["connected"] is True and status["bucket"] == bucket

        uploaded = service.upload_bytes(
            key="http/hello.txt",
            data="OpenOntology live MinIO round-trip".encode(),
            content_type="text/plain; charset=utf-8",
        )
        assert uploaded["uri"] == f"s3://{bucket}/http/hello.txt"
        listing = service.list_objects(search="HELLO", limit=20)
        assert [item["key"] for item in listing["objects"]] == ["http/hello.txt"]
        assert "round-trip" in service.read_object(key="http/hello.txt")["content"]

        copied = service.copy_object(
            source_key="http/hello.txt", destination_key="http/copied.txt",
        )
        assert copied["destination"].endswith("http/copied.txt")
        moved = service.move_object(
            source_key="http/copied.txt", destination_key="http/moved.txt",
        )
        assert moved["moved"] is True
        assert service.presign(key="http/hello.txt", expires_seconds=300)["url"].startswith("http")

        with Session() as db:
            mcp_upload = execute_minio_tool(db, "minio_upload_text", {
                "key": "mcp/note.md", "content": "live MCP upload and read",
            }, actor_type="live_test", actor_id="live-admin")
            assert '"ok": true' in mcp_upload
            mcp_read = execute_minio_tool(db, "minio_read_object", {"key": "mcp/note.md"})
            assert "live MCP upload and read" in mcp_read
            audits = db.query(MinioOperationAudit).all()
            assert {audit.operation for audit in audits} == {
                "minio_upload_text", "minio_read_object",
            }
            assert all(audit.bucket == bucket for audit in audits)
    finally:
        # Cleanup is intentionally best-effort so a failed assertion does not
        # leave test data behind on the shared live MinIO instance.
        for key in ("http/hello.txt", "http/copied.txt", "http/moved.txt", "mcp/note.md"):
            try:
                service.delete_object(key=key)
            except Exception:
                pass
        try:
            service._client.remove_bucket(bucket)
        except Exception:
            pass
