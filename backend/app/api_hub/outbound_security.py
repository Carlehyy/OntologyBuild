"""Outbound URL validation and redirect handling for API-Hub."""
from __future__ import annotations

import ipaddress
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import requests

from . import config


class OutboundTargetError(ValueError):
    pass


def _text_host_allowed(hostname: str) -> bool:
    hostname = hostname.lower().rstrip(".")
    for raw in config.OUTBOUND_ALLOWED_HOSTS:
        allowed = raw.lower().strip().rstrip(".")
        if not allowed:
            continue
        if allowed.startswith("*.") and hostname.endswith(allowed[1:]):
            return True
        try:
            ipaddress.ip_network(allowed, strict=False)
        except ValueError:
            if hostname == allowed:
                return True
    return False


def _ip_allowed(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    for raw in config.OUTBOUND_ALLOWED_HOSTS:
        try:
            if address in ipaddress.ip_network(raw.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def validate_outbound_url(url: str) -> str:
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

    hostname = parsed.hostname.lower().rstrip(".")
    if _text_host_allowed(hostname):
        return value

    try:
        address = ipaddress.ip_address(hostname)
        if _ip_allowed(address):
            return value
    except ValueError:
        pass

    raise OutboundTargetError(
        f"接口目标未进入 API_HUB_OUTBOUND_ALLOWED_HOSTS：{hostname}"
    )


def request_with_safe_redirects(
    session: requests.Session,
    method: str,
    url: str,
    *,
    validator: Callable[[str], str] = validate_outbound_url,
    **kwargs,
) -> requests.Response:
    current_url = validator(url)
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
            response.close()
            raise requests.TooManyRedirects(
                f"重定向次数超过上限 {config.OUTBOUND_MAX_REDIRECTS}"
            )

        next_url = validator(urljoin(current_url, location))
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
        response.close()
        current_url = next_url

    raise requests.TooManyRedirects("重定向次数超过安全上限")
