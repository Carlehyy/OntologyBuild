import json
import socket
import sqlite3
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api_hub import (
    config,
    credential as credential_service,
    db,
    executor,
    mcp_contract,
    outbound_security,
)
from app.api_hub.outbound_security import (
    OutboundTargetError,
    request_with_safe_redirects,
    validate_outbound_url,
)
from app.api_hub.routers import backup, credential, http_proxy, interfaces, mcp, proxy


@pytest.fixture
def hub_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "api_hub.db")
    monkeypatch.setattr(config, "SESSION_PATH", tmp_path / "w3_session.json")
    monkeypatch.setattr(config, "SESSION_LOCK_PATH", tmp_path / "w3_session.lock")
    monkeypatch.setattr(config, "INTERNAL_PROXY_TOKEN", "internal-proxy-test-token")
    # Most unit tests use .example placeholders and replace the actual request
    # method.  Dedicated outbound-security tests enable the guard explicitly.
    monkeypatch.setattr(config, "OUTBOUND_BLOCK_PRIVATE_NETWORKS", False)
    monkeypatch.setattr(
        config,
        "W3_LOGIN_ALLOWED_HOSTS",
        ("login.huawei.com", "login.example"),
    )
    db.init_db()
    app = FastAPI()
    app.include_router(credential.router)
    app.include_router(interfaces.router)
    app.include_router(interfaces.runs_router)
    app.include_router(backup.router)
    app.include_router(mcp.router)
    app.include_router(proxy.router)
    app.include_router(proxy.internal_router)
    app.include_router(http_proxy.admin_router)
    app.include_router(http_proxy.public_router)
    return TestClient(app)


def _interface(**overrides):
    payload = {
        "name": "健康检查",
        "description": "检查上游服务是否可用",
        "group_name": "基础服务",
        "method": "GET",
        "url": "https://service.example/health",
        "query_params": [{"key": "verbose", "value": "1"}],
        "headers": [{"key": "X-Trace", "value": "test"}],
        "body_type": "none",
        "body_content": "",
        "use_w3": False,
        "mcp_enabled": True,
        "open_enabled": True,
    }
    payload.update(overrides)
    return payload


def test_interface_crud_group_and_auth_boundary(hub_client, client):
    created = hub_client.post("/interfaces", json=_interface())
    assert created.status_code == 200
    item = created.json()
    assert item["id"] > 0
    assert item["open_enabled"] is True

    listed = hub_client.get("/interfaces").json()
    assert [row["name"] for row in listed] == ["健康检查"]

    updated = hub_client.put(
        f"/interfaces/{item['id']}",
        json=_interface(name="上游健康检查", method="POST"),
    )
    assert updated.json()["method"] == "POST"

    moved = hub_client.post(
        "/interfaces/groups/delete", json={"group_name": "基础服务"}
    )
    assert moved.json() == {"ok": True, "count": 1}
    assert hub_client.get(f"/interfaces/{item['id']}").json()["group_name"] == ""

    # The host application restricts every management route to administrators.
    from app.main import app as platform_app
    from app.deps import get_current_user

    response = client.get("/api/api-hub/interfaces")
    assert response.status_code == 403
    assert client.get("/api/api-hub/proxy/info").status_code == 403
    try:
        platform_app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            role="viewer", is_active=True
        )
        assert client.get("/api/api-hub/interfaces").status_code == 403
        assert client.post("/api/api-hub/proxy/keys", json={}).status_code == 403

        platform_app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            role="admin", is_active=True
        )
        assert client.get("/api/api-hub/interfaces").status_code == 200
    finally:
        platform_app.dependency_overrides.pop(get_current_user, None)


