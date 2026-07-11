"""
执行器 —— 把一条保存的接口真正发出去。

关键能力（把复杂留给系统）：启用 W3 的接口，先用现有 Cookie 直接发；如果响应
401/403 或被重定向回 login.huawei.com（说明会话过期），就静默重新登录一次、用新
Cookie 重发该请求，再把结果交还给前端。整个过程用户无感。
"""
import json
import time
from datetime import datetime, timezone

import requests

from . import config, credential, db

_LOGIN_HOST = "login.huawei.com"
_MAX_BODY_CHARS = 1_000_000  # 响应体最多展示 ~1MB，超出截断


def _looks_expired(resp: requests.Response) -> bool:
    if resp.status_code in (401, 403):
        return True
    # 被重定向到了 SSO 登录页（最终 URL 落在登录域）
    final = resp.url or ""
    if _LOGIN_HOST in final:
        return True
    # 兜底：有些后端不重定向、也不返回 401/403，而是直接回 200 + 一段 SSO 登录页 HTML。
    # 仅在响应像 HTML 且明显是登录页时才判定过期，避免误伤正常的业务响应。
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "html" in ctype:
        try:
            head = resp.text[:4000].lower()
        except Exception:  # noqa: BLE001
            head = ""
        if _LOGIN_HOST in head and ("loginaccount" in head or "login1" in head or "hwidcenter" in head):
            return True
    return False


def _parse_form(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _build_kwargs(iface: dict, query_override: dict | None = None,
                  body_override: str | None = None) -> dict:
    params = {p["key"]: p["value"] for p in iface.get("query_params", []) if p.get("key")}
    if query_override:
        params.update({str(k): str(v) for k, v in query_override.items() if v is not None})
    headers = {h["key"]: h["value"] for h in iface.get("headers", []) if h.get("key")}

    kwargs = {
        "params": params or None,
        "timeout": config.HTTP_TIMEOUT,
        "allow_redirects": True,
    }

    body_type = iface.get("body_type", "none")
    body = body_override if body_override is not None else (iface.get("body_content", "") or "")

    if body_type == "json" and body.strip():
        kwargs["data"] = body.encode("utf-8")
        headers.setdefault("Content-Type", "application/json; charset=utf-8")
    elif body_type == "form" and body.strip():
        kwargs["data"] = _parse_form(body)
    elif body_type == "raw" and body:
        kwargs["data"] = body.encode("utf-8")

    kwargs["headers"] = headers or None
    return kwargs


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
        "content_type": "",
        "error": None,
        "relogin": False,
    }


def run_interface(iface: dict, *, query_override: dict | None = None,
                  body_override: str | None = None) -> dict:
    method = (iface.get("method") or "GET").upper()
    url = (iface.get("url") or "").strip()
    use_w3 = bool(iface.get("use_w3", 1))
    result = _blank_result()

    if not url:
        result["error"] = "URL 不能为空"
        _save_run(iface, result)
        return result

    kwargs = _build_kwargs(iface, query_override=query_override, body_override=body_override)

    # 选择会话
    if use_w3:
        # 主动式：发请求前先确保有一份「尽量有效」的登录态。
        #   - 没有会话，或本地可判定已过期/即将过期 → 先刷新登录，再带新会话发，
        #     避免「明知会过期还硬发一次、失败了才补登」。
        session = credential.build_session_from_saved()
        if session is None or credential.saved_is_expired():
            had_session = session is not None
            st = credential.refresh()   # 走 refresh：带锁、并把最新登录态同步给界面
            if st.get("last_result") != "success":
                result["error"] = f"该接口需要 W3 登录态，自动登录失败：{st.get('message') or '未知原因'}"
                _save_run(iface, result)
                return result
            session = credential.build_session_from_saved()
            if session is None:
                result["error"] = "该接口需要 W3 登录态，但刷新后仍未能建立会话"
                _save_run(iface, result)
                return result
            if had_session:
                result["relogin"] = True   # 之前有会话、这次因过期而重登
    else:
        session = requests.Session()
        session.verify = False

    start = time.perf_counter()
    try:
        resp = session.request(method, url, **kwargs)

        # 反应式兜底：本地判断不了过期（如纯会话 cookie），但服务端把我们当未登录时，
        # 重新登录并重发一次，尽量让本次调用成功。
        if use_w3 and _looks_expired(resp):
            st = credential.refresh()
            if st.get("last_result") == "success":
                session2 = credential.build_session_from_saved()
                if session2 is not None:
                    resp = session2.request(method, url, **kwargs)
                    result["relogin"] = True
                else:
                    result["error"] = "登录态疑似过期，自动重登后仍未能建立会话"
            else:
                result["error"] = f"登录态疑似过期，自动重登失败：{st.get('message') or '未知原因'}"

        result["status_code"] = resp.status_code
        result["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        result["response_headers"] = dict(resp.headers)
        result["content_type"] = resp.headers.get("Content-Type", "")
        result["response_body"] = _safe_text(resp)
        if not 200 <= resp.status_code < 300:
            result["error"] = result["error"] or f"上游返回 HTTP {resp.status_code}"
        result["ok"] = result["error"] is None
    except requests.RequestException as e:
        result["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        result["error"] = f"请求失败：{e}"

    _save_run(iface, result)
    return result


def _save_run(iface: dict, result: dict) -> None:
    iid = iface.get("id")
    if not iid:
        return
    snapshot = {
        "method": iface.get("method"),
        "url": iface.get("url"),
        "query_params": iface.get("query_params"),
        "headers": iface.get("headers"),
        "body_type": iface.get("body_type"),
        "body_content": iface.get("body_content"),
        "use_w3": iface.get("use_w3"),
    }
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs(interface_id, ok, status_code, elapsed_ms, request_snapshot, "
            "response_headers, response_body, error, relogin, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                iid,
                1 if result["ok"] else 0,
                result["status_code"],
                result["elapsed_ms"],
                json.dumps(snapshot, ensure_ascii=False),
                json.dumps(result["response_headers"], ensure_ascii=False),
                result["response_body"],
                result["error"],
                1 if result["relogin"] else 0,
                now,
            ),
        )
        result["run_id"] = cur.lastrowid
        # 只保留最近 N 条
        conn.execute(
            "DELETE FROM runs WHERE interface_id = ? AND id NOT IN "
            "(SELECT id FROM runs WHERE interface_id = ? ORDER BY id DESC LIMIT ?)",
            (iid, iid, config.MAX_RUNS_PER_INTERFACE),
        )
    if iface.get("use_w3"):
        db.record_credential_usage(iface, result, now)
