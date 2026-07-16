"""统一接口执行器。

网页调试、MCP、n8n 内部代理与普通 HTTP 发布都走这里，确保 W3 登录态注入、
透明重登、动态参数合并和调用审计的行为一致。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from . import config, credential, db
from .outbound_security import OutboundTargetError, request_with_safe_redirects

_LOGIN_HOST = "login.huawei.com"
_MAX_BODY_CHARS = 1_000_000
_MAX_SNAPSHOT_BODY_CHARS = 100_000
_UNSET = object()
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}
_SENSITIVE_NAME_RE = re.compile(
    r"(authorization|cookie|token|secret|password|passwd|api[-_]?key|session)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)\b(authorization|cookie|token|secret|password|passwd|api[-_]?key|session)"
    r"\b(\s*[:=]\s*)([^\s,;&]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


@dataclass
class RequestOverrides:
    """一次调用允许覆盖的请求值和审计上下文。"""

    query_params: list[tuple[str, str]] | None = None
    headers: list[tuple[str, str]] | None = None
    body: Any = _UNSET
    content_type: str | None = None
    source: str = "ui"
    proxy_key_id: int | None = None
    proxy_key_name: str | None = None
    source_ip: str | None = None


def _looks_expired(resp: requests.Response) -> bool:
    if resp.status_code in (401, 403):
        return True
    final = resp.url or ""
    if _LOGIN_HOST in final:
        return True
    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "html" in content_type:
        try:
            head = resp.text[:4000].lower()
        except Exception:  # noqa: BLE001
            head = ""
        if _LOGIN_HOST in head and (
            "loginaccount" in head or "login1" in head or "hwidcenter" in head
        ):
            return True
    return False


def _parse_form(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out.append((key.strip(), value.strip()))
    return out


def _kv_pairs(items) -> list[tuple[str, str]]:
    out = []
    for item in items or []:
        if isinstance(item, dict):
            key, value = item.get("key"), item.get("value", "")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            key, value = item[0], item[1]
        else:
            continue
        key = str(key or "").strip()
        if key:
            out.append((key, str(value if value is not None else "")))
    return out


def _merge_query(defaults, overrides) -> list[tuple[str, str]]:
    base = _kv_pairs(defaults)
    if overrides is None:
        return base
    incoming = _kv_pairs(overrides)
    replaced = {key for key, _ in incoming}
    return [(key, value) for key, value in base if key not in replaced] + incoming


def _merge_headers(defaults, overrides) -> dict[str, str]:
    merged: dict[str, tuple[str, str]] = {}
    for key, value in _kv_pairs(defaults):
        merged[key.lower()] = (key, value)
    if overrides is not None:
        for key, value in _kv_pairs(overrides):
            merged[key.lower()] = (key, value)
    headers = {original: value for original, value in merged.values()}
    reserved = _HOP_BY_HOP_HEADERS | {config.PROXY_KEY_HEADER.lower()}
    for key in list(headers):
        if key.lower() in reserved:
            headers.pop(key, None)
    return headers


def _pop_header(headers: dict[str, str], name: str) -> str | None:
    target = name.lower()
    for key in list(headers):
        if key.lower() == target:
            return headers.pop(key)
    return None


def _set_header(headers: dict[str, str], name: str, value: str) -> None:
    _pop_header(headers, name)
    headers[name] = value


def _caller_cookies(cookie_header: str) -> dict[str, str]:
    parsed = SimpleCookie()
    try:
        parsed.load(cookie_header)
    except Exception:  # noqa: BLE001
        return {}
    return {key: morsel.value for key, morsel in parsed.items()}


def _redact_value(name: str, value: Any) -> Any:
    return "***" if _SENSITIVE_NAME_RE.search(name or "") else value


def _redact_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _redact_value(str(key), _redact_mapping(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_mapping(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return json.dumps(
                    _redact_mapping(json.loads(stripped)), ensure_ascii=False
                )
            except (json.JSONDecodeError, TypeError):
                pass
        return _redact_text(value)
    return value


def _redact_text(text: str) -> str:
    text = _BEARER_RE.sub("Bearer ***", text)
    return _SENSITIVE_TEXT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}***",
        text,
    )


def _redact_url(url: str) -> str:
    try:
        parsed = urlsplit(url or "")
        query = [
            (key, str(_redact_value(key, value)))
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query, doseq=True),
                "",
            )
        )
    except ValueError:
        return _redact_text(url or "")


def _redact_body(value: Any, content_type: str | None) -> Any:
    if value is _UNSET:
        return None
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif isinstance(value, str):
        text = value
    else:
        return _redact_mapping(value)

    if len(text) > _MAX_SNAPSHOT_BODY_CHARS:
        text = text[:_MAX_SNAPSHOT_BODY_CHARS] + "\n…（请求体快照已截断）"
    lowered = (content_type or "").lower()
    if "json" in lowered or text.lstrip().startswith(("{", "[")):
        try:
            return json.dumps(
                _redact_mapping(json.loads(text)), ensure_ascii=False
            )
        except (json.JSONDecodeError, TypeError):
            pass
    if "x-www-form-urlencoded" in lowered:
        return "&".join(
            f"{key}={_redact_value(key, item)}"
            for key, item in parse_qsl(text, keep_blank_values=True)
        )
    return _redact_text(text)


def _redact_response_body(value: str, content_type: str | None) -> str:
    redacted = _redact_body(value or "", content_type)
    return redacted if isinstance(redacted, str) else json.dumps(
        redacted, ensure_ascii=False
    )


def _redact_headers(headers: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"key": key, "value": str(_redact_value(key, value))}
        for key, value in headers.items()
    ]


def _redact_response_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: str(_redact_value(key, value))
        for key, value in (headers or {}).items()
    }


def _build_kwargs(
    iface: dict,
    overrides: RequestOverrides,
    *,
    use_w3: bool,
    session: requests.Session,
) -> tuple[dict, dict]:
    query = _merge_query(iface.get("query_params", []), overrides.query_params)
    headers = _merge_headers(iface.get("headers", []), overrides.headers)
    kwargs: dict[str, Any] = {
        "params": query or None,
        "timeout": config.HTTP_TIMEOUT,
    }

    body_for_snapshot: Any = _UNSET
    body_content_type = overrides.content_type
    if overrides.body is not _UNSET:
        kwargs["data"] = overrides.body
        body_for_snapshot = overrides.body
        if overrides.content_type:
            _set_header(headers, "Content-Type", overrides.content_type)
    else:
        body_type = iface.get("body_type", "none")
        body = iface.get("body_content", "") or ""
        if body_type == "json" and body.strip():
            kwargs["data"] = body.encode("utf-8")
            body_for_snapshot = body
            body_content_type = "application/json"
            if not any(key.lower() == "content-type" for key in headers):
                headers["Content-Type"] = "application/json; charset=utf-8"
        elif body_type == "form" and body.strip():
            form = _parse_form(body)
            kwargs["data"] = form
            body_for_snapshot = body
            body_content_type = "application/x-www-form-urlencoded"
        elif body_type == "raw" and body:
            kwargs["data"] = body.encode("utf-8")
            body_for_snapshot = body

    # W3 模式下可透传其它业务 Cookie，但同名登录 Cookie 永远以平台会话为准。
    cookie_header = None
    for key, value in list(headers.items()):
        if key.lower() == "cookie":
            cookie_header = value
            if use_w3:
                headers.pop(key)
            break
    if use_w3 and cookie_header:
        platform_cookie_names = set(session.cookies.get_dict())
        extra_cookies = {
            key: value
            for key, value in _caller_cookies(cookie_header).items()
            if key not in platform_cookie_names
        }
        if extra_cookies:
            kwargs["cookies"] = extra_cookies

    kwargs["headers"] = headers or None
    snapshot = {
        "method": iface.get("method"),
        "url": _redact_url(iface.get("url") or ""),
        "query_params": [
            {"key": key, "value": str(_redact_value(key, value))}
            for key, value in query
        ],
        "headers": _redact_headers(headers),
        "body_type": iface.get("body_type"),
        "body_content": _redact_body(body_for_snapshot, body_content_type),
        "use_w3": iface.get("use_w3"),
        "source": overrides.source,
        "proxy_key_name": overrides.proxy_key_name,
        "source_ip": overrides.source_ip,
    }
    return kwargs, snapshot


def _safe_text(resp: requests.Response) -> str:
    try:
        text = resp.text
    except Exception:  # noqa: BLE001
        text = resp.content.decode("utf-8", errors="replace")
    if len(text) > _MAX_BODY_CHARS:
        text = text[:_MAX_BODY_CHARS] + "\n\n…（响应过大，已截断显示）"
    return text


def _blank_result() -> dict:
    return {
        "ok": False,
        "status_code": None,
        "elapsed_ms": None,
        "response_headers": {},
        "response_body": "",
        "response_content": None,
        "content_type": "",
        "error": None,
        "error_type": None,
        "relogin": False,
    }


def _session_for_request(use_w3: bool, result: dict) -> requests.Session | None:
    if not use_w3:
        session = requests.Session()
        session.verify = config.TLS_CA_BUNDLE or True
        return session

    session = credential.build_session_from_saved()
    if session is None or credential.saved_is_expired():
        had_session = session is not None
        status = credential.refresh(force=False)
        if status.get("last_result") != "success":
            result["error"] = (
                "该接口需要 W3 登录态，自动登录失败："
                f"{status.get('message') or '未知原因'}"
            )
            result["error_type"] = "w3_login"
            return None
        session = credential.build_session_from_saved()
        if session is None:
            result["error"] = "该接口需要 W3 登录态，但刷新后仍未能建立会话"
            result["error_type"] = "w3_login"
            return None
        if had_session:
            result["relogin"] = True
    return session


def run_interface(
    iface: dict,
    overrides: RequestOverrides | None = None,
    *,
    include_response_content: bool = False,
    query_override: dict | None = None,
    body_override: str | None = None,
) -> dict:
    """执行接口；旧的 query_override/body_override 参数继续兼容 n8n 调用。"""
    method = (iface.get("method") or "GET").upper()
    url = (iface.get("url") or "").strip()
    use_w3 = bool(iface.get("use_w3", 1))
    if overrides is None:
        overrides = RequestOverrides(
            query_params=list((query_override or {}).items()) if query_override else None,
            body=body_override if body_override is not None else _UNSET,
            source="n8n_proxy" if query_override is not None or body_override is not None else "ui",
        )
    result = _blank_result()

    if not url:
        result["error"] = "URL 不能为空"
        result["error_type"] = "configuration"
        _save_run(iface, result, overrides, None)
        return result

    session = _session_for_request(use_w3, result)
    if session is None:
        _save_run(iface, result, overrides, None)
        return result

    kwargs, snapshot = _build_kwargs(iface, overrides, use_w3=use_w3, session=session)
    start = time.perf_counter()
    try:
        resp = request_with_safe_redirects(session, method, url, **kwargs)
        if use_w3 and _looks_expired(resp):
            status = credential.refresh()
            if status.get("last_result") == "success":
                session2 = credential.build_session_from_saved()
                if session2 is not None:
                    kwargs, snapshot = _build_kwargs(
                        iface, overrides, use_w3=use_w3, session=session2
                    )
                    resp = request_with_safe_redirects(
                        session2, method, url, **kwargs
                    )
                    result["relogin"] = True
                else:
                    result["error"] = "登录态疑似过期，自动重登后仍未能建立会话"
                    result["error_type"] = "w3_login"
            else:
                result["error"] = (
                    "登录态疑似过期，自动重登失败："
                    f"{status.get('message') or '未知原因'}"
                )
                result["error_type"] = "w3_login"

        result["status_code"] = resp.status_code
        result["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        result["response_headers"] = dict(resp.headers)
        result["content_type"] = resp.headers.get("Content-Type", "")
        result["response_body"] = _safe_text(resp)
        if include_response_content:
            result["response_content"] = resp.content
        if not 200 <= resp.status_code < 300:
            result["error"] = result["error"] or f"上游返回 HTTP {resp.status_code}"
            result["error_type"] = result["error_type"] or "upstream_http"
        result["ok"] = result["error"] is None
    except requests.Timeout as exc:
        result["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        result["error"] = _redact_text(f"请求超时：{exc}")
        result["error_type"] = "timeout"
    except OutboundTargetError as exc:
        result["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        result["error"] = f"目标地址被安全策略拒绝：{exc}"
        result["error_type"] = "ssrf_blocked"
    except requests.RequestException as exc:
        result["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        result["error"] = _redact_text(f"请求失败：{exc}")
        result["error_type"] = "network"

    _save_run(iface, result, overrides, snapshot)
    return result


def _save_run(
    iface: dict,
    result: dict,
    overrides: RequestOverrides,
    snapshot: dict | None,
) -> None:
    interface_id = iface.get("id")
    if not interface_id:
        return
    if snapshot is None:
        snapshot = {
            "method": iface.get("method"),
            "url": _redact_url(iface.get("url") or ""),
            "query_params": [
                {
                    "key": item.get("key", ""),
                    "value": str(_redact_value(item.get("key", ""), item.get("value", ""))),
                }
                for item in iface.get("query_params", [])
            ],
            "headers": _redact_headers(
                {
                    item.get("key", ""): item.get("value", "")
                    for item in iface.get("headers", [])
                    if item.get("key")
                }
            ),
            "body_type": iface.get("body_type"),
            "body_content": _redact_body(
                iface.get("body_content", ""),
                "application/json" if iface.get("body_type") == "json" else None,
            ),
            "use_w3": iface.get("use_w3"),
            "source": overrides.source,
            "proxy_key_name": overrides.proxy_key_name,
            "source_ip": overrides.source_ip,
        }
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs(interface_id, ok, status_code, elapsed_ms, request_snapshot, "
            "response_headers, response_body, error, relogin, source, proxy_key_id, "
            "proxy_key_name, source_ip, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                interface_id,
                1 if result["ok"] else 0,
                result["status_code"],
                result["elapsed_ms"],
                json.dumps(snapshot, ensure_ascii=False),
                json.dumps(
                    _redact_response_headers(result["response_headers"]),
                    ensure_ascii=False,
                ),
                _redact_response_body(
                    result["response_body"], result.get("content_type")
                ),
                _redact_text(result["error"]) if result["error"] else None,
                1 if result["relogin"] else 0,
                overrides.source,
                overrides.proxy_key_id,
                overrides.proxy_key_name,
                overrides.source_ip,
                now,
            ),
        )
        result["run_id"] = cur.lastrowid
        if overrides.proxy_key_id:
            conn.execute(
                "UPDATE proxy_keys SET last_used_at = ?, updated_at = ? WHERE id = ?",
                (now, now, overrides.proxy_key_id),
            )
        conn.execute(
            "DELETE FROM runs WHERE interface_id = ? AND id NOT IN "
            "(SELECT id FROM runs WHERE interface_id = ? ORDER BY id DESC LIMIT ?)",
            (interface_id, interface_id, config.MAX_RUNS_PER_INTERFACE),
        )
    if iface.get("use_w3"):
        db.record_credential_usage(
            iface,
            {
                **result,
                "error": _redact_text(result["error"]) if result["error"] else None,
            },
            now,
        )