def test_internal_n8n_proxy_validates_dynamic_parameters_and_revision(
    hub_client, monkeypatch,
):
    observed = {}

    def fake_request(session, method, url, **kwargs):
        observed.update({"method": method, "url": url, "kwargs": kwargs})
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps({"ok": True}).encode()
        response.encoding = "utf-8"
        return response

    monkeypatch.setattr(requests.Session, "request", fake_request)
    item = hub_client.post(
        "/interfaces",
        json=_interface(
            name="订单明细",
            method="POST",
            url="https://service.example/orders/{order_id}",
            open_enabled=False,
            body_type="json",
            body_content='{"include":false}',
            query_params=[{"key": "page", "value": "1"}],
            headers=[{"key": "X-Tenant", "value": "default"}],
            parameter_schema=[
                {"name": "order_id", "location": "path", "required": True},
                {"name": "page", "location": "query"},
                {"name": "X-Tenant", "location": "header"},
                {"name": "include", "location": "body", "value_type": "boolean"},
            ],
        ),
    ).json()
    assert item["config_revision"] == 1

    called = hub_client.post(
        f"/api-hub/internal/interfaces/{item['id']}/invoke",
        headers={"Authorization": "Bearer internal-proxy-test-token"},
        json={
            "interface_revision": 1,
            "path.order_id": "A/B",
            "query.page": 3,
            "headers.X-Tenant": "cn-01",
            "body.include": True,
        },
    )
    assert called.status_code == 200
    assert observed["method"] == "POST"
    assert observed["url"] == "https://service.example/orders/A%2FB"
    assert observed["kwargs"]["params"] == [("page", "3")]
    assert observed["kwargs"]["headers"]["X-Tenant"] == "cn-01"
    assert json.loads(observed["kwargs"]["data"]) == {"include": True}

    changed = hub_client.put(
        f"/interfaces/{item['id']}",
        json=_interface(
            name="订单明细 v2",
            method="POST",
            url="https://service.example/orders/{order_id}",
            body_type="json",
            parameter_schema=[
                {"name": "order_id", "location": "path", "required": True},
            ],
        ),
    ).json()
    assert changed["config_revision"] == 2
    stale = hub_client.post(
        f"/api-hub/internal/interfaces/{item['id']}/invoke",
        headers={"Authorization": "Bearer internal-proxy-test-token"},
        json={"interface_revision": 1, "path": {"order_id": "A100"}},
    )
    assert stale.status_code == 409
    assert "revision=1" in stale.json()["detail"]


def test_interface_move_reorders_within_and_across_groups(hub_client):
    first = hub_client.post(
        "/interfaces", json=_interface(name="A", group_name="一组")
    ).json()
    second = hub_client.post(
        "/interfaces", json=_interface(name="B", group_name="一组")
    ).json()
    hub_client.post(
        "/interfaces", json=_interface(name="C", group_name="二组")
    ).json()

    response = hub_client.put(
        f"/interfaces/{second['id']}/move",
        json={"group_name": "一组", "target_index": 0},
    )
    assert response.json() == {"ok": True}
    assert [
        item["name"]
        for item in hub_client.get("/interfaces").json()
        if item["group_name"] == "一组"
    ] == ["B", "A"]

    response = hub_client.put(
        f"/interfaces/{first['id']}/move",
        json={"group_name": "二组", "target_index": 0},
    )
    assert response.json() == {"ok": True}
    items = hub_client.get("/interfaces").json()
    assert [item["name"] for item in items if item["group_name"] == "一组"] == ["B"]
    assert [item["name"] for item in items if item["group_name"] == "二组"] == [
        "A",
        "C",
    ]


def test_admin_call_example_can_request_saved_cookie_header(hub_client):
    config.SESSION_PATH.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "W3_SESSION", "value": "admin-visible-session"},
                    {"name": "route", "value": "cn"},
                ]
            }
        ),
        encoding="utf-8",
    )
    cookie_header = hub_client.get("/credential/cookie-header")
    assert cookie_header.status_code == 200
    assert cookie_header.json() == {
        "cookie": "W3_SESSION=admin-visible-session; route=cn",
        "count": 2,
    }


