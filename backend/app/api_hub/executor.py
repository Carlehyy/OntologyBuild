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
from urllib.parse import quote

import requests

from . import config, credential, db, tls
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


@dataclass
class RequestFile:
    """One runtime multipart file. File bytes are never persisted in interface config/history."""

    field_name: str
    filename: str
    stream: Any
    content_type: str = "application/octet-stream"
    size: int | None = None


@dataclass
class RequestOverrides:
    """一次调用允许覆盖的请求值和审计上下文。"""

    path_params: list[tuple[str, str]] | None = None
    query_params: list[tuple[str, str]] | None = None
    headers: list[tuple[str, str]] | None = None
    body: Any = _UNSET
    content_type: str | None = None
    multipart_fields: list[tuple[str, str]] | None = None
    files: list[RequestFile] | None = None
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


_PATH_PARAMETER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}")


def _resolve_url(url: str, path_params) -> str:
    supplied = {key: value for key, value in _kv_pairs(path_params)}
    required = set(_PATH_PARAMETER_RE.findall(url))
    missing = sorted(required - supplied.keys())
    if missing:
        raise ValueError("缺少 Path 参数：" + ", ".join(missing))
    unknown = sorted(supplied.keys() - required)
    if unknown:
        raise ValueError("URL 中不存在这些 Path 参数：" + ", ".join(unknown))
    return _PATH_PARAMETER_RE.sub(
        lambda match: quote(supplied[match.group(1)], safe=""), url
    )


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


def _snapshot_body(value: Any) -> Any:
    """保留调用历史中的原始请求体，仅限制快照大小。"""
    if value is _UNSET:
        return None
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif isinstance(value, str):
        text = value
    else:
        return value

    if len(text) > _MAX_SNAPSHOT_BODY_CHARS:
        text = text[:_MAX_SNAPSHOT_BODY_CHARS] + "\n…（请求体快照已截断）"
    return text


def _snapshot_response_body(value: str) -> str:
    text = value or ""
    if len(text) > _MAX_SNAPSHOT_BODY_CHARS:
        return text[:_MAX_SNAPSHOT_BODY_CHARS] + "\n…（响应体快照已截断）"
    return text


def _snapshot_headers(headers: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"key": key, "value": str(value)}
        for key, value in headers.items()
    ]


def _build_kwargs(
    iface: dict,
    overrides: RequestOverrides,
    *,
    use_w3: bool,
    session: requests.Session,
) -> tuple[dict, dict]:
    resolved_url = _resolve_url(iface.get("url") or "", overrides.path_params)
    query = _merge_query(iface.get("query_params", []), overrides.query_params)
    headers = _merge_headers(iface.get("headers", []), overrides.headers)
    kwargs: dict[str, Any] = {
        "params": query or None,
        "timeout": config.HTTP_TIMEOUT,
    }

    body_for_snapshot: Any = _UNSET
    if overrides.multipart_fields is not None or overrides.files is not None:
        fields = _kv_pairs(overrides.multipart_fields)
        parts: list[tuple[str, tuple]] = [
            (key, (None, value)) for key, value in fields
        ]
        file_snapshot = []
        for item in overrides.files or []:
            try:
                item.stream.seek(0)
            except (AttributeError, OSError, ValueError):
                pass
            parts.append(
                (
                    item.field_name,
                    (
                        item.filename or "upload",
                        item.stream,
                        item.content_type or "application/octet-stream",
                    ),
                )
            )
            file_snapshot.append(
                {
                    "field": item.field_name,
                    "filename": item.filename or "upload",
                    "content_type": item.content_type or "application/octet-stream",
                    "size": item.size,
                }
            )
        # Let requests generate the multipart boundary. A saved/static Content-Type
        # would otherwise carry a stale boundary and corrupt the upload.
        _pop_header(headers, "Content-Type")
        if parts:
            kwargs["files"] = parts
        body_for_snapshot = {
            "fields": [{"key": key, "value": value} for key, value in fields],
            "files": file_snapshot,
        }
    elif overrides.body is not _UNSET:
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
            if not any(key.lower() == "content-type" for key in headers):
                headers["Content-Type"] = "application/json; charset=utf-8"
        elif body_type == "form" and body.strip():
            form = _parse_form(body)
            kwargs["data"] = form
            body_for_snapshot = body
        elif body_type == "multipart":
            form = _parse_form(body)
            _pop_header(headers, "Content-Type")
            if form:
                kwargs["files"] = [(key, (None, value)) for key, value in form]
            body_for_snapshot = {
                "fields": [{"key": key, "value": value} for key, value in form],
                "files": [],
            }
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
        "url": resolved_url,
        "query_params": [
            {"key": key, "value": str(value)}
            for key, value in query
        ],
        "headers": _snapshot_headers(headers),
        "body_type": iface.get("body_type"),
        "body_content": _snapshot_body(body_for_snapshot),
        "use_w3": iface.get("use_w3"),
        "source": overrides.source,
        "proxy_key_name": overrides.proxy_key_name,
        "source_ip": overrides.source_ip,
    }
    return kwargs, snapshot


def _safe_text(resp: requests.Response) -> str:
    content_type = (resp.headers.get("Content-Type") or "").split(";", 1)[0].lower()
    textual = (
        content_type.startswith("text/")
        or content_type.endswith("+json")
        or content_type.endswith("+xml")
        or content_type in {
            "application/json",
            "application/xml",
            "application/javascript",
            "application/x-www-form-urlencoded",
            "application/graphql",
        }
    )
    if content_type and not textual:
        return f"（二进制响应：{len(resp.content)} bytes，Content-Type: {content_type}）"
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
        tls.configure_session(session)
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

    try:
        kwargs, snapshot = _build_kwargs(iface, overrides, use_w3=use_w3, session=session)
        request_url = snapshot["url"]
    except ValueError as exc:
        result["error"] = str(exc)
        result["error_type"] = "configuration"
        _save_run(iface, result, overrides, None)
        return result
    start = time.perf_counter()
    try:
        resp = request_with_safe_redirects(session, method, request_url, **kwargs)
        if use_w3 and _looks_expired(resp):
            status = credential.refresh()
            if status.get("last_result") == "success":
                session2 = credential.build_session_from_saved()
                if session2 is not None:
                    kwargs, snapshot = _build_kwargs(
                        iface, overrides, use_w3=use_w3, session=session2
                    )
                    request_url = snapshot["url"]
                    resp = request_with_safe_redirects(
                        session2, method, request_url, **kwargs
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
        result["error"] = f"请求超时：{exc}"
        result["error_type"] = "timeout"
    except OutboundTargetError as exc:
        result["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        result["error"] = f"接口 URL 无效：{exc}"
        result["error_type"] = "configuration"
    except requests.RequestException as exc:
        result["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        result["error"] = f"请求失败：{exc}"
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
            "url": iface.get("url") or "",
            "query_params": [
                {
                    "key": item.get("key", ""),
                    "value": str(item.get("value", "")),
                }
                for item in iface.get("query_params", [])
            ],
            "headers": _snapshot_headers(
                {
                    item.get("key", ""): item.get("value", "")
                    for item in iface.get("headers", [])
                    if item.get("key")
                }
            ),
            "body_type": iface.get("body_type"),
            "body_content": _snapshot_body(iface.get("body_content", "")),
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
                    result["response_headers"],
                    ensure_ascii=False,
                ),
                _snapshot_response_body(result["response_body"]),
                result["error"],
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
        db.record_credential_usage(iface, result, now)
