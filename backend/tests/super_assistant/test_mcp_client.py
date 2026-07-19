import pytest

from app.shared.config import settings
from app.super_assistant.mcp_client import (
    McpClientError,
    _error_message,
    namespaced_tool_name,
    normalize_connection,
    validate_mcp_url,
)


def test_mcp_url_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_mcp_allowed_hosts", "localhost,*.example.com,10.0.0.0/8")
    assert validate_mcp_url("http://localhost:3000/mcp") == "http://localhost:3000/mcp"
    assert validate_mcp_url("https://tools.example.com/mcp") == "https://tools.example.com/mcp"
    assert validate_mcp_url("http://10.2.3.4/mcp") == "http://10.2.3.4/mcp"
    with pytest.raises(McpClientError, match="未进入"):
        validate_mcp_url("https://untrusted.invalid/mcp")
    with pytest.raises(McpClientError, match="不能内嵌"):
        validate_mcp_url("https://user:secret@tools.example.com/mcp")


def test_tool_namespace_is_stable_and_provider_safe():
    name = namespaced_tool_name("my server", "a.tool/with spaces")
    assert name == "mcp__my_server__a_tool_with_spaces"
    long_name = namespaced_tool_name("server" * 20, "tool" * 20)
    assert len(long_name) == 64
    assert long_name == namespaced_tool_name("server" * 20, "tool" * 20)


def test_anyio_exception_groups_are_unwrapped_for_users():
    error = ExceptionGroup("task group", [ConnectionError("connection refused")])
    assert _error_message(error) == "connection refused"


def test_normalizes_mcp_remote_wrapper_to_direct_streamable_http(monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_mcp_allowed_hosts", "38.76.215.169")
    assert normalize_connection(
        transport="stdio",
        command="npx",
        args=["-y", "mcp-remote", "http://38.76.215.169:8765/mcp"],
    ) == (
        "streamable_http",
        "http://38.76.215.169:8765/mcp",
        None,
        [],
    )


def test_validates_native_stdio_and_legacy_sse(monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_mcp_allowed_hosts", "localhost")
    assert normalize_connection(
        transport="stdio", command="npx", args=["-y", "@example/mcp-server"],
    ) == ("stdio", "", "npx", ["-y", "@example/mcp-server"])
    assert normalize_connection(
        transport="sse", url="http://localhost:3000/sse",
    ) == ("sse", "http://localhost:3000/sse", None, [])
