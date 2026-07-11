import asyncio
import json
import io
import time
import uuid
import zipfile
from types import SimpleNamespace

import pytest

from app.api_hub import config as api_hub_config, db as api_hub_db
from app.config import settings
from app.data_channel.steward import workspace
from app.data_channel.steward.browser_runtime import (
    BrowserManager, BrowserRuntimeError, _resolve_cdp_endpoint,
    _validate_navigation_response, analyze_pagination, browser_manager,
    probe_browser_cdp, public_capture, validate_target_url,
)
from app.data_channel.steward.toolkit import ToolRunner


@pytest.fixture
def steward_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "steward_workspace_root", str(tmp_path / "sessions"))
    return tmp_path


def test_workspace_isolates_sessions_and_archive_excludes_browser_secrets(steward_workspace):
    first = str(uuid.uuid4())
    second = str(uuid.uuid4())
    row = workspace.save_bytes(
        first, "需求说明.md", b"source: https://example.com/report\n", source="upload",
        mime_type="text/markdown",
    )

    assert [item["id"] for item in workspace.list_files(first)] == [row["id"]]
    assert workspace.list_files(second) == []
    assert row["urls"] == ["https://example.com/report"]
    with pytest.raises(workspace.WorkspaceError):
        workspace._within(first, "../../outside.txt")

    workspace.storage_state_path(first).write_text(
        json.dumps({"cookies": [{"value": "browser-secret"}]}), encoding="utf-8")
    workspace.append_capture(first, {
        "id": "capture", "requestHeaders": {"Authorization": "Bearer raw-secret"},
    })
    archive = workspace.archive_path(first)
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        combined = b"".join(zf.read(name) for name in names)
    assert names == ["files/需求说明.md", "manifest.json"]
    assert b"browser-secret" not in combined
    assert b"raw-secret" not in combined


def test_stream_upload_enforces_limit_without_leaving_partial_file(steward_workspace, monkeypatch):
    cid = str(uuid.uuid4())
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    with pytest.raises(workspace.WorkspaceError):
        workspace.save_stream(
            cid, "too-large.txt", io.BytesIO(b"x"), source="upload",
            mime_type="text/plain",
        )
    assert workspace.list_files(cid) == []
    assert list((workspace.session_root(cid) / "files").iterdir()) == []


def test_network_capture_redacts_auth_and_detects_page_offset_and_cursor():
    capture = {
        "id": "c1", "requestHeaders": {"Authorization": "Bearer secret", "Accept": "application/json"},
        "responseHeaders": {"Set-Cookie": "sid=secret"}, "responseBody": '{"ok":true}',
    }
    public = public_capture(capture)
    assert public["requestHeaders"]["Authorization"] == "••••••"
    assert public["responseHeaders"]["Set-Cookie"] == "••••••"
    assert "secret" not in json.dumps(public)

    page = analyze_pagination(
        "https://example.com/api/orders?page=2&pageSize=50",
        {"data": [], "total": 120, "hasNext": True},
    )
    assert page and page["mode"] == "page"
    assert page["requestParams"] == {"page": "2", "pagesize": "50"}
    offset = analyze_pagination("https://example.com/api/orders?offset=50&limit=50", [])
    assert offset and offset["mode"] == "offset"
    cursor = analyze_pagination("https://example.com/api/orders", {"nextCursor": "abc"})
    assert cursor and cursor["mode"] == "cursor"


def test_url_policy_blocks_loopback_and_metadata(monkeypatch):
    with pytest.raises(BrowserRuntimeError):
        validate_target_url("http://127.0.0.1/admin")
    with pytest.raises(BrowserRuntimeError):
        validate_target_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(BrowserRuntimeError):
        validate_target_url("https://user:password@example.com")

    monkeypatch.setattr(
        "app.data_channel.steward.browser_runtime.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("10.20.30.40", 443))],
    )
    monkeypatch.setattr(settings, "steward_browser_allow_private_networks", True)
    assert validate_target_url("https://intranet.example/path") == "https://intranet.example/path"
    monkeypatch.setattr(settings, "steward_browser_allow_private_networks", False)
    with pytest.raises(BrowserRuntimeError):
        validate_target_url("https://intranet.example/path")


def test_internal_cdp_hostname_resolves_to_ip_for_chromium_host_validation(monkeypatch):
    monkeypatch.setattr(
        "app.data_channel.steward.browser_runtime.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("172.19.0.10", 9222))],
    )
    assert _resolve_cdp_endpoint("http://browser:9222") == "http://172.19.0.10:9222"
    assert _resolve_cdp_endpoint("http://127.0.0.1:9222") == "http://127.0.0.1:9222"
    assert _resolve_cdp_endpoint("https://browser.example/cdp") == "https://browser.example/cdp"


