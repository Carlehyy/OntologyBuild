import json
import socket
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

import pytest
import requests
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api_hub import config, db
from app.api_hub.routers import backup, http_proxy, interfaces


class EchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self):
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if parsed.path == "/status/418":
            payload = b"upstream teapot"
            self.send_response(418)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
            return
        if parsed.path == "/binary":
            payload = b"\x00\x01\xfe\xffAPI-HUB"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("X-Upstream", "binary")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
            return

        payload = json.dumps(
            {
                "method": self.command,
                "path": parsed.path,
                "query": parse_qsl(parsed.query, keep_blank_values=True),
                "headers": dict(self.headers.items()),
                "body": body.decode("utf-8", errors="replace"),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Upstream", "echo")
        self.send_header("Set-Cookie", "UPSTREAM_SECRET=must-not-leak")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle
    do_HEAD = _handle
    do_OPTIONS = _handle

    def log_message(self, _format, *_args):
        return


def test_http_proxy_migrates_existing_api_hub_database(tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy.db"
    with sqlite3.connect(legacy_path) as conn:
        conn.executescript(
            """
            CREATE TABLE interfaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '未命名接口',
                description TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                method TEXT NOT NULL DEFAULT 'GET',
                url TEXT NOT NULL DEFAULT '',
                query_params TEXT NOT NULL DEFAULT '[]',
                headers TEXT NOT NULL DEFAULT '[]',
                body_type TEXT NOT NULL DEFAULT 'none',
                body_content TEXT NOT NULL DEFAULT '',
                use_w3 INTEGER NOT NULL DEFAULT 1,
                mcp_enabled INTEGER NOT NULL DEFAULT 0,
                open_enabled INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interface_id INTEGER NOT NULL,
                ok INTEGER NOT NULL DEFAULT 0,
                status_code INTEGER,
                elapsed_ms INTEGER,
                request_snapshot TEXT,
                response_headers TEXT,
                response_body TEXT,
                error TEXT,
                relogin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
    monkeypatch.setattr(config, "DB_PATH", legacy_path)
    db.init_db()
    with db.get_conn() as conn:
        interface_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(interfaces)")
        }
        run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "http_enabled", "proxy_slug", "proxy_query_keys",
        "proxy_header_keys", "proxy_body_enabled",
    } <= interface_columns
    assert {"source", "proxy_key_id", "proxy_key_name", "source_ip"} <= run_columns
    assert {"proxy_keys", "proxy_key_interfaces"} <= tables


@pytest.fixture
def proxy_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "api_hub.db")
    monkeypatch.setattr(config, "SESSION_PATH", tmp_path / "w3_session.json")
    monkeypatch.setattr(config, "HTTP_TIMEOUT", 5)
    monkeypatch.setattr(config, "PROXY_MAX_REQUEST_BYTES", 1024 * 1024)
    db.init_db()

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()

    app = FastAPI()
    app.include_router(interfaces.router)
    app.include_router(interfaces.runs_router)
    app.include_router(backup.router)
    app.include_router(http_proxy.admin_router)
    app.include_router(http_proxy.public_router)
    with TestClient(app) as client:
        yield {
            "app": app,
            "client": client,
            "upstream_url": f"http://127.0.0.1:{upstream.server_port}",
        }

    upstream.shutdown()
    upstream.server_close()


def _interface(client: TestClient, upstream_url: str, **overrides):
    payload = {
        "name": "Echo",
        "description": "HTTP proxy integration target",
        "group_name": "代理测试",
        "method": "GET",
        "url": upstream_url + "/echo",
        "query_params": [
            {"key": "tenant", "value": "100"},
            {"key": "page", "value": "1"},
        ],
        "headers": [{"key": "X-Fixed", "value": "fixed"}],
        "body_type": "none",
        "body_content": "",
        "use_w3": False,
        "mcp_enabled": False,
        "open_enabled": False,
        "http_enabled": True,
        "proxy_slug": "echo",
        "proxy_query_keys": ["page", "tag"],
        "proxy_header_keys": ["Authorization", "X-Trace-ID"],
        "proxy_body_enabled": False,
    }
    payload.update(overrides)
    response = client.post("/interfaces", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _key(client: TestClient, interface_ids=None, scope_all=False, **overrides):
    payload = {
        "name": "测试调用方",
        "enabled": True,
        "scope_all": scope_all,
        "interface_ids": interface_ids or [],
        "valid_from": None,
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
    }
    payload.update(overrides)
    response = client.post("/proxy/keys", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_http_proxy_auth_scope_query_headers_response_and_audit(proxy_env):
    client = proxy_env["client"]
    iface = _interface(client, proxy_env["upstream_url"])
    key = _key(client, [iface["id"]])

    assert client.get("/proxy/echo").status_code == 401
    response = client.get(
        "/proxy/echo?page=3&tag=a&tag=b",
        headers={
            config.PROXY_KEY_HEADER: key["secret"],
            "Authorization": "Bearer upstream-token",
            "X-Trace-ID": "trace-1",
            "X-Not-Allowed": "ignored",
        },
    )
    assert response.status_code == 200, response.text
    echoed = response.json()
    assert echoed["query"] == [
        ["tenant", "100"],
        ["page", "3"],
        ["tag", "a"],
        ["tag", "b"],
    ]
    headers = {name.lower(): value for name, value in echoed["headers"].items()}
    assert headers["authorization"] == "Bearer upstream-token"
    assert headers["x-trace-id"] == "trace-1"
    assert headers["x-fixed"] == "fixed"
    assert config.PROXY_KEY_HEADER.lower() not in headers
    assert "x-not-allowed" not in headers
    assert response.headers["x-upstream"] == "echo"
    assert "set-cookie" in response.headers  # non-W3 upstream cookies are legitimate

    assert client.get(
        "/proxy/echo?internal=1",
        headers={config.PROXY_KEY_HEADER: key["secret"]},
    ).status_code == 400
    wrong_method = client.post(
        "/proxy/echo",
        headers={config.PROXY_KEY_HEADER: key["secret"]},
    )
    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "GET"

    other = _interface(client, proxy_env["upstream_url"], name="Other", proxy_slug="other")
    assert client.get(
        "/proxy/other",
        headers={config.PROXY_KEY_HEADER: key["secret"]},
    ).status_code == 403
    assert other["http_enabled"] is True

    with db.get_conn() as conn:
        run = conn.execute(
            "SELECT * FROM runs WHERE interface_id = ? ORDER BY id DESC",
            (iface["id"],),
        ).fetchone()
    assert run["source"] == "http_proxy"
    assert run["proxy_key_name"] == "测试调用方"
    assert run["source_ip"] == "testclient"
    assert key["secret"] not in run["request_snapshot"]
    assert "upstream-token" not in run["request_snapshot"]
    request_snapshot = json.loads(run["request_snapshot"])
    snapshot_headers = {
        item["key"].lower(): item["value"] for item in request_snapshot["headers"]
    }
    assert snapshot_headers["authorization"] == "***"

    listed = client.get("/proxy/keys").json()
    assert "secret" not in listed[0]
    assert "key_hash" not in listed[0]


def test_http_proxy_body_w3_cookie_status_and_binary_passthrough(proxy_env):
    client = proxy_env["client"]
    config.SESSION_PATH.write_text(
        json.dumps(
            {
                "acquired_at": datetime.now(timezone.utc).isoformat(),
                "cookies": [
                    {
                        "name": "W3_SESSION",
                        "value": "platform-cookie",
                        "domain": "127.0.0.1",
                        "path": "/",
                        "expires": (
                            datetime.now(timezone.utc) + timedelta(hours=1)
                        ).timestamp(),
                        "secure": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _interface(
        client,
        proxy_env["upstream_url"],
        name="Post Echo",
        method="POST",
        proxy_slug="post-echo",
        query_params=[],
        proxy_query_keys=[],
        headers=[],
        proxy_header_keys=["Cookie", "Authorization"],
        body_type="json",
        body_content='{"default":true}',
        proxy_body_enabled=True,
        use_w3=True,
    )
    _interface(
        client,
        proxy_env["upstream_url"],
        name="Teapot",
        method="GET",
        url=proxy_env["upstream_url"] + "/status/418",
        proxy_slug="teapot",
        query_params=[],
        proxy_query_keys=[],
    )
    _interface(
        client,
        proxy_env["upstream_url"],
        name="Binary",
        method="GET",
        url=proxy_env["upstream_url"] + "/binary",
        proxy_slug="binary",
        query_params=[],
        proxy_query_keys=[],
    )
    key = _key(client, scope_all=True)

    response = client.post(
        "/proxy/post-echo",
        headers={
            config.PROXY_KEY_HEADER: key["secret"],
            "Content-Type": "application/json",
            "Cookie": "theme=dark; W3_SESSION=caller-must-not-win",
            "Authorization": "Bearer business",
        },
        content=b'{"page":2,"password":"private"}',
    )
    assert response.status_code == 200, response.text
    echoed = response.json()
    headers = {name.lower(): value for name, value in echoed["headers"].items()}
    assert echoed["body"] == '{"page":2,"password":"private"}'
    assert headers["authorization"] == "Bearer business"
    assert "W3_SESSION=platform-cookie" in headers["cookie"]
    assert "theme=dark" in headers["cookie"]
    assert "caller-must-not-win" not in headers["cookie"]
    assert "set-cookie" not in response.headers

    status = client.get(
        "/proxy/teapot",
        headers={config.PROXY_KEY_HEADER: key["secret"]},
    )
    assert status.status_code == 418
    assert status.text == "upstream teapot"

    binary = client.get(
        "/proxy/binary",
        headers={config.PROXY_KEY_HEADER: key["secret"]},
    )
    assert binary.status_code == 200
    assert binary.content == b"\x00\x01\xfe\xffAPI-HUB"
    assert binary.headers["content-type"] == "application/octet-stream"

    with db.get_conn() as conn:
        snapshot_text = conn.execute(
            "SELECT request_snapshot FROM runs r JOIN interfaces i ON i.id=r.interface_id "
            "WHERE i.proxy_slug='post-echo' ORDER BY r.id DESC"
        ).fetchone()["request_snapshot"]
    snapshot = json.loads(snapshot_text)
    assert "private" not in snapshot_text
    assert json.loads(snapshot["body_content"])["password"] == "***"
    assert "platform-cookie" not in snapshot_text


def test_http_proxy_key_lifecycle_publication_validation_and_backup(proxy_env):
    client = proxy_env["client"]
    iface = _interface(client, proxy_env["upstream_url"])
    key = _key(client, [iface["id"]])

    update = {
        "name": key["name"],
        "enabled": False,
        "valid_from": key["valid_from"],
        "expires_at": key["expires_at"],
        "scope_all": key["scope_all"],
        "interface_ids": key["interface_ids"],
    }
    assert client.put(f"/proxy/keys/{key['id']}", json=update).status_code == 200
    assert client.get(
        "/proxy/echo",
        headers={config.PROXY_KEY_HEADER: key["secret"]},
    ).status_code == 401
    assert client.delete(f"/proxy/keys/{key['id']}").status_code == 200

    duplicate = _interface(
        client,
        proxy_env["upstream_url"],
        name="Draft duplicate",
        proxy_slug="echo",
        http_enabled=False,
    )
    conflict = client.put(
        f"/interfaces/{duplicate['id']}/http-publication",
        json={
            "enabled": True,
            "slug": "echo",
            "query_keys": [],
            "header_keys": [],
            "body_enabled": False,
        },
    )
    assert conflict.status_code == 409
    blocked_header = client.put(
        f"/interfaces/{duplicate['id']}/http-publication",
        json={
            "enabled": True,
            "slug": "safe-path",
            "query_keys": [],
            "header_keys": [config.PROXY_KEY_HEADER],
            "body_enabled": False,
        },
    )
    assert blocked_header.status_code == 400

    backup_response = client.post(
        "/backup/export",
        json={"name": "proxy-backup", "mode": "full", "ids": []},
    )
    assert backup_response.status_code == 200
    payload = backup_response.json()
    assert payload["version"] == 2
    assert "proxy_keys" not in payload
    assert key["secret"] not in backup_response.text
    exported = next(item for item in payload["interfaces"] if item["name"] == "Echo")
    assert exported["http_enabled"] is True
    assert exported["proxy_slug"] == "echo"
    assert exported["proxy_query_keys"] == ["page", "tag"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_http_proxy_real_tcp_end_to_end(proxy_env):
    """Real sockets on both hops: requests -> uvicorn proxy -> upstream HTTP server."""
    app = proxy_env["app"]
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    base = f"http://127.0.0.1:{port}"
    try:
        created = requests.post(
            base + "/interfaces",
            json={
                "name": "TCP E2E",
                "method": "GET",
                "url": proxy_env["upstream_url"] + "/echo",
                "query_params": [{"key": "tenant", "value": "live"}],
                "headers": [{"key": "X-Fixed", "value": "tcp"}],
                "use_w3": False,
                "http_enabled": True,
                "proxy_slug": "tcp-e2e",
                "proxy_query_keys": ["page"],
                "proxy_header_keys": ["X-Trace-ID"],
            },
            timeout=5,
        )
        created.raise_for_status()
        iface = created.json()
        key_response = requests.post(
            base + "/proxy/keys",
            json={
                "name": "TCP caller",
                "enabled": True,
                "scope_all": False,
                "interface_ids": [iface["id"]],
            },
            timeout=5,
        )
        key_response.raise_for_status()
        secret = key_response.json()["secret"]

        response = requests.get(
            base + "/proxy/tcp-e2e",
            params={"page": 7},
            headers={
                config.PROXY_KEY_HEADER: secret,
                "X-Trace-ID": "real-socket",
            },
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        assert payload["query"] == [["tenant", "live"], ["page", "7"]]
        headers = {name.lower(): value for name, value in payload["headers"].items()}
        assert headers["x-fixed"] == "tcp"
        assert headers["x-trace-id"] == "real-socket"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
