"""Synchronous, safe outbound delivery for formal-action webhook rules.

The graph editor stores a webhook rule as JSON.  This module is the only
runtime that turns that definition into an HTTP request: templates are
resolved, the JSON body is validated, redirects are revalidated and every
attempt carries the same idempotency key.  It deliberately has no dependency
on the API-Hub database or credentials; API-Hub's URL/redirect guard is reused
so both outbound surfaces enforce the same target policy.
"""
from __future__ import annotations

import ipaddress
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import requests

from app.api_hub import config as api_hub_config
from app.api_hub.outbound_security import (
    OutboundTargetError,
    request_with_safe_redirects,
    validate_outbound_url,
)
from app.config import settings


class WebhookDispatchError(ValueError):
    """A user-visible configuration or delivery failure for one webhook."""

    def __init__(
        self,
        message: str,
        *,
        safe_url: str | None = None,
        method: str | None = None,
        idempotency_key: str | None = None,
        attempts: int = 0,
        delivery_uncertain: bool = False,
    ) -> None:
        super().__init__(message)
        self.safe_url = safe_url
        self.method = method
        self.idempotency_key = idempotency_key
        self.attempts = attempts
        self.delivery_uncertain = delivery_uncertain


_TEMPLATE_RE = re.compile(r"\{\{?\s*(params?|object)\.(\w+)\s*\}?\}")
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_FORBIDDEN_HEADERS = {
    "host", "content-length", "connection", "keep-alive", "transfer-encoding",
    "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade",
    # The server owns retry identity.  A static, user-supplied value would
    # accidentally deduplicate unrelated action executions at the receiver.
    "idempotency-key",
}


def _display_url(url: str) -> str:
    """Keep secrets in URL query strings out of action logs/errors."""
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"


def _trusted_preview_host(hostname: str) -> bool:
    """Pure string check for deployment hosts explicitly exempted from SSRF."""
    for raw in api_hub_config.OUTBOUND_TRUSTED_HOSTS:
        candidate = str(raw or "").strip().rstrip(".").lower()
        if not candidate:
            continue
        if candidate.startswith("*."):
            if hostname.endswith("." + candidate[2:]):
                return True
        elif hostname == candidate:
            return True
    return False


def _validate_preview_url(url: str) -> str:
    """Validate everything possible without DNS or any other network access.

    Isolated ontology trials must not contact downstream infrastructure, which
    includes resolver traffic. Hostname resolution and the final SSRF decision
    are repeated by ``dispatch_webhook`` immediately before real delivery.
    Literal private addresses can still be rejected here without I/O.
    """
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
    if (
        api_hub_config.OUTBOUND_BLOCK_PRIVATE_NETWORKS
        and not _trusted_preview_host(hostname)
    ):
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            # Hostname DNS/SSRF validation is intentionally deferred.
            pass
        else:
            if _is_private_literal(address):
                raise OutboundTargetError(
                    f"接口 URL 指向受保护的内网地址：{address}")
    return value


