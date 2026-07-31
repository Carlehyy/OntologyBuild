"""Proxy-safe transports for calls to services on the same machine."""
from __future__ import annotations

import ipaddress
import urllib.request
from typing import Any, Final
from urllib.parse import urlsplit


_LOOPBACK_PROXY_BYPASSES: Final[tuple[str, ...]] = (
    "all://127.0.0.1",
    "all://localhost",
    "all://[::1]",
)


def loopback_httpx_mounts(*target_urls: str) -> dict[str, None]:
    """Return fresh HTTPX mounts that bypass proxies only for loopback."""
    patterns = list(_LOOPBACK_PROXY_BYPASSES)
    for target_url in target_urls:
        if not is_loopback_url(target_url):
            continue
        host = urlsplit(target_url).hostname
        if not host:
            continue
        mount_host = f"[{host}]" if ":" in host else host
        patterns.append(f"all://{mount_host}")
    return dict.fromkeys(patterns)


def is_loopback_url(url: str) -> bool:
    """Return whether an absolute HTTP(S) URL targets a loopback host."""
    host = urlsplit(url).hostname
    if not host:
        return False
    normalized = host.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


class _LoopbackBypassProxyHandler(urllib.request.ProxyHandler):
    """Retain configured proxies except for an explicit loopback request."""

    def proxy_open(self, request: Any, proxy: str, proxy_type: str):
        if is_loopback_url(request.full_url):
            return None
        return super().proxy_open(request, proxy, proxy_type)


def open_with_loopback_bypass(request: Any, *, timeout: float):
    """Open a request directly on loopback and normally everywhere else.

    A dedicated opener is only created for loopback.  Its proxy handler still
    retains the system proxy map for a possible redirect to a non-loopback
    destination.
    """
    if is_loopback_url(request.full_url):
        opener = urllib.request.build_opener(_LoopbackBypassProxyHandler())
        return opener.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)