def test_run_history_and_response_capture(hub_client, monkeypatch):
    item = hub_client.post("/interfaces", json=_interface(use_w3=True)).json()

    def fake_request(session, method, url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps({"ok": True, "method": method}).encode()
        response.encoding = "utf-8"
        return response

    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setattr(
        credential_service, "build_session_from_saved", lambda: requests.Session()
    )
    monkeypatch.setattr(credential_service, "saved_is_expired", lambda: False)
    result = hub_client.post(f"/interfaces/{item['id']}/run")
    assert result.status_code == 200
    assert result.json()["ok"] is True
    assert result.json()["status_code"] == 200

    history = hub_client.get("/runs", params={"keyword": "健康"}).json()
    assert history["total"] == 1
    summary = history["items"][0]
    detail = hub_client.get(
        f"/interfaces/{item['id']}/runs/{summary['id']}"
    ).json()
    assert detail["request_snapshot"]["url"] == item["url"]
    assert json.loads(detail["response_body"])["ok"] is True

    usage = hub_client.get("/credential/usage", params={"limit": 60}).json()
    assert usage["total"] == 1
    assert usage["success"] == 1
    assert usage["recent"][0]["interface_name"] == "健康检查"

    overview = hub_client.get("/runs/overview").json()
    assert overview["total_interfaces"] == 1
    assert overview["executed_interfaces"] == 1
    assert overview["unexecuted_interfaces"] == 0
    assert overview["today_traffic"] == 1
    assert overview["seven_day_traffic"] == 1
    assert overview["seven_day_success"] == 1
    assert overview["seven_day_failed"] == 0
    assert overview["success_rate"] == 100
    assert overview["p95_elapsed_ms"] is not None
    assert overview["slow_threshold_ms"] == 500
    assert sum(item["failed"] for item in overview["daily"]) == 0


def test_run_history_filters_failures_and_slow_calls(hub_client):
    item = hub_client.post("/interfaces", json=_interface()).json()
    created_at = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        conn.executemany(
            "INSERT INTO runs(interface_id, ok, status_code, elapsed_ms, "
            "request_snapshot, response_headers, response_body, error, relogin, "
            "created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                (item["id"], 1, 200, 120, "{}", "{}", "{}", None, 0, created_at),
                (item["id"], 0, 503, 680, "{}", "{}", "{}", "上游不可用", 0, created_at),
                (item["id"], 1, 200, 900, "{}", "{}", "{}", None, 0, created_at),
            ],
        )

    failed = hub_client.get("/runs", params={"result": "failed"}).json()
    assert failed["total"] == 1
    assert failed["items"][0]["status_code"] == 503

    slow = hub_client.get("/runs", params={"result": "slow"}).json()
    assert slow["total"] == 2
    assert {row["elapsed_ms"] for row in slow["items"]} == {680, 900}

    success = hub_client.get("/runs", params={"result": "success"}).json()
    assert success["total"] == 2

    overview = hub_client.get("/runs/overview").json()
    assert overview["seven_day_traffic"] == 3
    assert overview["seven_day_success"] == 2
    assert overview["seven_day_failed"] == 1
    assert overview["success_rate"] == 66.7
    assert overview["p95_elapsed_ms"] == 900
    assert sum(item["failed"] for item in overview["daily"]) == 1


