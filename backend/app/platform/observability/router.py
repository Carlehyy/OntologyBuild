"""Admin-facing HTTP adapters for the performance monitoring page.

Mounted under /api/v1/settings/monitoring via the settings aggregate router;
require_admin is applied by the composition root in app/main.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.platform.observability import service

router = APIRouter()


@router.get("/overview")
def overview(
    window: str = Query("24h", pattern="^(24h|7d)$"),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return {"data": service.overview(db, window=window)}


@router.get("/trend")
def trend(
    window: str = Query("24h", pattern="^(24h|7d)$"),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return {"data": service.trend(db, window=window)}


@router.get("/top")
def top(
    window: str = Query("24h", pattern="^(24h|7d)$"),
    sort_by: str = Query("slow_count", pattern="^(slow_count|p95_ms|avg_ms|requests|error_rate)$"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return {"data": service.top_routes(db, window, sort_by=sort_by, limit=limit)}


@router.get("/slow-requests")
def slow_requests(
    start: str = "",
    end: str = "",
    route: str = "",
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return {
        "data": service.slow_requests(
            db, start=start, end=end, route=route, page=page, size=size
        )
    }
