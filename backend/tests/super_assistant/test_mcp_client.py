import pytest

from app.shared.config import settings
from app.super_assistant.mcp_client import McpClientError, _error_message, namespaced_tool_name, validate_mcp_url


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