def test_non_2xx_response_is_failure_in_history_and_credential_usage(
    hub_client, monkeypatch
):
    item = hub_client.post("/interfaces", json=_interface(use_w3=True)).json()

    def fake_request(session, method, url, **kwargs):
        response = requests.Response()
        response.status_code = 567
        response.url = url
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps({"error": "blocked"}).encode()
        response.encoding = "utf-8"
        return response

    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setattr(
        credential_service, "build_session_from_saved", lambda: requests.Session()
    )
    monkeypatch.setattr(credential_service, "saved_is_expired", lambda: False)

    response = hub_client.post(f"/interfaces/{item['id']}/run")
    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is False
    assert result["status_code"] == 567
    assert result["error"] == "上游返回 HTTP 567"
    assert json.loads(result["response_body"])["error"] == "blocked"

    history = hub_client.get("/runs").json()
    assert history["total"] == 1
    summary = history["items"][0]
    assert summary["ok"] == 0
    assert summary["status_code"] == 567
    assert summary["error"] == "上游返回 HTTP 567"

    detail = hub_client.get(
        f"/interfaces/{item['id']}/runs/{summary['id']}"
    ).json()
    assert detail["ok"] is False
    assert detail["error"] == "上游返回 HTTP 567"
    assert json.loads(detail["response_body"])["error"] == "blocked"

    usage = hub_client.get("/credential/usage", params={"limit": 60}).json()
    assert usage["total"] == 1
    assert usage["success"] == 0
    assert usage["failed"] == 1
    assert usage["success_rate"] == 0
    assert usage["recent"][0]["ok"] is False
    assert usage["recent"][0]["error"] == "上游返回 HTTP 567"


def test_n8n_proxy_is_fail_closed_and_forwards_dynamic_pagination(
    hub_client, monkeypatch
):
    item = hub_client.post("/interfaces", json=_interface(open_enabled=True)).json()
    monkeypatch.setattr(config, "SYSTEM_MCP_TOKEN", "proxy-token")
    observed = {}

    def fake_run(iface, overrides=None, **_kwargs):
        observed.update({
            "id": iface["id"],
            "query": dict(overrides.query_params or []),
            "body": overrides.body,
            "source": overrides.source,
        })
        return {
            "ok": True, "status_code": 200, "response_body": '{"rows":[1]}',
            "content_type": "application/json", "run_id": 42, "error": None,
        }

    monkeypatch.setattr("app.api_hub.routers.proxy.executor.run_interface", fake_run)
    unauthenticated = hub_client.post(
        f"/api-hub/proxy/{item['id']}", json={"query": {"page": 3}}
    )
    assert unauthenticated.status_code == 401

    response = hub_client.post(
        f"/api-hub/proxy/{item['id']}?tenant=cn",
        headers={"Authorization": "Bearer proxy-token"},
        json={"query": {"page": 3, "pageSize": 100}, "body": {"active": True}},
    )
    assert response.status_code == 200
    assert response.json() == {"rows": [1]}
    assert response.headers["x-api-hub-run-id"] == "42"
    assert observed == {
        "id": item["id"],
        "query": {"tenant": "cn", "page": "3", "pageSize": "100"},
        "body": '{"active": true}',
        "source": "n8n_proxy",
    }


def test_backup_round_trip_skips_duplicates(hub_client):
    item = hub_client.post(
        "/interfaces",
        json=_interface(
            url="https://service.example/health?token=url-secret",
            query_params=[
                {"key": "verbose", "value": "1"},
                {"key": "api_key", "value": "query-secret"},
            ],
            headers=[{"key": "Authorization", "value": "Bearer header-secret"}],
            body_type="json",
            body_content='{"password":"body-secret"}',
        ),
    ).json()
    exported = hub_client.post(
        "/backup/export",
        json={"name": "review-copy", "mode": "full", "ids": []},
    )
    assert exported.status_code == 200
    payload = exported.json()
    assert payload["app"] == "API-Hub"
    assert payload["interface_count"] == 1
    assert payload["includes_sensitive_values"] is False
    portable = payload["interfaces"][0]
    assert "url-secret" not in portable["url"]
    assert portable["query_params"][1]["value"] == ""
    assert portable["headers"] == []
    assert portable["body_content"] == ""
    assert portable["sensitive_values_omitted"] is True

    sensitive_export = hub_client.post(
        "/backup/export",
        json={
            "name": "review-copy-sensitive",
            "mode": "full",
            "ids": [],
            "include_sensitive": True,
        },
    ).json()
    assert sensitive_export["includes_sensitive_values"] is True
    sensitive_portable = sensitive_export["interfaces"][0]
    assert sensitive_portable["headers"][0]["value"] == "Bearer header-secret"
    assert "body-secret" in sensitive_portable["body_content"]

    duplicate = hub_client.post("/backup/import", json=payload).json()
    assert duplicate["imported"] == 0
    assert duplicate["skipped"] == 1

    assert hub_client.delete(f"/interfaces/{item['id']}").status_code == 200
    restored = hub_client.post("/backup/import", json=payload).json()
    assert restored["imported"] == 1
    restored_item = hub_client.get("/interfaces").json()[0]
    assert restored_item["name"] == "健康检查"
    assert restored_item["mcp_enabled"] is False
    assert restored_item["open_enabled"] is False
    assert restored_item["http_enabled"] is False


