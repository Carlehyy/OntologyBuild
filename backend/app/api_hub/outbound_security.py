"""Outbound URL validation and redirect handling for API-Hub.

Interface targets are intentionally not restricted by a deployment-level host
allowlist.  Registered interfaces may call any valid HTTP/HTTPS endpoint.
"""
from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import requests

from . import config


class OutboundTargetError(ValueError):
    pass


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

    return value


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
