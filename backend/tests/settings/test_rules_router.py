"""Behavioral contracts for Settings Agent and workflow services."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.settings.agents.models import AgentConfig
from app.settings.agents.schemas import (
    AgentConfigUpdate,
    TestConnectionRequest as AgentTestConnectionRequest,
)
from app.settings.rules import (
    agent_config_service,
    router,
)


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _HttpxModule:
    class ConnectError(Exception):
        pass

    class TimeoutException(Exception):
        pass

    def __init__(self, get):
        self._get = get
        self.calls = []

    def Client(self, *, timeout):
        owner = self

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get(self, url, **kwargs):
                owner.calls.append((url, kwargs, timeout))
                return owner._get(url, **kwargs)

        return Client()


def test_single_row_helpers_keep_create_commit_refresh_order(
    db,
    monkeypatch,
):
    events = []
    original_commit = db.commit
    original_refresh = db.refresh

    def commit():
        events.append("commit")
        original_commit()

    def refresh(item):
        events.append(("refresh", type(item)))
        original_refresh(item)

    monkeypatch.setattr(db, "commit", commit)
    monkeypatch.setattr(db, "refresh", refresh)

    agent = agent_config_service._get_agent_config(db)

    assert agent.id == "default"
    assert events == [
        "commit",
        ("refresh", AgentConfig),
    ]


@pytest.mark.parametrize(
    ("password", "auth_enabled", "expected_cipher", "expected_token"),
    [
        ("new-password", True, "encrypted:new-password", ""),
        ("", False, "", ""),
        ("", True, "existing-cipher", "existing-token"),
    ],
)
def test_agent_update_preserves_password_branches(
    password,
    auth_enabled,
    expected_cipher,
    expected_token,
):
    cfg = SimpleNamespace(
        base_url="",
        auth_enabled=False,
        username="",
        password_encrypted="existing-cipher",
        token="existing-token",
        target_agent_id="",
        target_agent_name="",
    )
    db = SimpleNamespace(commits=0)
    db.commit = lambda: setattr(db, "commits", db.commits + 1)

    result = agent_config_service.update_agent_config(
        AgentConfigUpdate(
            base_url=" qwenpaw.local/ ",
            auth_enabled=auth_enabled,
            username=" user ",
            password=password,
            target_agent_id=" agent-1 ",
            target_agent_name=" Agent One ",
        ),
        db,
        get_agent_config_fn=lambda _db: cfg,
        encrypt_fn=lambda value: f"encrypted:{value}",
    )

    assert result == {"message": "Agent config updated"}
    assert cfg.base_url == "http://qwenpaw.local"
    assert cfg.username == "user"
    assert cfg.password_encrypted == expected_cipher
    assert cfg.token == expected_token
    assert cfg.target_agent_id == "agent-1"
    assert cfg.target_agent_name == "Agent One"
    assert db.commits == 1


def test_agent_connection_saves_only_after_connectivity_and_auth_success():
    http = _HttpxModule(
        lambda url, **_kwargs: _Response(
            payload={"enabled": True, "has_users": True},
        ),
    )
    cfg = SimpleNamespace(
        base_url="",
        auth_enabled=False,
        username="",
        password_encrypted="",
        token="cached",
    )
    db = SimpleNamespace(commits=0)
    db.commit = lambda: setattr(db, "commits", db.commits + 1)

    response = agent_config_service.test_agent_connection(
        AgentTestConnectionRequest(
            base_url="ignored",
            auth_enabled=True,
            username=" user ",
            password="secret",
        ),
        db,
        get_agent_config_fn=lambda _db: cfg,
        normalize_base_url_fn=lambda _raw: "http://patched",
        login_qwenpaw_fn=lambda *_args: "jwt",
        encrypt_fn=lambda value: f"encrypted:{value}",
        httpx_module=http,
    )

    assert http.calls[0][0] == "http://patched/api/auth/status"
    assert response.model_dump() == {
        "ok": True,
        "message": "连接成功，认证通过",
        "has_auth": True,
        "token_valid": True,
    }
    assert cfg.base_url == "http://patched"
    assert cfg.username == "user"
    assert cfg.password_encrypted == "encrypted:secret"
    assert cfg.token == ""
    assert db.commits == 1


def test_agent_auth_failure_returns_without_persisting():
    http = _HttpxModule(
        lambda _url, **_kwargs: _Response(payload={"enabled": True}),
    )
    db = SimpleNamespace(
        commit=lambda: pytest.fail("authentication failure must not commit"),
    )

    response = agent_config_service.test_agent_connection(
        AgentTestConnectionRequest(
            base_url="http://qwenpaw",
            auth_enabled=True,
            username="user",
            password="wrong",
        ),
        db,
        get_agent_config_fn=lambda _db: pytest.fail(
            "authentication failure must not create config",
        ),
        login_qwenpaw_fn=lambda *_args: None,
        httpx_module=http,
    )

    assert response.ok is True
    assert response.has_auth is True
    assert response.token_valid is False
    assert response.message == "QwenPaw 服务可连通，但认证失败：用户名或密码错误"


@pytest.mark.parametrize(
    ("error_factory", "message"),
    [
        (
            lambda module: module.ConnectError("offline"),
            "无法连接到 QwenPaw 服务，请检查地址是否正确",
        ),
        (
            lambda module: module.TimeoutException("slow"),
            "连接 QwenPaw 超时，请检查网络",
        ),
        (
            lambda _module: RuntimeError("broken"),
            "连接失败: broken",
        ),
    ],
)
def test_agent_connectivity_errors_remain_successful_http_payloads(
    error_factory,
    message,
):
    http = _HttpxModule(lambda _url, **_kwargs: (_ for _ in ()).throw(
        error_factory(http),
    ))
    response = agent_config_service.test_agent_connection(
        AgentTestConnectionRequest(base_url="http://qwenpaw"),
        SimpleNamespace(),
        httpx_module=http,
    )
    assert response.ok is False
    assert response.message == message


def test_fetch_agents_prefers_saved_password_and_forwards_bearer_token():
    http = _HttpxModule(
        lambda _url, **_kwargs: _Response(
            payload={
                "agents": [
                    {"id": "a1", "name": "Agent 1"},
                    {"id": "a2", "description": "second"},
                ],
            },
        ),
    )
    cfg = SimpleNamespace(
        password_encrypted="cipher",
        username="saved-user",
    )
    logins = []

    def login(api_base, username, password):
        logins.append((api_base, username, password))
        return "saved-token"

    response = agent_config_service.fetch_qwenpaw_agents(
        AgentTestConnectionRequest(
            base_url="http://qwenpaw",
            auth_enabled=True,
            username="request-user",
            password="request-password",
        ),
        SimpleNamespace(),
        get_agent_config_fn=lambda _db: cfg,
        login_qwenpaw_fn=login,
        decrypt_fn=lambda value: "saved-password",
        httpx_module=http,
    )

    assert logins == [
        ("http://qwenpaw/api", "saved-user", "saved-password"),
    ]
    assert http.calls[-1][1]["headers"] == {
        "Authorization": "Bearer saved-token",
    }
    assert [agent.model_dump() for agent in response.agents] == [
        {"id": "a1", "name": "Agent 1", "description": ""},
        {"id": "a2", "name": "a2", "description": "second"},
    ]


@pytest.mark.parametrize(
    ("get", "status_code", "detail"),
    [
        (
            lambda _module: (
                lambda _url, **_kwargs: _Response(status_code=401)
            ),
            502,
            "QwenPaw 认证失败，请检查用户名和密码",
        ),
        (
            lambda module: (
                lambda _url, **_kwargs: (_ for _ in ()).throw(
                    module.ConnectError("offline"),
                )
            ),
            502,
            "无法连接到 QwenPaw 服务",
        ),
        (
            lambda module: (
                lambda _url, **_kwargs: (_ for _ in ()).throw(
                    module.TimeoutException("slow"),
                )
            ),
            504,
            "连接 QwenPaw 超时",
        ),
        (
            lambda _module: (
                lambda _url, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("broken"),
                )
            ),
            502,
            "获取智能体列表失败: broken",
        ),
    ],
)
def test_fetch_agents_error_mapping_is_unchanged(
    get,
    status_code,
    detail,
):
    http = _HttpxModule(lambda *_args, **_kwargs: None)
    http._get = get(http)

    with pytest.raises(HTTPException) as raised:
        agent_config_service.fetch_qwenpaw_agents(
            AgentTestConnectionRequest(base_url="http://qwenpaw"),
            SimpleNamespace(),
            httpx_module=http,
        )

    assert raised.value.status_code == status_code
    assert raised.value.detail == detail
