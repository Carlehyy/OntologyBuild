from __future__ import annotations

import httpx

from app.http_transport import loopback_httpx_mounts


def test_loopback_mounts_bypass_proxy_without_disabling_remote_proxy(
    monkeypatch,
) -> None:
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
            client._transport_for_url(httpx.URL("http://127.0.0.1:8888"))
            is direct_transport
        )
        assert (
            client._transport_for_url(httpx.URL("http://localhost:8888"))
            is direct_transport
        )
        assert (
            client._transport_for_url(httpx.URL("http://[::1]:8888"))
            is direct_transport
        )
        assert (
            client._transport_for_url(httpx.URL("https://api.example.test"))
            is not direct_transport
        )
