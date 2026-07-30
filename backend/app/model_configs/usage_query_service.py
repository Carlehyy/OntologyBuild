"""Usage statistics and paginated call-log queries for model configs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.model_configs.config_service import require_config
from app.model_configs.models import ModelCallLog
from app.model_configs.presentation import (
    iso_utc,
    safe_log_error,
    utc_naive,
)


def get_model_stats(
    db: Session,
    model_id: str,
    *,
    now: datetime | None = None,
) -> dict:
    require_config(db, model_id)
    now = now or datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    thirty_days_ago = now - timedelta(days=30)

    today_calls = (
        db.query(func.count(ModelCallLog.id))
        .filter(
            ModelCallLog.model_config_id == model_id,
            ModelCallLog.created_at >= today_start,
        )
        .scalar()
        or 0
    )
    total_30d = (
        db.query(func.count(ModelCallLog.id))
        .filter(
            ModelCallLog.model_config_id == model_id,
            ModelCallLog.created_at >= thirty_days_ago,
        )
        .scalar()
        or 0
    )
    success_30d = (
        db.query(func.count(ModelCallLog.id))
        .filter(
            ModelCallLog.model_config_id == model_id,
            ModelCallLog.created_at >= thirty_days_ago,
            ModelCallLog.status == "success",
        )
        .scalar()
        or 0
    )
    availability = (
        round(success_30d / total_30d * 100, 1)
        if total_30d > 0
        else None
    )
    avg_latency = (
        db.query(func.avg(ModelCallLog.latency_ms))
        .filter(
            ModelCallLog.model_config_id == model_id,
            ModelCallLog.created_at >= thirty_days_ago,
            ModelCallLog.status == "success",
        )
        .scalar()
    )
    avg_latency = round(avg_latency, 1) if avg_latency else None
    last_call = (
        db.query(ModelCallLog)
        .filter(ModelCallLog.model_config_id == model_id)
        .order_by(ModelCallLog.created_at.desc())
        .first()
    )
    recent_60 = (
        db.query(ModelCallLog)
        .filter(ModelCallLog.model_config_id == model_id)
        .order_by(ModelCallLog.created_at.desc())
        .limit(60)
        .all()
    )
    recent_60.reverse()

    heat_cells = []
    for log in recent_60:
        if log.status == "success":
            if log.latency_ms < 500:
                color = "#216e39"
            elif log.latency_ms < 1000:
                color = "#2d8a4e"
            elif log.latency_ms < 3000:
                color = "#40c463"
            else:
                color = "#9be9a8"
            cell_status = "success"
            title = f"成功 {log.latency_ms}ms"
        elif log.status == "error":
            color = "#e5484d"
            cell_status = "error"
            title = (
                f"异常: {safe_log_error(log.error_message) or '未知错误'}"
            )
        else:
            color = "#f0a020"
            cell_status = "timeout"
            title = f"超时 {log.latency_ms}ms"
        heat_cells.append({
            "color": color,
            "title": title,
            "status": cell_status,
        })

    for _ in range(60 - len(heat_cells)):
        heat_cells.insert(0, {
            "color": "#eceef1",
            "title": "暂无调用记录",
            "status": "none",
        })

    return {
        "todayCalls": today_calls,
        "availability": (
            f"{availability}" if availability is not None else None
        ),
        "avgLatency": avg_latency,
        "lastCall": iso_utc(last_call.created_at) if last_call else None,
        "successRate": (
            round(success_30d / total_30d * 100, 1)
            if total_30d > 0
            else None
        ),
        "heatCells": heat_cells,
    }


def list_model_calls(
    db: Session,
    model_id: str,
    *,
    page: int,
    page_size: int,
    status: str,
    start: datetime | None,
    end: datetime | None,
) -> dict:
    require_config(db, model_id)
    normalized_status = status.strip().lower()
    if normalized_status in {"", "all"}:
        normalized_status = ""
    elif normalized_status not in {"success", "error", "timeout"}:
        raise HTTPException(400, "不支持的调用状态筛选")

    start_utc = utc_naive(start)
    end_utc = utc_naive(end)
    if start_utc and end_utc and start_utc > end_utc:
        raise HTTPException(400, "开始时间不能晚于结束时间")

    query = db.query(ModelCallLog).filter(
        ModelCallLog.model_config_id == model_id,
    )
    if normalized_status:
        query = query.filter(ModelCallLog.status == normalized_status)
    if start_utc:
        query = query.filter(ModelCallLog.created_at >= start_utc)
    if end_utc:
        query = query.filter(ModelCallLog.created_at <= end_utc)

    total = query.count()
    rows = (
        query.order_by(
            ModelCallLog.created_at.desc(),
            ModelCallLog.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "status": row.status,
                "latency_ms": row.latency_ms,
                "error_summary": safe_log_error(row.error_message),
                "created_at": iso_utc(row.created_at),
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
