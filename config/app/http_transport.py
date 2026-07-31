"""HTTP transport policy for the local configuration center.

HTTPX trusts the operating-system proxy configuration by default.  On Windows,
Python may obtain that proxy from the registry even when the usual proxy
environment variables are empty.  Explicit ``None`` mounts keep loopback
traffic on the machine while leaving every non-loopback destination on
HTTPX's normal environment-aware transport.
"""
from __future__ import annotations

import ipaddress
from typing import Final
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
        host = urlsplit(target_url).hostname
        if not host:
            continue
        normalized = host.rstrip(".").lower()
        try:
            is_loopback = (
                normalized == "localhost"
                or ipaddress.ip_address(normalized).is_loopback
            )
        except ValueError:
            is_loopback = normalized == "localhost"
        if not is_loopback:
            continue
        mount_host = f"[{host}]" if ":" in host else host
        patterns.append(f"all://{mount_host}")
    return dict.fromkeys(patterns)