def test_browser_cdp_probe_validates_websocket_endpoint(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps({
                "Browser": "Chrome/Test", "Protocol-Version": "1.3",
                "webSocketDebuggerUrl": "ws://172.19.0.10:9222/devtools/browser/test",
            }).encode()

    monkeypatch.setattr(settings, "steward_browser_cdp_url", "http://browser:9222")
    monkeypatch.setattr(
        "app.data_channel.steward.browser_runtime.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("172.19.0.10", 9222))],
    )
    monkeypatch.setattr(
        "app.data_channel.steward.browser_runtime.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(),
    )

    status = probe_browser_cdp()

    assert status == {
        "configured": True, "reachable": True,
        "browser": "Chrome/Test", "protocolVersion": "1.3",
    }


def test_navigation_rejects_waf_error_instead_of_reporting_blank_success():
    class Response:
        status = 567

    with pytest.raises(BrowserRuntimeError) as caught:
        _validate_navigation_response(Response(), "https://example.com/all")

    message = str(caught.value)
    assert "HTTP 567" in message
    assert "CDN/WAF" in message
    assert "实时浏览器" in message


def test_navigation_allows_success_and_download_without_response():
    class Response:
        status = 200

    _validate_navigation_response(Response(), "https://example.com/all")
    _validate_navigation_response(None, "https://example.com/download")


def test_register_proxy_interface_uses_capture_without_leaking_auth(
    steward_workspace, monkeypatch,
):
    cid = str(uuid.uuid4())
    monkeypatch.setattr(api_hub_config, "DB_PATH", steward_workspace / "api-hub.db")
    monkeypatch.setattr(api_hub_config, "SYSTEM_MCP_TOKEN", "system-token")
    api_hub_db.init_db()
    workspace.append_capture(cid, {
        "id": "cap-1", "method": "GET",
        "url": "https://service.example/api/orders?page=1&pageSize=20",
        "requestHeaders": {
            "Accept": "application/json", "Authorization": "Bearer captured-secret",
            "User-Agent": "not-copied",
        },
        "requestBody": None,
    })
    runner = ToolRunner(None, user_id="u1", conversation_id=cid)
    result = runner.run("register_proxy_interface", {
        "capture_id": "cap-1", "name": "订单接口", "include_auth": True,
    })

    assert "error" not in result
    assert result["interface"]["authCopied"] is True
    assert "captured-secret" not in json.dumps(result, ensure_ascii=False)
    with api_hub_db.get_conn() as conn:
        row = conn.execute("SELECT * FROM interfaces").fetchone()
    headers = json.loads(row["headers"])
    assert {item["key"] for item in headers} == {"Accept", "Authorization"}
    assert row["url"] == "https://service.example/api/orders"
    assert json.loads(row["query_params"])[0] == {"key": "page", "value": "1"}


def test_live_browser_ticket_is_single_use():
    cid = str(uuid.uuid4())
    ticket = browser_manager.issue_ticket(cid, "user-1")
    assert browser_manager.redeem_ticket(ticket, cid) == (True, "user-1")
    assert browser_manager.redeem_ticket(ticket, cid) == (False, None)


def _manager_for_lifecycle_tests(sessions, live=None):
    manager = BrowserManager.__new__(BrowserManager)
    manager._sessions = {session.conversation_id: session for session in sessions}
    manager._live_clients = live or {}
    closed = []

    async def close(conversation_id):
        closed.append(conversation_id)
        manager._sessions.pop(conversation_id, None)

    manager._close = close
    return manager, closed


def test_idle_browser_reaper_preserves_recent_and_live_sessions(monkeypatch):
    now = time.time()
    sessions = [
        SimpleNamespace(conversation_id="expired", user_id="u1", last_active=now - 90),
        SimpleNamespace(conversation_id="recent", user_id="u1", last_active=now - 10),
        SimpleNamespace(conversation_id="live", user_id="u2", last_active=now - 90),
    ]
    manager, closed = _manager_for_lifecycle_tests(sessions, {"live": 1})
    monkeypatch.setattr(settings, "steward_browser_idle_timeout_seconds", 30)

    reclaimed = asyncio.run(manager._reap_idle(now=now))

    assert reclaimed == ["expired"]
    assert closed == ["expired"]
    assert set(manager._sessions) == {"recent", "live"}


def test_browser_capacity_evicts_lru_for_global_and_user_limits(monkeypatch):
    now = time.time()
    sessions = [
        SimpleNamespace(conversation_id="u1-old", user_id="u1", last_active=now - 20),
        SimpleNamespace(conversation_id="u2-live", user_id="u2", last_active=now - 10),
    ]
    manager, closed = _manager_for_lifecycle_tests(sessions, {"u2-live": 1})
    monkeypatch.setattr(settings, "steward_browser_idle_timeout_seconds", 900)
    monkeypatch.setattr(settings, "steward_browser_max_sessions", 2)
    monkeypatch.setattr(settings, "steward_browser_max_sessions_per_user", 1)

    reclaimed = asyncio.run(manager._ensure_capacity("u1"))

    assert reclaimed == ["u1-old"]
    assert closed == ["u1-old"]
    assert set(manager._sessions) == {"u2-live"}


def test_live_browser_handoff_blocks_agent_but_allows_user_actions():
    manager, _ = _manager_for_lifecycle_tests([], {"conversation": 1})

    with pytest.raises(BrowserRuntimeError, match="手动接管"):
        manager._assert_actor_allowed("conversation", "agent")
    manager._assert_actor_allowed("conversation", "user")