def _is_private_literal(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Mirror the shared outbound guard for literal IPs without DNS."""
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _inside_json_string(text: str, position: int) -> bool:
    """Return whether ``position`` occurs inside a JSON string literal.

    The editor's JSON template examples place a token either as a complete
    JSON value or in a string.  Counting unescaped quote delimiters lets both
    forms safely retain their native type after substitution.
    """
    in_string = False
    escaped = False
    for char in text[:position]:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == '"':
            in_string = not in_string
    return in_string


def _resolve_template_value(namespace: str, key: str, params: Mapping[str, Any],
                            object_props: Mapping[str, Any]) -> Any:
    source = params if namespace in {"param", "params"} else object_props
    if key not in source:
        readable = "参数" if namespace in {"param", "params"} else "对象属性"
        raise WebhookDispatchError(f"Webhook 模板引用的{readable}不存在: {key}")
    return source[key]


def _render_json_body(template: str, params: Mapping[str, Any],
                      object_props: Mapping[str, Any], max_bytes: int) -> bytes | None:
    if not template or not template.strip():
        return None
    if len(template.encode("utf-8")) > max_bytes:
        raise WebhookDispatchError(f"Webhook 请求体超过 {max_bytes} bytes 限制")

    def replace(match: re.Match[str]) -> str:
        value = _resolve_template_value(match.group(1), match.group(2), params, object_props)
        try:
            if _inside_json_string(template, match.start()):
                # A token inside a quoted string must be escaped as string
                # content; an unquoted token keeps JSON's number/bool/object
                # type.  This covers both editor examples without eval().
                if isinstance(value, str):
                    return json.dumps(value, ensure_ascii=False)[1:-1]
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                return json.dumps(text, ensure_ascii=False)[1:-1]
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise WebhookDispatchError(
                f"Webhook 模板值无法序列化为 JSON: {match.group(0)}") from exc

    rendered = _TEMPLATE_RE.sub(replace, template)
    try:
        # Parse and re-encode so malformed JSON never reaches a downstream
        # service, even when a configured Content-Type is misleading.
        body = json.loads(rendered)
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except json.JSONDecodeError as exc:
        raise WebhookDispatchError(
            f"Webhook 请求体模板不是有效 JSON: 第 {exc.lineno} 行第 {exc.colno} 列") from exc
    if len(encoded) > max_bytes:
        raise WebhookDispatchError(f"Webhook 渲染后的请求体超过 {max_bytes} bytes 限制")
    return encoded


def _build_headers(raw: Any, idempotency_key: str, has_body: bool) -> dict[str, str]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise WebhookDispatchError("Webhook headers 必须是键值对象")
    if len(raw) > 50:
        raise WebhookDispatchError("Webhook headers 不能超过 50 项")

    headers: dict[str, str] = {}
    seen: set[str] = set()
    for key, value in raw.items():
        name = str(key).strip()
        lowered = name.lower()
        if not name or not _HEADER_NAME_RE.fullmatch(name):
            raise WebhookDispatchError(f"Webhook Header 名称无效: {name or '(空)'}")
        if lowered in seen:
            raise WebhookDispatchError(f"Webhook Header 重复: {name}")
        if lowered in _FORBIDDEN_HEADERS:
            raise WebhookDispatchError(f"Webhook Header 不允许由规则设置: {name}")
        rendered = str(value if value is not None else "")
        if "\r" in rendered or "\n" in rendered:
            raise WebhookDispatchError(f"Webhook Header 值不能包含换行: {name}")
        if len(rendered) > 100_000:
            raise WebhookDispatchError(f"Webhook Header 值过长: {name}")
        headers[name] = rendered
        seen.add(lowered)

    if has_body and "content-type" not in seen:
        headers["Content-Type"] = "application/json; charset=utf-8"
    headers["Idempotency-Key"] = idempotency_key
    return headers


def _prepare_webhook(config: Mapping[str, Any], *, params: Mapping[str, Any],
                     object_props: Mapping[str, Any] | None,
                     idempotency_key: str | None = None,
                     resolve_target: bool = True) -> dict[str, Any]:
    """Resolve and validate a webhook without sending it."""
    if not isinstance(config, Mapping):
        raise WebhookDispatchError("Webhook 配置必须是对象")

    method = str(config.get("method") or "POST").upper().strip()
    if method not in _ALLOWED_METHODS:
        raise WebhookDispatchError(f"Webhook 不支持 HTTP 方法: {method}")
    try:
        validator = (
            validate_outbound_url if resolve_target else _validate_preview_url)
        url = validator(str(config.get("url") or ""))
    except OutboundTargetError as exc:
        raise WebhookDispatchError(str(exc)) from exc

    max_body = max(1, int(settings.formal_action_webhook_max_body_bytes or 1_000_000))
    body = _render_json_body(
        str(config.get("bodyTemplate") or ""), params, object_props or {}, max_body)
    delivery_key = idempotency_key or f"formal-action:{uuid.uuid4()}"
    headers = _build_headers(config.get("headers"), delivery_key, body is not None)
    timeout = max(1, int(settings.formal_action_webhook_timeout_seconds or 15))
    max_attempts = max(1, min(5, int(settings.formal_action_webhook_max_attempts or 1)))
    return {
        "method": method,
        "url": url,
        "safeUrl": _display_url(url),
        "body": body,
        "headers": headers,
        "idempotencyKey": delivery_key,
        "timeout": timeout,
        "maxAttempts": max_attempts,
    }


def preview_webhook(config: Mapping[str, Any], *, params: Mapping[str, Any],
                    object_props: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate a dry-run webhook without HTTP or DNS side effects."""
    prepared = _prepare_webhook(
        config, params=params, object_props=object_props,
        resolve_target=False)
    return {
        "url": prepared["safeUrl"],
        "method": prepared["method"],
        "hasBody": prepared["body"] is not None,
        "targetValidation": "syntax_only_dns_deferred",
    }


def dispatch_webhook(config: Mapping[str, Any], *, params: Mapping[str, Any],
                     object_props: Mapping[str, Any] | None,
                     idempotency_key: str | None = None) -> dict[str, Any]:
    """Deliver one configured webhook or raise ``WebhookDispatchError``.

    Retry is intentionally limited to connection/timeout and transient 5xx/429
    responses.  All attempts carry one generated key so receivers that support
    HTTP idempotency can safely collapse retries.
    """
    prepared = _prepare_webhook(
        config, params=params, object_props=object_props,
        idempotency_key=idempotency_key)
    method = prepared["method"]
    url = prepared["url"]
    safe_url = prepared["safeUrl"]
    body = prepared["body"]
    headers = prepared["headers"]
    delivery_key = prepared["idempotencyKey"]
    timeout = prepared["timeout"]
    max_attempts = prepared["maxAttempts"]

    session = requests.Session()
    session.verify = api_hub_config.TLS_CA_BUNDLE or True
    last_error: str | None = None
    attempts_made = 0
    delivery_uncertain = False
    try:
        for attempt in range(1, max_attempts + 1):
            attempts_made = attempt
            response = None
            try:
                response = request_with_safe_redirects(
                    session, method, url, headers=headers, data=body, timeout=timeout)
                status = int(response.status_code)
                if 200 <= status < 300:
                    return {
                        "url": safe_url,
                        "method": method,
                        "statusCode": status,
                        "attempts": attempt,
                        "idempotencyKey": delivery_key,
                    }
                last_error = f"上游返回 HTTP {status}"
                # A non-2xx application can still have performed work before
                # returning its error.  Reconciliation must use the stable key.
                delivery_uncertain = True
                retryable = status == 429 or 500 <= status < 600
            except requests.Timeout:
                last_error = f"请求超时（{timeout}s）"
                delivery_uncertain = True
                retryable = True
            except OutboundTargetError as exc:
                # Validation covers the initial target and every redirect.
                # Either failure is configuration, not a transient network
                # error, and must never be retried.
                last_error = f"Webhook 目标无效: {exc}"
                # request_with_safe_redirects can raise after the initial
                # endpoint has already answered, so reconciliation must keep
                # treating the delivery outcome as uncertain.
                delivery_uncertain = True
                retryable = False
            except requests.RequestException as exc:
                last_error = f"请求失败: {exc}"
                delivery_uncertain = True
                retryable = True
            finally:
                if response is not None:
                    # A real ``requests`` response always owns a raw stream.
                    # Keep lightweight test/adaptor responses (which may only
                    # expose an in-memory body) compatible as well.
                    try:
                        response.close()
                    except AttributeError:
                        pass
            if not retryable or attempt == max_attempts:
                break
    finally:
        session.close()

    raise WebhookDispatchError(
        f"Webhook 投递失败（{safe_url}，已尝试 {attempts_made} 次）: "
        f"{last_error or '未知错误'}",
        safe_url=safe_url,
        method=method,
        idempotency_key=delivery_key,
        attempts=attempts_made,
        delivery_uncertain=delivery_uncertain,
    )
