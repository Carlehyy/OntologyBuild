"""
登录态模块 —— 整个平台的心脏。

W3 登录逻辑原样移植自同事的 jx-favorite-sync（相同的登录地址、JSON 载荷、
statusCode==0 成功判断），只是从那个耦合了 Obsidian 输出/bs4 的类里剥离出来，
让它只做一件事：拿到一份可用的登录 Cookie。

登录成功后，把整个 cookie jar（带 domain/path/过期时间）连同获取时间持久化到
data/w3_session.json，发请求时再重建出来注入。
"""
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

import requests

from . import config, tls
from . import db
from app.shared.encryption import decrypt, encrypt
from .outbound_security import request_with_safe_redirects

_LOCK = threading.Lock()
_STATUS_KEY = "w3_refresh_status"

# 最近一次刷新（登录）尝试的结果；同时持久化到 SQLite 供多 worker 展示。
_last = {
    "result": None,      # "success" | "failed" | None
    "message": "",
    "refreshed_at": None,
}

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


def _new_session() -> requests.Session:
    s = requests.Session()
    tls.configure_session(s, use_system_trust=True)
    s.headers.update(_DEFAULT_HEADERS)
    return s


def validate_login_url(login_url: str) -> str:
    value = (login_url or "").strip()
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    allowed = {item.lower() for item in config.W3_LOGIN_ALLOWED_HOSTS}
    if parsed.scheme.lower() != "https" or not hostname or parsed.username or parsed.password:
        raise ValueError("W3 登录地址必须是无内嵌账号信息的 HTTPS 绝对地址")
    if hostname not in allowed:
        raise ValueError(
            "W3 登录地址域名不在允许清单中；请通过 "
            "API_HUB_W3_LOGIN_ALLOWED_HOSTS 由管理员配置"
        )
    return value


def _login_with(username: str, password: str, login_url: str) -> requests.Session:
    if not username:
        raise RuntimeError("未配置 W3_USERNAME（请在 .env 填写工号）")
    if not password:
        raise RuntimeError("未配置 W3_PASSWORD（请在 .env 填写密码）")
    try:
        login_url = validate_login_url(login_url)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    s = _new_session()
    payload = {
        "loginAccount": username,
        "uid": username,
        "password": password,
        "encryptedPasswordSwitch": "off",
    }
    try:
        resp = request_with_safe_redirects(
            s,
            "POST",
            login_url,
            validator=validate_login_url,
            json=payload,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"登录请求失败：{e}（是否已连内网/VPN？）") from e

    try:
        result = resp.json()
    except ValueError:
        raise RuntimeError("登录返回的不是 JSON，可能未连内网或登录地址有误")

    if result.get("statusCode") != 0:
        raise RuntimeError(f"登录被拒绝：{result.get('statusText', '未知原因')}")

    return s


def w3_login() -> requests.Session:
    """执行一次 W3 SSO 登录。成功返回带登录 Cookie 的 Session，失败抛 RuntimeError。"""
    return _login_with(*runtime_credentials())


# ── Cookie 持久化 ──────────────────────────────────────────
def _serialize(jar) -> list:
    out = []
    for c in jar:
        out.append({
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path,
            "expires": c.expires,
            "secure": c.secure,
        })
    return out


def save_session(session: requests.Session) -> None:
    payload = {
        "acquired_at": datetime.now(timezone.utc).isoformat(),
        "cookies": _serialize(session.cookies),
    }
    config.SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=config.SESSION_PATH.parent,
            prefix=config.SESSION_PATH.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, config.SESSION_PATH)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def load_saved() -> Optional[dict]:
    if not config.SESSION_PATH.exists():
        return None
    try:
        data = json.loads(config.SESSION_PATH.read_text(encoding="utf-8"))
        return data if data.get("cookies") else None
    except (json.JSONDecodeError, OSError, AttributeError):
        return None


def build_session_from_saved() -> Optional[requests.Session]:
    """从磁盘上的 cookie 重建一个 Session；没有则返回 None。"""
    saved = load_saved()
    if not saved:
        return None
    s = _new_session()
    for c in saved["cookies"]:
        s.cookies.set(
            c["name"], c["value"],
            domain=c.get("domain", ""),
            path=c.get("path", "/"),
            expires=c.get("expires"),
            secure=c.get("secure", False),
        )
    return s


def clear_saved() -> None:
    if config.SESSION_PATH.exists():
        config.SESSION_PATH.unlink()


# ── 在线授权配置 ──────────────────────────────────────────
_MODE_KEY = "w3_config_mode"
_USER_KEY = "w3_username"
_PASSWORD_KEY = "w3_password_encrypted"
_LOGIN_URL_KEY = "w3_login_url"


def _online_mode() -> bool:
    return db.get_setting(_MODE_KEY, "") == "online"


def runtime_credentials() -> tuple[str, str, str]:
    """Return the effective credentials, preferring encrypted online config."""
    if not _online_mode():
        return config.W3_USERNAME, config.W3_PASSWORD, config.W3_LOGIN_URL
    username = db.get_setting(_USER_KEY, "") or ""
    encrypted = db.get_setting(_PASSWORD_KEY, "") or ""
    password = ""
    if encrypted:
        try:
            password = decrypt(encrypted)
        except Exception:  # unreadable ciphertext must fail closed
            password = ""
    login_url = db.get_setting(_LOGIN_URL_KEY, "") or config.W3_LOGIN_URL
    return username, password, login_url


def public_configuration() -> dict:
    username, password, login_url = runtime_credentials()
    return {
        "username": username,
        "password_configured": bool(password),
        "login_url": login_url,
        "source": "online" if _online_mode() else "environment",
    }


