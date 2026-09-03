"""超级助手会话内容搜索端点（独立子路由：router.py 已贴近架构测试行数上限）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.super_assistant import search_service
from app.super_assistant.schemas import SearchResultOut

router = APIRouter()


@router.get("/search/conversations", response_model=SearchResultOut)
def search_conversations(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SearchResultOut:
    return search_service.search_conversations(db, current_user, q=q, limit=limit)
