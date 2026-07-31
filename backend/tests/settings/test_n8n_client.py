import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.settings.workflows.n8n_client import N8nApiError, N8nClient


class _StubHttpClient:
    def __init__(self, response: httpx.Response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def request(self, *_args, **_kwargs):
        return self.response


def test_empty_gateway_error_keeps_readable_reason(monkeypatch):
    response = httpx.Response(
        502,
        content=b"",
        request=httpx.Request("GET", "http://n8n.example/api/v1/workflows"),
    )
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **_kwargs: _StubHttpClient(response),
    )

    client = N8nClient("http://n8n.example/api/v1", "test-key")
    with pytest.raises(N8nApiError) as caught:
        client.list_workflows(limit=1)

    assert caught.value.status_code == 502
    assert caught.value.message == "Bad Gateway"
    assert str(caught.value) == "HTTP 502: Bad Gateway"


def test_nested_json_error_message_is_extracted(monkeypatch):
    response = httpx.Response(
        400,
        json={"error": {"message": "workflow is invalid"}},
        request=httpx.Request("PUT", "http://n8n.example/api/v1/workflows/1"),
    )
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **_kwargs: _StubHttpClient(response),
    )

    client = N8nClient("http://n8n.example/api/v1", "test-key")
    with pytest.raises(N8nApiError) as caught:
        client.update_workflow("1", {"name": "test"})

    assert caught.value.message == "workflow is invalid"


def test_local_n8n_bypasses_proxy_without_disabling_environment(
    monkeypatch,
):
    response = httpx.Response(
        200,
        json={"data": []},
        request=httpx.Request("GET", "http://127.0.0.1:5678/api/v1/workflows"),
    )
    seen = {}

    def create_client(**kwargs):
        seen.update(kwargs)
        return _StubHttpClient(response)

    monkeypatch.setattr(httpx, "Client", create_client)

    client = N8nClient("http://127.0.0.1:5678", "test-key")
    assert client.list_workflows(limit=1) == []

    assert seen["mounts"]["all://127.0.0.1"] is None
    assert seen["mounts"]["all://localhost"] is None
    assert seen["mounts"]["all://[::1]"] is None
    assert "trust_env" not in seen


def test_local_n8n_api_key_never_reaches_configured_proxy(
    monkeypatch,
):
    target_requests = []
    proxy_requests = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            target_requests.append(
                {
                    "path": self.path,
                    "api_key": self.headers.get("X-N8N-API-KEY"),
                }
            )
            payload = json.dumps({"data": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            proxy_requests.append(dict(self.headers))
            self.send_response(502)
            self.end_headers()

        def log_message(self, *_args):
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    target_thread.start()
    proxy_thread.start()

    try:
        for key in (
            "ALL_PROXY",
            "all_proxy",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        ):
            monkeypatch.delenv(key, raising=False)
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        monkeypatch.setenv("HTTP_PROXY", proxy_url)
        monkeypatch.setenv("HTTPS_PROXY", proxy_url)
        monkeypatch.setenv("NO_PROXY", "")

        client = N8nClient(
            f"http://127.0.0.1:{target.server_port}",
            "must-stay-local",
        )
        assert client.list_workflows(limit=1) == []
    finally:
        target.shutdown()
        proxy.shutdown()
        target.server_close()
        proxy.server_close()
        target_thread.join(timeout=2)
        proxy_thread.join(timeout=2)

    assert target_requests == [
        {
            "path": "/api/v1/workflows?limit=1",
            "api_key": "must-stay-local",
        }
    ]
    assert proxy_requests == []