def update_configuration(
    username: str,
    password: str | None,
    login_url: str,
    clear_password: bool = False,
) -> dict:
    with _LOCK, _process_refresh_lock():
        current_user, current_password, current_login_url = runtime_credentials()
        username = username.strip()
        login_url = validate_login_url(login_url.strip() or config.W3_LOGIN_URL)
        identity_changed = username != current_user or login_url != current_login_url
        if clear_password:
            next_password = ""
        elif password is None or password == "":
            if identity_changed and current_password:
                raise ValueError("修改 W3 账号或登录地址时必须重新输入密码")
            next_password = current_password
        else:
            next_password = password
        db.set_setting(_MODE_KEY, "online")
        db.set_setting(_USER_KEY, username)
        db.set_setting(_PASSWORD_KEY, encrypt(next_password) if next_password else "")
        db.set_setting(_LOGIN_URL_KEY, login_url)
        clear_saved()  # a changed account must not reuse the previous cookie jar
        return public_configuration()


# ── 对外动作 ───────────────────────────────────────────────
@contextmanager
def _process_refresh_lock():
    config.SESSION_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.SESSION_LOCK_PATH.open("a+", encoding="utf-8") as handle:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            fcntl = None
        try:
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass


def _persist_last() -> None:
    db.set_setting(_STATUS_KEY, json.dumps(_last, ensure_ascii=False))


def _load_last() -> dict:
    raw = db.get_setting(_STATUS_KEY, "")
    if raw:
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                return {**_last, **value}
        except (json.JSONDecodeError, TypeError):
            pass
    return dict(_last)


def _recent_session(max_age_seconds: int = 60) -> bool:
    saved = load_saved()
    if not saved or not saved.get("acquired_at"):
        return False
    try:
        acquired = datetime.fromisoformat(
            str(saved["acquired_at"]).replace("Z", "+00:00")
        )
        return (
            datetime.now(timezone.utc) - acquired.astimezone(timezone.utc)
        ).total_seconds() < max_age_seconds
    except (TypeError, ValueError):
        return False


def refresh(*, force: bool = True) -> dict:
    """登录并持久化 Cookie；线程和多进程之间都串行化。"""
    with _LOCK, _process_refresh_lock():
        if not force and _recent_session():
            return status()
        now = datetime.now(timezone.utc).isoformat()
        try:
            s = w3_login()
            save_session(s)
            _last.update({"result": "success", "message": "登录成功", "refreshed_at": now})
        except Exception as e:  # noqa: BLE001 —— 任何失败都要回报给前端，不能崩
            _last.update({"result": "failed", "message": str(e), "refreshed_at": now})
        _persist_last()
        return status()


def _session_expiry(saved: dict):
    """从持久化的 cookie 里推断会话过期时间。

    返回 (earliest_expires_ts, has_expiry)：
      - earliest_expires_ts：所有带过期时间的 cookie 中最早的那个（epoch 秒）；
      - has_expiry：是否至少有一个 cookie 带了明确的过期时间。
    很多 SSO 用的是「会话 cookie」（expires=None），这种无法从本地判断，
    此时 has_expiry=False，我们就不对有效性下结论、只如实汇报“有会话文件”。
    """
    earliest = None
    has_expiry = False
    for c in (saved.get("cookies") or []):
        exp = c.get("expires")
        if exp is None:
            continue
        try:
            exp = float(exp)
        except (TypeError, ValueError):
            continue
        has_expiry = True
        if earliest is None or exp < earliest:
            earliest = exp
    return earliest, has_expiry


def saved_is_expired(margin_seconds: int = 30) -> bool:
    """基于持久化 cookie 的过期时间，尽力判断当前会话是否已过期 / 即将过期。

    仅在「能本地判定」时返回 True，即 cookie 带了明确的过期时间且已过期
    （含 margin_seconds 提前量，避免请求发出途中刚好过期）。
    若没有会话、或都是无过期时间的会话 cookie（本地判断不了），返回 False，
    交给请求层的反应式重登兜底。
    """
    saved = load_saved()
    if not saved:
        return False
    earliest, has_expiry = _session_expiry(saved)
    if has_expiry and earliest is not None:
        return earliest <= (datetime.now(timezone.utc).timestamp() + max(0, margin_seconds))
    return False


def status() -> dict:
    """登录态快照。

    无法在不发请求的情况下 100% 确认会话有效；但如果持久化的 cookie 里带了
    明确的过期时间，我们就用它做一次本地判断，避免“会话其实早过期了，界面却一直
    显示已登录”的误导。对于没有过期时间的会话 cookie，只能如实汇报“有会话”。
    """
    saved = load_saved()
    expired = False
    expires_at = None
    if saved:
        earliest, has_expiry = _session_expiry(saved)
        if has_expiry and earliest is not None:
            expires_at = datetime.fromtimestamp(earliest, tz=timezone.utc).isoformat()
            expired = earliest <= datetime.now(timezone.utc).timestamp()
    public = public_configuration()
    last = _load_last()
    return {
        "configured": bool(public["username"] and public["password_configured"]),
        "username": public["username"],
        "credential_source": public["source"],
        "has_session": saved is not None,
        "expired": expired,            # True = 本地可判定已过期，建议重新登录
        "expires_at": expires_at,      # 已知的最早过期时间（无则为 None）
        "acquired_at": saved.get("acquired_at") if saved else None,
        "last_result": last["result"],
        "message": last["message"],
        "refreshed_at": last["refreshed_at"],
    }
