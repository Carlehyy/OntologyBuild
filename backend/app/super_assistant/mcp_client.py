from __future__ import annotations

import ipaddress
import json
import re
from typing import Any
from urllib.parse import urlsplit

from app.shared.config import settings
from app.shared.encryption import decrypt, encrypt


class McpClientError(ValueError):
    pass


def _error_message(exc: BaseException) -> str:
    """Unwrap AnyIO task groups so connection failures stay actionable."""
    if isinstance(exc, BaseExceptionGroup):
        messages = [_error_message(child) for child in exc.exceptions]
        messages = [message for message in messages if message]
        return "; ".join(dict.fromkeys(messages)) or exc.__class__.__name__
    return str(exc).strip() or exc.__class__.__name__


def _allowed_entries() -> list[str]:
    return [item.strip().lower().rstrip(".") for item in settings.super_assistant_mcp_allowed_hosts.split(",") if item.strip()]


def validate_mcp_url(url: str) -> str:
    value = (url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise McpClientError("MCP URL 必须是 HTTP/HTTPS 绝对地址")
    if parsed.username or parsed.password:
        raise McpClientError("MCP URL 不能内嵌账号或密码")
    try:
        parsed.port
    except ValueError as exc:
        raise McpClientError("MCP URL 端口无效") from exc
    hostname = parsed.hostname.lower().rstrip(".")
    for allowed in _allowed_entries():
        if allowed.startswith("*.") and hostname.endswith(allowed[1:]) and hostname != allowed[2:]:
            return value
        try:
            network = ipaddress.ip_network(allowed, strict=False)
            try:
                if ipaddress.ip_address(hostname) in network:
                    return value
            except ValueError:
                pass
        except ValueError:
            if hostname == allowed:
                return value
    raise McpClientError(
        f"MCP 目标 {hostname} 未进入 SUPER_ASSISTANT_MCP_ALLOWED_HOSTS"
    )


def encrypt_headers(headers: dict[str, str]) -> tuple[str | None, list[str]]:
    if not headers:
        return None, []
    payload = json.dumps(headers, ensure_ascii=False, sort_keys=True)
    return encrypt(payload), sorted(headers.keys(), key=str.lower)


def decrypt_headers(ciphertext: str | None) -> dict[str, str]:
    if not ciphertext:
        return {}
    try:
        value = json.loads(decrypt(ciphertext))
    except Exception as exc:  # cryptography/json errors share a safe public message
        raise McpClientError("MCP 请求头无法解密，请重新保存配置") from exc
    if not isinstance(value, dict):
        raise McpClientError("MCP 请求头配置格式无效")
    return {str(key): str(item) for key, item in value.items()}


def namespaced_tool_name(server_name: str, tool_name: str) -> str:
    import hashlib

    server = re.sub(r"[^a-zA-Z0-9_-]", "_", server_name)
    tool = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_name)
    raw = f"mcp__{server}__{tool}"
    if len(raw) <= 64:
        return raw
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"{raw[:55]}_{digest}"


def _tool_manifest_item(tool: Any) -> dict[str, Any]:
    if hasattr(tool, "model_dump"):
        raw = tool.model_dump(by_alias=True, exclude_none=True)
    else:
        raw = {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", "") or "",
            "inputSchema": getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {},
        }
    schema = raw.get("inputSchema") or raw.get("input_schema") or {"type": "object", "properties": {}}
    return {
        "name": str(raw.get("name") or ""),
        "description": str(raw.get("description") or ""),
        "input_schema": schema,
    }


async def discover_tools(url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    valid_url = validate_mcp_url(url)
    try:
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:
        raise McpClientError("Python MCP SDK 未安装，请通过 uv 同步后端依赖") from exc

    try:
        timeout = httpx.Timeout(connect=20, read=30, write=20, pool=20)
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=False) as http_client:
            async with streamable_http_client(
                valid_url,
                http_client=http_client,
                terminate_on_close=True,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    return [_tool_manifest_item(tool) for tool in result.tools]
    except McpClientError:
        raise
    except Exception as exc:
        raise McpClientError(f"无法连接 MCP Server: {_error_message(exc)}") from exc


async def call_tool(url: str, headers: dict[str, str], tool_name: str,
                    arguments: dict[str, Any]) -> str:
    valid_url = validate_mcp_url(url)
    try:
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:
        raise McpClientError("Python MCP SDK 未安装，请通过 uv 同步后端依赖") from exc

    try:
        timeout = httpx.Timeout(connect=20, read=120, write=60, pool=20)
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=False) as http_client:
            async with streamable_http_client(
                valid_url,
                http_client=http_client,
                terminate_on_close=True,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
                    if hasattr(result, "model_dump_json"):
                        return result.model_dump_json(by_alias=True, exclude_none=True)
                    return json.dumps(result, ensure_ascii=False, default=str)
    except McpClientError:
        raise
    except Exception as exc:
        raise McpClientError(f"MCP 工具 {tool_name} 执行失败: {_error_message(exc)}") from exc
