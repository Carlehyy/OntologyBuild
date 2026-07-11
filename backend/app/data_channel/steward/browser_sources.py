"""Browser-provider CRUD, secret handling and per-conversation resolution."""
from __future__ import annotations

import hashlib
import json
import secrets
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import settings
from app.data_channel.steward.browser_runtime import BrowserTarget, managed_browser_target
from app.data_channel.steward.models import (
    BROWSER_SOURCE_COMPANION,
    BROWSER_SOURCE_REMOTE_CDP,
    StewardBrowserSource,
)
from app.data_channel.steward.service import StewardError
from app.shared.encryption import decrypt, encrypt


MANAGED_SOURCE_ID = "managed"


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def validate_remote_endpoint(value: str) -> str:
    endpoint = (value or "").strip().rstrip("/")
    parsed = urlparse(endpoint)
    allowed = {"https", "wss"} if settings.environment == "production" else {"http", "https", "ws", "wss"}
    if parsed.scheme.lower() not in allowed or not parsed.hostname:
        schemes = "https/wss" if settings.environment == "production" else "http/https/ws/wss"
        raise StewardError(f"远程浏览器地址必须是合法的 {schemes} CDP 地址")
    if parsed.username or parsed.password:
        raise StewardError("远程浏览器地址不能内嵌用户名或密码，请使用加密请求头")
    if parsed.hostname.lower() in {"169.254.169.254", "metadata.google.internal"}:
        raise StewardError("禁止连接云主机元数据地址")
    return endpoint


def normalize_headers(value: dict | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, item in (value or {}).items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise StewardError("远程浏览器请求头必须是字符串键值")
        if key.lower() in {"host", "connection", "upgrade"}:
            raise StewardError(f"不允许配置请求头 {key}")
        result[key[:200]] = item[:4000]
    return result


def managed_out() -> dict:
    return {
        "id": MANAGED_SOURCE_ID,
        "name": "平台托管浏览器",
        "sourceType": "managed",
        "enabled": True,
        "online": True,
        "hasSecret": False,
        "lastSeenAt": None,
    }


def source_out(source: StewardBrowserSource) -> dict:
    online = False
    if source.source_type == BROWSER_SOURCE_COMPANION:
        from app.data_channel.steward.companion import companion_hub
        online = companion_hub.is_online(source.id)
    return {
        "id": source.id,
        "name": source.name,
        "sourceType": source.source_type,
        "enabled": bool(source.enabled),
        "online": online if source.source_type == BROWSER_SOURCE_COMPANION else None,
        "hasSecret": bool(source.endpoint_url_encrypted or source.headers_encrypted or source.device_token_hash),
        "lastSeenAt": source.last_seen_at.isoformat() if source.last_seen_at else None,
        "createdAt": source.created_at.isoformat() if source.created_at else None,
        "updatedAt": source.updated_at.isoformat() if source.updated_at else None,
    }


def create_source(db: Session, user_id: str | None, *, name: str, source_type: str,
                  endpoint_url: str | None = None, headers: dict | None = None) -> tuple[StewardBrowserSource, str | None]:
    normalized_type = (source_type or "").strip().lower()
    if normalized_type not in {BROWSER_SOURCE_REMOTE_CDP, BROWSER_SOURCE_COMPANION}:
        raise StewardError("浏览器来源类型不合法")
    clean_name = (name or "").strip()[:120]
    if not clean_name:
        raise StewardError("浏览器来源名称不能为空")
    token: str | None = None
    endpoint_encrypted = ""
    headers_encrypted = ""
    device_hash = ""
    if normalized_type == BROWSER_SOURCE_REMOTE_CDP:
        endpoint_encrypted = encrypt(validate_remote_endpoint(endpoint_url or ""))
        clean_headers = normalize_headers(headers)
        headers_encrypted = encrypt(json.dumps(clean_headers, ensure_ascii=False)) if clean_headers else ""
    else:
        token = secrets.token_urlsafe(36)
        device_hash = token_hash(token)
    source = StewardBrowserSource(
        user_id=user_id, name=clean_name, source_type=normalized_type,
        endpoint_url_encrypted=endpoint_encrypted, headers_encrypted=headers_encrypted,
        device_token_hash=device_hash, enabled=True,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source, token


def require_source(db: Session, source_id: str, user_id: str | None, *, admin: bool = False) -> StewardBrowserSource:
    source = db.query(StewardBrowserSource).filter(StewardBrowserSource.id == source_id).first()
    if not source:
        raise StewardError("浏览器来源不存在")
    if source.user_id != user_id and not admin:
        raise StewardError("无权使用他人的浏览器来源")
    return source


def resolve_target(db: Session, source_id: str | None, user_id: str | None,
                   *, admin: bool = False) -> BrowserTarget:
    if not source_id or source_id == MANAGED_SOURCE_ID:
        return managed_browser_target()
    source = require_source(db, source_id, user_id, admin=admin)
    if not source.enabled:
        raise StewardError("当前浏览器来源已停用，请在实时浏览器中切换来源")
    if source.source_type == BROWSER_SOURCE_REMOTE_CDP:
        endpoint = decrypt(source.endpoint_url_encrypted)
        headers = json.loads(decrypt(source.headers_encrypted) or "{}")
    else:
        from app.data_channel.steward.companion import companion_hub
        endpoint = companion_hub.endpoint(source.id)
        if not endpoint:
            raise StewardError("本机浏览器助手尚未在线。请先下载并运行助手；生产环境必须使用 HTTPS/WSS。")
        headers = {}
    return BrowserTarget(
        key=f"{source.source_type}:{source.id}", endpoint_url=endpoint,
        source_type=source.source_type, label=source.name, headers=headers,
    )


def rotate_companion_token(db: Session, source: StewardBrowserSource) -> str:
    if source.source_type != BROWSER_SOURCE_COMPANION:
        raise StewardError("只有本机浏览器助手可以重置配对令牌")
    token = secrets.token_urlsafe(36)
    source.device_token_hash = token_hash(token)
    db.commit()
    return token
