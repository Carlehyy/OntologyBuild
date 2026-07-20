from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.settings.object_storage import mcp_server
from app.settings.object_storage.models import MinioConfig
from app.shared.database import Base


def test_streamable_http_auth_initialize_list_and_call(tmp_path, monkeypatch):
    token = "protocol-test-token"
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mcp-protocol.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine, tables=[MinioConfig.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(MinioConfig(
            id="default",
            enabled=True,
            endpoint="minio.invalid:9000",
            secure=False,
            region="us-east-1",
            default_bucket="openontology",
            access_key_encrypted="unused",
            secret_key_encrypted="unused",
            read_enabled=True,
            write_enabled=True,
            delete_enabled=False,
            mcp_enabled=True,
            connected=True,
            mcp_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        ))
        db.commit()

    monkeypatch.setattr(mcp_server, "SessionLocal", Session)
    monkeypatch.setattr(
        mcp_server,
        "execute_minio_tool",
        lambda db, name, arguments, **kwargs: '{"ok":true,"result":{"connected":true}}',
    )

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp_server.reset_session_manager().run():
            yield

    app = FastAPI(lifespan=lifespan)

    class Proxy:
        def __init__(self, app):
            self.wrapped = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http" and scope.get("path") == "/mcp/minio":
                headers = dict(scope.get("headers") or [])
                auth = headers.get(b"authorization", b"").decode("latin-1")
                if not auth.startswith("Bearer "):
                    response = b'{"detail":"Missing MinIO MCP Bearer token"}'
                    await send({
                        "type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json")],
                    })
                    await send({"type": "http.response.body", "body": response})
                    return
                try:
                    mcp_server.validate_bearer_token(auth.removeprefix("Bearer ").strip())
                except HTTPException as exc:
                    response = str(exc.detail).encode()
                    await send({"type": "http.response.start", "status": exc.status_code, "headers": []})
                    await send({"type": "http.response.body", "body": response})
                    return
                forwarded = dict(scope)
                forwarded["path"] = "/"
                forwarded["raw_path"] = b"/"
                await mcp_server.handle_mcp(forwarded, receive, send)
                return
            await self.wrapped(scope, receive, send)

    app.add_middleware(Proxy)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with TestClient(app) as client:
        unauthorized = client.post("/mcp/minio", json={})
        assert unauthorized.status_code == 401
        initialized = client.post("/mcp/minio", headers=headers, json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        })
        assert initialized.status_code == 200, initialized.text
        assert initialized.json()["result"]["serverInfo"]["name"] == "openontology-minio"

        tools = client.post("/mcp/minio", headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        assert tools.status_code == 200, tools.text
        names = {item["name"] for item in tools.json()["result"]["tools"]}
        assert {"minio_upload_text", "minio_read_object", "minio_list_objects"} <= names

        called = client.post("/mcp/minio", headers=headers, json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "minio_status", "arguments": {}},
        })
        assert called.status_code == 200, called.text
        assert '"connected":true' in called.json()["result"]["content"][0]["text"]
