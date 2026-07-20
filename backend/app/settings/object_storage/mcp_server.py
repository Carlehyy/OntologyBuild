from __future__ import annotations

import asyncio
import json

import mcp.types as types
from fastapi import HTTPException
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from app.database import SessionLocal
from app.settings.object_storage.models import MinioConfig
from app.settings.object_storage.service import (
    execute_minio_tool,
    minio_tool_manifest,
    token_matches,
)


server = Server("openontology-minio")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=item["name"],
            description=item["description"],
            inputSchema=item["input_schema"],
        )
        for item in minio_tool_manifest()
    ]


def _call_tool_sync(name: str, arguments: dict | None) -> list[types.TextContent]:
    db = SessionLocal()
    try:
        output = execute_minio_tool(db, name, arguments, actor_type="external_mcp")
    except Exception as exc:
        output = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    finally:
        db.close()
    return [types.TextContent(type="text", text=output)]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    # minio-py is synchronous. Keep network I/O away from the ASGI event loop so
    # a slow object operation cannot stall unrelated platform requests.
    return await asyncio.to_thread(_call_tool_sync, name, arguments)


def validate_bearer_token(token: str) -> None:
    db = SessionLocal()
    try:
        config = db.query(MinioConfig).filter(MinioConfig.id == "default").first()
        if not config or not config.enabled or not config.connected or not config.mcp_enabled:
            raise HTTPException(status_code=503, detail="MinIO MCP is disabled")
        if not token_matches(config, token):
            raise HTTPException(status_code=401, detail="Invalid MinIO MCP token")
    finally:
        db.close()


def _build_session_manager() -> StreamableHTTPSessionManager:
    return StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


session_manager = _build_session_manager()


def reset_session_manager() -> StreamableHTTPSessionManager:
    global session_manager
    session_manager = _build_session_manager()
    return session_manager


async def handle_mcp(scope, receive, send) -> None:
    await session_manager.handle_request(scope, receive, send)
