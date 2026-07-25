"""Outbound URL validation and redirect handling for API-Hub.

Interface targets are intentionally not restricted by a deployment-level host
allowlist.  Registered interfaces may call any valid HTTP/HTTPS endpoint.
"""
from __future__ import annotations

from collections.abc import Callable
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests

from . import config, publication


class OutboundTargetError(ValueError):
    pass


def _close_response(response: requests.Response) -> None:
    """Best-effort close for real and lightweight adapter responses.

    ``requests.Response.close`` expects a ``raw`` stream while it still has
    unread content.  Production responses always provide one, but test doubles
    and small adapter responses may deliberately only carry an in-memory body.
    Redirect handling must not turn that harmless implementation detail into an
    application failure.
    """
    try:
        response.close()
    except AttributeError:
        pass


def validate_outbound_url(
    url: str,
    *,
    trusted_hosts: tuple[str, ...] = (),
) -> str:
    value = (url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise OutboundTargetError("接口 URL 必须是 HTTP/HTTPS 绝对地址")
    if parsed.username or parsed.password:
        raise OutboundTargetError("接口 URL 不能内嵌账号或密码")
    try:
        parsed.port
    except ValueError as exc:
        raise OutboundTargetError("接口 URL 端口无效") from exc

    hostname = (parsed.hostname or "").rstrip(".").lower()
    if config.OUTBOUND_BLOCK_PRIVATE_NETWORKS and not _is_trusted_host(
        hostname, trusted_hosts
    ) and not _is_trusted_host(hostname, config.OUTBOUND_TRUSTED_HOSTS):
        _reject_private_target(hostname, parsed.port)

    return value


def _is_trusted_host(hostname: str, trusted_hosts: tuple[str, ...]) -> bool:
    for raw in trusted_hosts:
        candidate = str(raw or "").strip().rstrip(".").lower()
        if not candidate:
            continue
        if candidate.startswith("*."):
            if hostname.endswith("." + candidate[2:]):
                return True
        elif hostname == candidate:
            return True
    return False


def _reject_private_target(hostname: str, port: int | None) -> None:
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = {literal}
    except ValueError:
        try:
            infos = socket.getaddrinfo(
                hostname,
                port or 443,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise OutboundTargetError("无法解析接口 URL 的目标主机") from exc
        addresses = {
            ipaddress.ip_address(info[4][0])
            for info in infos
        }
    blocked = sorted(
        str(address)
        for address in addresses
        if _is_private_or_special(address)
    )
    if blocked:
        raise OutboundTargetError(
            "接口 URL 指向受保护的内网地址：" + ", ".join(blocked)
        )


def _is_private_or_special(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def request_with_safe_redirects(
    session: requests.Session,
    method: str,
    url: str,
    *,
    validator: Callable[[str], str] = validate_outbound_url,
    trusted_hosts: tuple[str, ...] = (),
    **kwargs,
) -> requests.Response:
    def validate(target: str) -> str:
        if validator is validate_outbound_url:
            return validator(target, trusted_hosts=trusted_hosts)
        return validator(target)

    current_url = validate(url)
    current_method = method.upper()
    request_kwargs = dict(kwargs)
    request_kwargs["allow_redirects"] = False

    for redirect_count in range(config.OUTBOUND_MAX_REDIRECTS + 1):
        response = session.request(current_method, current_url, **request_kwargs)
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("Location")
        if not location:
            return response
        if redirect_count >= config.OUTBOUND_MAX_REDIRECTS:
            _close_response(response)
            raise requests.TooManyRedirects(
                f"重定向次数超过上限 {config.OUTBOUND_MAX_REDIRECTS}"
            )

        # Validate every redirect target again.  This performs a fresh DNS
        # lookup, closing the common redirect-to-private-network SSRF path.
        try:
            next_url = validate(urljoin(current_url, location))
            if not _same_origin(current_url, next_url):
                # ``requests`` removes credentials when it follows a cross-origin
                # redirect.  We follow redirects manually so retain that boundary:
                # do not send a configured token, caller cookie, or proxy key to a
                # different host.  W3's session-cookie jar remains domain scoped.
                _drop_cross_origin_credentials(request_kwargs)
            request_kwargs.pop("params", None)
            if response.status_code == 303 or (
                response.status_code in {301, 302}
                and current_method not in {"GET", "HEAD"}
            ):
                current_method = "GET"
                for key in ("data", "json", "files"):
                    request_kwargs.pop(key, None)
                headers = dict(request_kwargs.get("headers") or {})
                for key in list(headers):
                    if key.lower() in {"content-type", "content-length"}:
                        headers.pop(key, None)
                request_kwargs["headers"] = headers or None
        finally:
            # Also release the original response when the redirected target is
            # rejected by the DNS/private-network safety check.
            _close_response(response)
        current_url = next_url

    raise requests.TooManyRedirects("重定向次数超过安全上限")


def _same_origin(left: str, right: str) -> bool:
    a = urlsplit(left)
    b = urlsplit(right)
    return (
        a.scheme.lower(),
        (a.hostname or "").rstrip(".").lower(),
        a.port,
    ) == (
        b.scheme.lower(),
        (b.hostname or "").rstrip(".").lower(),
        b.port,
    )


def _drop_cross_origin_credentials(request_kwargs: dict) -> None:
    headers = dict(request_kwargs.get("headers") or {})
    for key in list(headers):
        lowered = key.lower()
        if (
            lowered in {"authorization", "proxy-authorization", "cookie", "x-api-hub-key"}
            or publication.is_sensitive_name(key)
        ):
            headers.pop(key, None)
    request_kwargs["headers"] = headers or None
    request_kwargs.pop("cookies", None)
