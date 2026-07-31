from __future__ import annotations

import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx

from app.shared.http_transport import (
    _LoopbackBypassProxyHandler,
    is_loopback_url,
    loopback_httpx_mounts,
    open_with_loopback_bypass,
)


def test_loopback_url_detection_covers_ipv4_ipv6_and_localhost() -> None:
    assert is_loopback_url("http://127.0.0.1:8000")
    assert is_loopback_url("http://127.0.0.25:8000")
    assert is_loopback_url("http://[::1]:8000")
    assert is_loopback_url("http://localhost.:8000")
    assert not is_loopback_url("http://192.168.1.10:8000")
    assert not is_loopback_url("https://api.example.test")


def test_httpx_mounts_keep_remote_proxy_enabled(monkeypatch) -> None:
    for key in (
        "ALL_PROXY",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")

    with httpx.Client(mounts=loopback_httpx_mounts()) as client:
        direct_transport = client._transport
        assert (
            client._transport_for_url(httpx.URL("http://127.0.0.1:8000"))
            is direct_transport
        )
        assert (
            client._transport_for_url(httpx.URL("https://api.example.test"))
            is not direct_transport
        )


def test_stdlib_proxy_handler_only_bypasses_loopback(monkeypatch) -> None:
    proxied = object()
    monkeypatch.setattr(
        urllib.request.ProxyHandler,
        "proxy_open",
        lambda *_args, **_kwargs: proxied,
    )
    handler = _LoopbackBypassProxyHandler({})

    assert (
        handler.proxy_open(
            SimpleNamespace(full_url="http://127.0.0.1:9222/json/version"),
            "http://proxy.example",
            "http",
        )
        is None
    )
    assert (
        handler.proxy_open(
            SimpleNamespace(full_url="https://api.example.test"),
            "http://proxy.example",
            "https",
        )
        is proxied
    )


def test_stdlib_open_uses_dedicated_opener_only_for_loopback(
    monkeypatch,
) -> None:
    calls: list[tuple[str, float]] = []
    local_response = object()
    remote_response = object()

    class Opener:
        def open(self, request, timeout):
            calls.append(("local", timeout))
            return local_response

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda _request, timeout: (
            calls.append(("remote", timeout)) or remote_response
        ),
    )

    local_request = urllib.request.Request("http://127.0.0.1:9222/json/version")
    remote_request = urllib.request.Request("https://api.example.test/health")

    assert open_with_loopback_bypass(local_request, timeout=1.5) is local_response
    assert open_with_loopback_bypass(remote_request, timeout=3.0) is remote_response
    assert calls == [("local", 1.5), ("remote", 3.0)]


def test_stdlib_loopback_request_does_not_reach_configured_proxy(
    monkeypatch,
) -> None:
    target_requests = []
    proxy_requests = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            target_requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "6")
            self.end_headers()
            self.wfile.write(b"target")

        def log_message(self, *_args):
            return

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            proxy_requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "5")
            self.end_headers()
            self.wfile.write(b"proxy")

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

        request = urllib.request.Request(
            f"http://127.0.0.1:{target.server_port}/health"
        )
        with open_with_loopback_bypass(request, timeout=2.0) as response:
            assert response.read() == b"target"
    finally:
        target.shutdown()
        proxy.shutdown()
        target.server_close()
        proxy.server_close()
        target_thread.join(timeout=2)
        proxy_thread.join(timeout=2)

    assert target_requests == ["/health"]
    assert proxy_requests == []
