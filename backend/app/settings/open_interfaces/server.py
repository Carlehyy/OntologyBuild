from __future__ import annotations

import json
from contextvars import ContextVar

import mcp.types as types
from fastapi import HTTPException
from jose import JWTError
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from app.database import SessionLocal
from app.services.auth_service import decode_token, get_user_by_id
from app.services.mcp_catalog import list_published
from app.services.mcp_executor import call_interface

SERVER_NAME = "ontoprompt-open-interfaces"
server = Server(SERVER_NAME)

_current_app = None
_request_bearer_token: ContextVar[str | None] = ContextVar("mcp_request_bearer_token", default=None)


def bind_app(app) -> None:
    global _current_app
    _current_app = app


def _published_catalog() -> list[dict]:
    if _current_app is None:
        return []
    db = SessionLocal()
    try:
        return list_published(_current_app, db)
    finally:
        db.close()


def published_tools() -> list[dict]:
    return [
        {
            "operation_id": item["operation_id"],
            "name": item.get("display_name") or item.get("summary") or item["operation_id"],
            "method": item["method"],
            "path": item["path"],
        }
        for item in _published_catalog()
    ]


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_open_interfaces",
            description="列出 OntoPrompt 当前已开放为 MCP 的后端接口。调用具体接口前应先使用本工具获取 operation_id、方法、路径、参数和请求体说明。支持 keyword 过滤。",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "可选。按名称、标签、路径或方法过滤。"},
                },
            },
        ),
        types.Tool(
            name="call_open_interface",
            description="调用一个已开放的 OntoPrompt 后端接口。operation_id 来自 list_open_interfaces；path/query/body 分别提供路径参数、查询参数和 JSON 请求体。调用会沿用 MCP 请求的 OntoPrompt Bearer JWT，不绕过原有权限。",
            inputSchema={
                "type": "object",
                "properties": {
                    "operation_id": {"type": "string", "description": "目标接口 operation_id。"},
                    "path": {"type": "object", "description": "可选。路径参数，例如 ontology_id。", "additionalProperties": True},
                    "query": {"type": "object", "description": "可选。查询参数。", "additionalProperties": True},
                    "body": {"description": "可选。JSON 请求体。"},
                },
                "required": ["operation_id"],
            },
        ),
    ]


def _handle_list(keyword: str | None) -> list[types.TextContent]:
    kw = (keyword or "").strip().lower()
    items = []
    for item in _published_catalog():
        hay = f"{item.get('summary')} {item.get('description')} {' '.join(item.get('tags') or [])} {item['method']} {item['path']}".lower()
        if kw and kw not in hay:
            continue
        items.append({
            "operation_id": item["operation_id"],
            "name": item.get("display_name") or item.get("summary") or item["operation_id"],
            "description": item.get("config_description") or item.get("description") or "",
            "tags": item.get("tags") or [],
            "method": item["method"],
            "path": item["path"],
            "parameters": item.get("parameters") or [],
            "request_body": item.get("request_body"),
        })
    payload = {
        "count": len(items),
        "interfaces": items,
        "usage": "用 call_open_interface 并传 operation_id 调用其中某个接口。path/query/body 分别传路径参数、查询参数和 JSON 请求体。",
    }
    if not items:
        payload["usage"] = "当前没有已开放接口，或没有匹配 keyword 的接口。请在平台「系统设置 / 开放接口」里勾选。"
    return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


async def _handle_call(args: dict, bearer_token: str | None) -> list[types.TextContent]:
    if not bearer_token:
        return [types.TextContent(type="text", text="缺少 OntoPrompt Bearer JWT。请在 MCP 请求 Authorization header 中提供 Bearer token。")]
    operation_id = args.get("operation_id")
    if not operation_id:
        return [types.TextContent(type="text", text="缺少 operation_id。请先调用 list_open_interfaces。")]
    if _current_app is None:
        return [types.TextContent(type="text", text="MCP server 尚未绑定 FastAPI app。")]
    db = SessionLocal()
    try:
        result = await call_interface(_current_app, db, operation_id, bearer_token, args)
    except HTTPException as exc:
        return [types.TextContent(type="text", text=f"调用失败：HTTP {exc.status_code} {exc.detail}")]
    finally:
        db.close()
    status = "成功" if result["ok"] else "失败"
    text = f"HTTP {result['status_code']} · {status}\n\n{result['body_text']}"
    return [types.TextContent(type="text", text=text)]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    args = arguments or {}
    bearer_token = _request_bearer_token.get()
    if name == "list_open_interfaces":
        return _handle_list(args.get("keyword"))
    if name == "call_open_interface":
        return await _handle_call(args, bearer_token)
    return [types.TextContent(type="text", text=f"未知工具：{name}")]


def _build_session_manager() -> StreamableHTTPSessionManager:
    return StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


session_manager = _build_session_manager()


def reset_session_manager() -> StreamableHTTPSessionManager:
    """StreamableHTTPSessionManager 每个实例只能 run() 一次；lifespan 可能被
    多次进入（测试里每个 TestClient 都走一遍），所以每次启动都重建实例。"""
    global session_manager
    session_manager = _build_session_manager()
    return session_manager


async def handle_mcp(scope, receive, send, bearer_token: str | None = None):
    token = _request_bearer_token.set(bearer_token)
    try:
        await session_manager.handle_request(scope, receive, send)
    finally:
        _request_bearer_token.reset(token)


def validate_bearer_token(token: str) -> None:
    db = SessionLocal()
    try:
        try:
            payload = decode_token(token)
            subject = payload.get("sub")
            if not subject:
                raise HTTPException(status_code=401, detail="Invalid token")
            user = get_user_by_id(db, subject)
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid token")
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    finally:
        db.close()
