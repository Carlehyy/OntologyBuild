"""Query service for the platform performance monitoring page.

All aggregation happens at query time: rollup rows from multiple API
replicas are merged in SQL (portable SUMs across PostgreSQL and SQLite),
and percentiles are estimated from the histogram bucket columns stored in
every rollup row.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.platform.observability.collector import (
    BUCKET_COLUMNS,
    BUCKET_COUNT,
    percentile_from_buckets,
    utc_now,
)
from app.platform.observability.models import ApiPerfMinuteRollup, ApiPerfSlowRequest
from app.shared.perf_spans import parse_breakdown

WINDOW_OPTIONS = ("24h", "7d")


def _window_start(window: str) -> datetime:
    if window == "7d":
        return utc_now() - timedelta(days=7)
    return utc_now() - timedelta(hours=24)


def _parse_iso(value: str) -> datetime | None:
    """Parse a browser ISO timestamp into a naive UTC datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _bucket_row_to_counts(row, offset: int = 0) -> list[int]:
    counts = [0] * BUCKET_COUNT
    for index in range(BUCKET_COUNT):
        value = row[offset + index]
        counts[index] = int(value or 0)
    return counts


def overview(db: Session, window: str = "24h") -> dict[str, Any]:
    window = window if window in WINDOW_OPTIONS else "24h"
    start = _window_start(window)
    sums = [
        func.sum(ApiPerfMinuteRollup.count),
        func.sum(ApiPerfMinuteRollup.total_ms),
    ] + [
        func.sum(getattr(ApiPerfMinuteRollup, column))
        for column in BUCKET_COLUMNS
    ]
    row = (
        db.query(*sums)
        .filter(ApiPerfMinuteRollup.minute_ts >= start)
        .one()
    )
    total = int(row[0] or 0)
    total_ms = int(row[1] or 0)
    buckets = _bucket_row_to_counts(row, offset=2)

    status_rows = (
        db.query(
            ApiPerfMinuteRollup.status_class,
            func.sum(ApiPerfMinuteRollup.count),
        )
        .filter(ApiPerfMinuteRollup.minute_ts >= start)
        .group_by(ApiPerfMinuteRollup.status_class)
        .all()
    )
    by_class = {status_class: int(count or 0) for status_class, count in status_rows}
    ok = by_class.get("2xx", 0) + by_class.get("3xx", 0)
    client_errors = by_class.get("4xx", 0)
    server_errors = by_class.get("5xx", 0)

    slow_count = (
        db.query(func.count(ApiPerfSlowRequest.id))
        .filter(ApiPerfSlowRequest.created_at >= start)
        .scalar()
        or 0
    )
    return {
        "window": window,
        "requests": total,
        "success_rate": round(ok * 100 / total, 1) if total else 100.0,
        "client_error_rate": round(client_errors * 100 / total, 1) if total else 0.0,
        "server_error_rate": round(server_errors * 100 / total, 1) if total else 0.0,
        "avg_ms": int(total_ms / total) if total else None,
        "p50_ms": percentile_from_buckets(buckets, 50),
        "p95_ms": percentile_from_buckets(buckets, 95),
        "p99_ms": percentile_from_buckets(buckets, 99),
        "slow_requests": slow_count,
        "slow_threshold_ms": int(settings.api_perf_slow_threshold_ms),
    }


