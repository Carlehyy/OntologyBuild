"""
第三方上传鉴权 — get_ingest_key

读 X-API-Key 头 → sha256 比对 event_ingest_keys（enabled 且未吊销）→ 命中则
更新 last_used_at，并携带来源 IP 返回。与平台 JWT 的 get_current_user 互不干扰
（HTTPBearer(auto_error=False) + 无全局鉴权中间件，路由各自 Depends）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.deps import get_db
from app.events import service
from app.events.models import EventIngestKey

# auto_error=False：缺头时返回 None 而非直接 403，让我们统一返回 401 "Invalid API key"
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass
class IngestContext:
    key: EventIngestKey
    client_ip: Optional[str]


def _client_ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def get_ingest_key(
    request: Request,
    api_key: Optional[str] = Depends(api_key_header),
    db: Session = Depends(get_db),
) -> IngestContext:
    key = service.verify_ingest_key(db, (api_key or "").strip())
    if not key:
        # 刻意用 "Invalid API key"，不用 "Not authenticated"——避免前端 axios 拦截器误判跳登录
        raise HTTPException(status_code=401, detail="Invalid API key")
    key.last_used_at = service._now()
    db.commit()
    return IngestContext(key=key, client_ip=_client_ip(request))