def test_online_credential_config_is_encrypted_and_password_is_never_returned(hub_client):
    response = hub_client.put(
        "/credential/config",
        json={
            "username": "w3-user",
            "password": "private-password",
            "login_url": "https://login.example/session",
        },
    )
    assert response.status_code == 200
    public = response.json()
    assert public == {
        "username": "w3-user",
        "password_configured": True,
        "login_url": "https://login.example/session",
        "source": "online",
    }
    assert "password" not in public
    stored = db.get_setting("w3_password_encrypted")
    assert stored and stored != "private-password"
    username, password, login_url = credential_service.runtime_credentials()
    assert (username, password, login_url) == (
        "w3-user", "private-password", "https://login.example/session"
    )

    fetched = hub_client.get("/credential/config").json()
    assert fetched == public

    retained_password_attack = hub_client.put(
        "/credential/config",
        json={
            "username": "another-user",
            "login_url": "https://login.example/other",
        },
    )
    assert retained_password_attack.status_code == 400
    assert "必须重新输入密码" in retained_password_attack.json()["detail"]

    malicious_login_url = hub_client.put(
        "/credential/config",
        json={
            "username": "w3-user",
            "password": "new-password",
            "login_url": "https://attacker.example/collect",
        },
    )
    assert malicious_login_url.status_code == 400
    assert "允许清单" in malicious_login_url.json()["detail"]


def test_w3_login_uses_native_redirects_and_keeps_sso_cookie_jar(monkeypatch):
    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"statusCode": 0}

    class FakeSession:
        def __init__(self):
            self.cookies = requests.cookies.RequestsCookieJar()
            self.max_redirects = 30
            self.post_call = None

        def post(self, url, **kwargs):
            self.post_call = (url, kwargs)
            self.cookies.set("JSESSIONID", "login-session", domain="login.example")
            self.cookies.set("hwssot", "sso-token", domain=".example")
            return FakeResponse()

    session = FakeSession()
    monkeypatch.setattr(credential_service, "_new_session", lambda: session)
    monkeypatch.setattr(config, "W3_LOGIN_ALLOWED_HOSTS", ("login.example",))
    monkeypatch.setattr(config, "OUTBOUND_MAX_REDIRECTS", 7)
    monkeypatch.setattr(config, "HTTP_TIMEOUT", 12)

    result = credential_service._login_with(
        "w3-user", "private-password", "https://login.example/session"
    )

    assert result is session
    assert session.max_redirects == 7
    assert session.cookies.get("hwssot", domain=".example") == "sso-token"
    assert session.post_call == (
        "https://login.example/session",
        {
            "json": {
                "loginAccount": "w3-user",
                "uid": "w3-user",
                "password": "private-password",
                "encryptedPasswordSwitch": "off",
            },
            "headers": {"Content-Type": "application/json; charset=UTF-8"},
            "allow_redirects": True,
            "timeout": 12,
        },
    )