def trend(db: Session, window: str = "24h") -> dict[str, Any]:
    """Per-minute (24h) or per-hour (7d) series of count/error-rate/p95."""
    window = window if window in WINDOW_OPTIONS else "24h"
    step = timedelta(minutes=1) if window == "24h" else timedelta(hours=1)
    step_seconds = int(step.total_seconds())
    start = _window_start(window)
    points = int((utc_now() - start).total_seconds() // step_seconds) + 1
    base = start.replace(tzinfo=None)

    bucket_columns = [getattr(ApiPerfMinuteRollup, column) for column in BUCKET_COLUMNS]
    select = [
        ApiPerfMinuteRollup.minute_ts,
        ApiPerfMinuteRollup.status_class,
        func.sum(ApiPerfMinuteRollup.count),
        func.sum(ApiPerfMinuteRollup.total_ms),
    ] + [func.sum(column) for column in bucket_columns]
    rows = (
        db.query(*select)
        .filter(ApiPerfMinuteRollup.minute_ts >= start)
        .group_by(ApiPerfMinuteRollup.minute_ts, ApiPerfMinuteRollup.status_class)
        .all()
    )

    counts = [0] * points
    errors = [0] * points
    totals_ms = [0] * points
    buckets = [[0] * BUCKET_COUNT for _ in range(points)]
    for row in rows:
        minute_ts = row[0]
        status_class = row[1]
        count = int(row[2] or 0)
        index = int((minute_ts - base).total_seconds() // step_seconds)
        if index < 0 or index >= points:
            continue
        counts[index] += count
        totals_ms[index] += int(row[3] or 0)
        if status_class in {"4xx", "5xx"}:
            errors[index] += count
        for i in range(BUCKET_COUNT):
            buckets[index][i] += int(row[4 + i] or 0)

    series = []
    for index in range(points):
        point_time = base + step * index
        total = counts[index]
        series.append(
            {
                "t": point_time.isoformat() + "Z",
                "count": total,
                "avg_ms": int(totals_ms[index] / total) if total else None,
                "p95_ms": percentile_from_buckets(buckets[index], 95),
                "error_rate": round(errors[index] * 100 / total, 1) if total else 0.0,
            }
        )
    return {"window": window, "points": series}


def top_routes(
    db: Session,
    window: str = "24h",
    *,
    sort_by: str = "slow_count",
    limit: int = 20,
) -> dict[str, Any]:
    window = window if window in WINDOW_OPTIONS else "24h"
    limit = max(min(limit, 100), 1)
    start = _window_start(window)

    slow_by_route: dict[str, int] = {}
    slow_rows = (
        db.query(ApiPerfSlowRequest.route, func.count(ApiPerfSlowRequest.id))
        .filter(ApiPerfSlowRequest.created_at >= start)
        .group_by(ApiPerfSlowRequest.route)
        .all()
    )
    for route, count in slow_rows:
        slow_by_route[route] = int(count)

    bucket_columns = [getattr(ApiPerfMinuteRollup, column) for column in BUCKET_COLUMNS]
    select = [
        ApiPerfMinuteRollup.method,
        ApiPerfMinuteRollup.route,
        ApiPerfMinuteRollup.status_class,
        func.sum(ApiPerfMinuteRollup.count),
        func.sum(ApiPerfMinuteRollup.total_ms),
        func.max(ApiPerfMinuteRollup.max_ms),
    ] + [func.sum(column) for column in bucket_columns]
    rows = (
        db.query(*select)
        .filter(ApiPerfMinuteRollup.minute_ts >= start)
        .group_by(
            ApiPerfMinuteRollup.method,
            ApiPerfMinuteRollup.route,
            ApiPerfMinuteRollup.status_class,
        )
        .all()
    )

    aggregate: dict[str, dict[str, Any]] = {}
    for row in rows:
        method, route, status_class = row[0], row[1], row[2]
        count = int(row[3] or 0)
        entry = aggregate.setdefault(
            route,
            {
                "route": route,
                "method": method,
                "requests": 0,
                "errors": 0,
                "total_ms": 0,
                "max_ms": 0,
                "buckets": [0] * BUCKET_COUNT,
            },
        )
        entry["requests"] += count
        entry["total_ms"] += int(row[4] or 0)
        entry["max_ms"] = max(entry["max_ms"], int(row[5] or 0))
        if status_class in {"4xx", "5xx"}:
            entry["errors"] += count
        for i in range(BUCKET_COUNT):
            entry["buckets"][i] += int(row[6 + i] or 0)

    items = []
    for entry in aggregate.values():
        total = entry["requests"]
        items.append(
            {
                "route": entry["route"],
                "method": entry["method"],
                "requests": total,
                "error_rate": round(entry["errors"] * 100 / total, 1) if total else 0.0,
                "avg_ms": int(entry["total_ms"] / total) if total else None,
                "p95_ms": percentile_from_buckets(entry["buckets"], 95),
                "max_ms": entry["max_ms"],
                "slow_count": slow_by_route.get(entry["route"], 0),
            }
        )
    allowed = {"slow_count", "p95_ms", "avg_ms", "requests", "error_rate"}
    sort_key = sort_by if sort_by in allowed else "slow_count"
    items.sort(key=lambda item: (item.get(sort_key) or 0), reverse=True)
    return {"items": items[:limit], "total": len(items)}


def slow_requests(
    db: Session,
    *,
    start: str = "",
    end: str = "",
    route: str = "",
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    page = max(page, 1)
    size = max(min(size, 100), 1)
    query = db.query(ApiPerfSlowRequest)
    parsed_start = _parse_iso(start)
    parsed_end = _parse_iso(end)
    if parsed_start:
        query = query.filter(ApiPerfSlowRequest.created_at >= parsed_start)
    if parsed_end:
        query = query.filter(ApiPerfSlowRequest.created_at <= parsed_end)
    if route:
        query = query.filter(ApiPerfSlowRequest.route.contains(route))
    total = query.count()
    rows = (
        query.order_by(ApiPerfSlowRequest.id.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    items = []
    for row in rows:
        parsed = parse_breakdown(row.breakdown)
        spans = parsed.get("spans") if isinstance(parsed.get("spans"), list) else []
        breakdown = {
            key: value
            for key, value in parsed.items()
            if key not in ("spans", "spans_truncated")
        }
        items.append(
            {
                "id": row.id,
                "created_at": row.created_at.isoformat() + "Z"
                if row.created_at
                else None,
                "method": row.method,
                "route": row.route,
                "status_code": row.status_code,
                "duration_ms": row.duration_ms,
                "request_id": row.request_id,
                "username": row.username,
                "source_ip": row.source_ip,
                "user_agent": row.user_agent,
                "breakdown": breakdown,
                "spans": spans,
                "spans_truncated": bool(parsed.get("spans_truncated")),
            }
        )
    return {"items": items, "total": total, "page": page, "size": size}