def test_w3_session_validates_every_native_redirect(monkeypatch):
    monkeypatch.setattr(config, "W3_LOGIN_ALLOWED_HOSTS", ("login.example",))
    session = credential_service._W3Session()
    response = requests.Response()
    response.status_code = 302
    response.url = "https://login.example/session"

    response.headers["Location"] = "/sso/complete"
    assert session.get_redirect_target(response) == (
        "https://login.example/sso/complete"
    )

    response.headers["Location"] = "https://attacker.example/collect"
    with pytest.raises(
        requests.exceptions.InvalidURL, match="W3 登录重定向被拒绝"
    ):
        session.get_redirect_target(response)


def test_preview_run_uses_draft_without_saving_and_keeps_full_history(
    hub_client, monkeypatch
):
    item = hub_client.post("/interfaces", json=_interface(name="已保存名称")).json()
    observed = {}

    def fake_request(session, method, url, **kwargs):
        observed["verify"] = session.verify
        observed["method"] = method
        observed["url"] = url
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(
            {
                "ok": True,
                "nested": '{"password":"upstream-secret"}',
            }
        ).encode()
        response.encoding = "utf-8"
        return response

    monkeypatch.setattr(requests.Session, "request", fake_request)
    response = hub_client.post(
        "/interfaces/preview-run",
        json={
            **_interface(name="未保存草稿", method="OPTIONS"),
            "id": item["id"],
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert observed == {
        "verify": True,
        "method": "OPTIONS",
        "url": "https://service.example/health",
    }
    assert hub_client.get(f"/interfaces/{item['id']}").json()["name"] == "已保存名称"

    history = hub_client.get(f"/interfaces/{item['id']}/runs").json()
    detail = hub_client.get(
        f"/interfaces/{item['id']}/runs/{history[0]['id']}"
    ).json()
    assert "upstream-secret" in detail["response_body"]


def test_mcp_contract_merges_only_declared_runtime_fields():
    interface = {
        "id": 99,
        "name": "订单详情",
        "method": "POST",
        "url": "https://service.example/orders/{order_id}",
        "query_params": [{"key": "page", "value": "1"}],
        "headers": [
            {"key": "X-Tenant", "value": "default"},
            {"key": "Authorization", "value": "Bearer platform-secret"},
        ],
        "body_type": "json",
        "body_content": '{"include":false,"token":"platform-secret"}',
        "file_fields": [],
        "parameter_schema": [],
        "use_w3": True,
    }

    public = mcp_contract.public_parameters(interface)
    assert {item["name"] for item in public} >= {"order_id", "page", "X-Tenant", "/include"}
    assert all("token" not in item["name"].lower() for item in public)

    overrides = mcp_contract.request_overrides(
        interface,
        {
            "path": {"order_id": "A-1024"},
            "query": {"page": 2},
            "headers": {"X-Tenant": "tenant-a"},
            "body": {"include": True},
        },
    )
    assert overrides.path_params == [("order_id", "A-1024")]
    assert overrides.query_params == [("page", "2")]
    assert overrides.headers == [("X-Tenant", "tenant-a")]
    assert json.loads(overrides.body) == {
        "include": True,
        "token": "platform-secret",
    }

    with pytest.raises(mcp_contract.McpContractError, match="未在 MCP 契约中开放"):
        mcp_contract.request_overrides(
            interface,
            {"path": {"order_id": "A-1024"}, "query": {"debug": "1"}},
        )

    explicit = {
        **interface,
        "parameter_schema": [
            {"name": "page", "location": "query", "dynamic": True},
            {"name": "Authorization", "location": "header", "dynamic": True},
            {"name": "api_token", "location": "query", "dynamic": True},
        ],
    }
    assert mcp_contract.public_parameters(explicit) == [
        {
            "name": "page",
            "location": "query",
            "value_type": "string",
            "required": False,
            "description": "",
        }
    ]


def test_mcp_contract_preview_matches_runtime_mapping_without_secrets(hub_client):
    item = hub_client.post(
        "/interfaces",
        json=_interface(
            name="订单详情",
            method="POST",
            url="https://service.example/orders/{order_id}",
            query_params=[{"key": "page", "value": "1"}],
            headers=[
                {"key": "X-Tenant", "value": "default"},
                {"key": "Authorization", "value": "Bearer platform-secret"},
            ],
            body_type="json",
            body_content='{"include":false,"token":"platform-secret"}',
        ),
    ).json()

    response = hub_client.get(f"/interfaces/{item['id']}/mcp-contract")
    assert response.status_code == 200
    payload = response.json()
    assert payload["open_enabled"] is True
    names = {parameter["name"] for parameter in payload["parameters"]}
    assert {"order_id", "page", "X-Tenant", "/include"} <= names
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "platform-secret" not in serialized
    assert payload["call_example"] == {
        "interface_id": item["id"],
        "path": {"order_id": "<order_id>"},
        "query": {"page": "<page>"},
        "headers": {"X-Tenant": "<X-Tenant>"},
        "body": {"include": "<include>"},
    }


def test_single_worker_request_gate_fails_fast_when_saturated(monkeypatch):
    gate = threading.BoundedSemaphore(1)
    assert gate.acquire(blocking=False)
    monkeypatch.setattr(executor, "_REQUEST_GATE", gate)
    monkeypatch.setattr(config, "REQUEST_QUEUE_TIMEOUT", 0)

    result = executor.run_interface(
        {
            "id": None,
            "name": "过载保护",
            "method": "GET",
            "url": "https://service.example/health",
            "query_params": [],
            "headers": [],
            "body_type": "none",
            "body_content": "",
            "use_w3": False,
        }
    )
    assert result["error_type"] == "overloaded"
    assert result["error"] == "接口调用繁忙，请稍后重试"


def test_sqlite_audit_contention_does_not_fail_completed_upstream_call(monkeypatch):
    def fake_request(_session, _method, url, **_kwargs):
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "application/json"
        response._content = b'{"ok":true}'
        return response

    def busy_connection():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setattr(executor.db, "get_conn", busy_connection)
    monkeypatch.setattr(config, "OUTBOUND_BLOCK_PRIVATE_NETWORKS", False)
    result = executor.run_interface(
        {
            "id": 123,
            "name": "审计争用",
            "method": "GET",
            "url": "https://service.example/health",
            "query_params": [],
            "headers": [],
            "body_type": "none",
            "body_content": "",
            "use_w3": False,
        }
    )
    assert result["ok"] is True
    assert result["status_code"] == 200
    assert "run_id" not in result


def test_w3_calls_keep_global_and_w3_trusted_host_exceptions(monkeypatch):
    observed = {}

    def fake_request(_session, _method, url, **kwargs):
        observed["trusted_hosts"] = kwargs["trusted_hosts"]
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "application/json"
        response._content = b'{}'
        return response

    monkeypatch.setattr(config, "OUTBOUND_TRUSTED_HOSTS", ("intranet.example",))
    monkeypatch.setattr(config, "W3_OUTBOUND_TRUSTED_HOSTS", ("his.huawei.com",))
    monkeypatch.setattr(executor, "request_with_safe_redirects", fake_request)
    monkeypatch.setattr(
        credential_service, "build_session_from_saved", lambda: requests.Session()
    )
    monkeypatch.setattr(credential_service, "saved_is_expired", lambda: False)
    result = executor.run_interface(
        {
            "id": None,
            "name": "W3 内网服务",
            "method": "GET",
            "url": "https://his.huawei.com/msa/service",
            "query_params": [],
            "headers": [],
            "body_type": "none",
            "body_content": "",
            "use_w3": True,
        }
    )
    assert result["ok"] is True
    assert observed["trusted_hosts"] == ("intranet.example", "his.huawei.com")


def test_outbound_urls_block_private_targets_but_keep_w3_trusted_hosts(
    hub_client, monkeypatch
):
    monkeypatch.setattr(config, "OUTBOUND_BLOCK_PRIVATE_NETWORKS", True)
    monkeypatch.setattr(config, "OUTBOUND_TRUSTED_HOSTS", ())
    monkeypatch.setattr(config, "W3_OUTBOUND_TRUSTED_HOSTS", ("his.huawei.com",))

    def resolve(host, _port, **_kwargs):
        address = "10.8.0.12" if host in {"private.example", "his.huawei.com"} else "8.8.8.8"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    monkeypatch.setattr(outbound_security.socket, "getaddrinfo", resolve)
    assert validate_outbound_url("https://public.example/data").startswith("https://")
    with pytest.raises(OutboundTargetError, match="受保护的内网地址"):
        validate_outbound_url("https://private.example/data")
    assert validate_outbound_url(
        "https://his.huawei.com/msa/service",
        trusted_hosts=config.W3_OUTBOUND_TRUSTED_HOSTS,
    ).startswith("https://")
    assert validate_outbound_url(
        "http://127.0.0.1:8000/private",
        trusted_hosts=("127.0.0.1",),
    ).startswith("http://")
    with pytest.raises(OutboundTargetError):
        validate_outbound_url("file:///etc/passwd")
    with pytest.raises(OutboundTargetError):
        validate_outbound_url("https://user:password@public.example/data")

    from app.main import app as platform_app

    monkeypatch.setattr(config, "MCP_TOKEN", "")
    monkeypatch.setattr(config, "SYSTEM_MCP_TOKEN", "")
    client = TestClient(platform_app)
    assert client.post(
        config.MCP_PATH, headers={"Content-Type": "application/json"}, json={}
    ).status_code == 503
    assert client.post(
        config.SYSTEM_MCP_PATH,
        headers={"Content-Type": "application/json"},
        json={},
    ).status_code == 503

    assert "token" not in hub_client.get("/mcp/info").json()
    assert "token" not in hub_client.get("/mcp/system/info").json()


def test_outbound_redirect_target_is_revalidated(monkeypatch):
    monkeypatch.setattr(config, "OUTBOUND_BLOCK_PRIVATE_NETWORKS", True)
    monkeypatch.setattr(config, "OUTBOUND_TRUSTED_HOSTS", ())

    def resolve(host, _port, **_kwargs):
        address = "10.8.0.12" if host == "private.example" else "8.8.8.8"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    class RedirectSession:
        def request(self, *_args, **_kwargs):
            response = requests.Response()
            response.status_code = 302
            response.headers["Location"] = "https://private.example/internal"
            return response

    monkeypatch.setattr(outbound_security.socket, "getaddrinfo", resolve)
    with pytest.raises(OutboundTargetError, match="受保护的内网地址"):
        request_with_safe_redirects(
            RedirectSession(), "GET", "https://public.example/start"
        )


def test_cross_origin_redirect_drops_configured_credentials(monkeypatch):
    monkeypatch.setattr(config, "OUTBOUND_BLOCK_PRIVATE_NETWORKS", True)

    def resolve(_host, _port, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    class RedirectSession:
        def __init__(self):
            self.calls = []

        def request(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            response = requests.Response()
            response.status_code = 302 if len(self.calls) == 1 else 200
            if response.status_code == 302:
                response.headers["Location"] = "https://other.example/next"
            return response

    monkeypatch.setattr(outbound_security.socket, "getaddrinfo", resolve)
    session = RedirectSession()
    request_with_safe_redirects(
        session,
        "GET",
        "https://public.example/start",
        headers={
            "Authorization": "Bearer platform-token",
            "X-Api-Key": "platform-key",
            "X-Tenant": "tenant-a",
        },
        cookies={"caller": "cookie-value"},
    )
    second_kwargs = session.calls[1][1]
    assert second_kwargs["headers"] == {"X-Tenant": "tenant-a"}
    assert "cookies" not in second_kwargs
