"""In-memory request aggregation with batched persistence.

Ordinary requests only mutate process-local counters (microsecond cost).
A background loop flushes minute rollups every API_PERF_FLUSH_INTERVAL_SECONDS
and prunes expired rows.  Slow requests are persisted synchronously (from a
worker thread) so a crash never loses single-request evidence.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Final

from sqlalchemy import delete

from app.config import settings
from app.database import SessionLocal
from app.platform.observability.models import ApiPerfMinuteRollup, ApiPerfSlowRequest

logger = logging.getLogger(__name__)

# Histogram bucket edges in milliseconds.  10 buckets, last one open-ended.
HISTOGRAM_EDGES_MS: Final[tuple[int, ...]] = (
    50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000,
)
BUCKET_COUNT: Final[int] = len(HISTOGRAM_EDGES_MS) + 1
BUCKET_COLUMNS: Final[tuple[str, ...]] = tuple(
    f"bucket_{index}" for index in range(BUCKET_COUNT)
)


def utc_now() -> datetime:
    """Return a naive UTC timestamp for storage parity with the platform DB."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def minute_bucket(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def resolve_username(authorization: str) -> str:
    """Resolve the JWT subject to a username (worker thread, slow path only).

    Falls back to the raw subject id; any failure yields an empty string.
    """
    if not authorization.startswith("Bearer "):
        return ""
    token = authorization[len("Bearer "):].strip()
    if not token:
        return ""
    try:
        from app.auth.service import decode_token, get_user_by_id
        from app.database import SessionLocal

        payload = decode_token(token)
        user_id = str(payload.get("sub") or "")
        if not user_id:
            return ""
        db = SessionLocal()
        try:
            user = get_user_by_id(db, user_id)
            return (user.username if user else user_id)[:255]
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — identity is best effort only
        return ""


def status_class_of(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    if 500 <= status_code < 600:
        return "5xx"
    return ""


def bucket_index(duration_ms: int) -> int:
    """Map a duration onto the histogram bucket index (0-based)."""
    for index, edge in enumerate(HISTOGRAM_EDGES_MS):
        if duration_ms < edge:
            return index
    return BUCKET_COUNT - 1


def percentile_from_buckets(counts: list[int], percentile: float) -> int | None:
    """Estimate a percentile from merged histogram bucket counts.

    Returns None when there are no samples.  Linear interpolation inside
    the target bucket; the open-ended top bucket is reported at its lower
    edge, which keeps estimates conservative.
    """
    total = sum(counts)
    if total <= 0:
        return None
    target = max(1, int(round(total * percentile / 100.0 + 0.5)))  # 1-based rank
    cumulative = 0
    for index, count in enumerate(counts):
        cumulative += count
        if cumulative >= target:
            lower = HISTOGRAM_EDGES_MS[index - 1] if index > 0 else 0
            if index == BUCKET_COUNT - 1:
                return lower
            upper = HISTOGRAM_EDGES_MS[index]
            in_bucket = count
            previous = cumulative - in_bucket
            fraction = (target - previous) / in_bucket if in_bucket else 0.0
            return int(round(lower + (upper - lower) * fraction))
    return None


class _PerfCollector:
    """Process-local request aggregator shared by the middleware."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rollups: "OrderedDict[tuple, list[int]]" = OrderedDict()
        self._maintenance_task: asyncio.Task | None = None

    # -- recording ------------------------------------------------------

    def record(self, *, minute_ts: datetime, method: str, route: str,
               status_class: str, duration_ms: int) -> None:
        """Accumulate one request into the in-memory rollup buffer."""
        if not settings.api_perf_enabled:
            return
        key = (minute_ts, method, route, status_class)
        index = bucket_index(duration_ms)
        with self._lock:
            entry = self._rollups.get(key)
            if entry is None:
                if len(self._rollups) >= settings.api_perf_buffer_max_rows:
                    self._rollups.popitem(last=False)  # evict the oldest minute
                entry = [1, duration_ms, duration_ms, [0] * BUCKET_COUNT]
                entry[3][index] = 1
                self._rollups[key] = entry
            else:
                entry[0] += 1
                entry[1] += duration_ms
                entry[2] = max(entry[2], duration_ms)
                entry[3][index] += 1

    # -- persistence ----------------------------------------------------

    def flush(self) -> int:
        """Persist buffered rollups.  Returns the number of rows written."""
        with self._lock:
            snapshot: list[tuple[tuple, list[int]]] = list(self._rollups.items())
            self._rollups.clear()
        if not snapshot:
            return 0
        db = SessionLocal()
        written = 0
        try:
            for (minute_ts, method, route, status_class), entry in snapshot:
                row = ApiPerfMinuteRollup(
                    minute_ts=minute_ts,
                    method=method,
                    route=route,
                    status_class=status_class,
                    count=entry[0],
                    total_ms=entry[1],
                    max_ms=entry[2],
                )
                for column, value in zip(BUCKET_COLUMNS, entry[3]):
                    setattr(row, column, value)
                db.add(row)
                written += 1
            db.commit()
        except Exception:  # noqa: BLE001 — monitoring must not break requests
            db.rollback()
            logger.exception("API 性能监控聚合落库失败")
        finally:
            db.close()
        return written

    def persist_slow(self, record: dict[str, Any]) -> None:
        """Persist one slow request row synchronously (worker thread)."""
        authorization = str(record.pop("_authorization", "") or "")
        if authorization and not record.get("username"):
            record["username"] = resolve_username(authorization)
        db = SessionLocal()
        try:
            db.add(ApiPerfSlowRequest(**record))
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("API 性能监控慢请求落库失败")
        finally:
            db.close()

    def cleanup_expired(self) -> tuple[int, int]:
        """Delete rollups and slow rows beyond their retention windows."""
        now = utc_now()
        agg_cutoff = now - timedelta(days=settings.api_perf_agg_retention_days)
        slow_cutoff = now - timedelta(days=settings.api_perf_slow_retention_days)
        db = SessionLocal()
        try:
            deleted_rollups = db.execute(
                delete(ApiPerfMinuteRollup).where(
                    ApiPerfMinuteRollup.minute_ts < agg_cutoff
                )
            ).rowcount
            deleted_slow = db.execute(
                delete(ApiPerfSlowRequest).where(
                    ApiPerfSlowRequest.created_at < slow_cutoff
                )
            ).rowcount
            db.commit()
            return deleted_rollups, deleted_slow
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("API 性能监控保留清理失败")
            return 0, 0
        finally:
            db.close()

    # -- background maintenance ----------------------------------------

    async def maintenance_loop(self) -> None:
        """Periodically flush rollups and prune expired rows."""
        interval = max(5, int(settings.api_perf_flush_interval_seconds))
        cleanup_every = max(1, 24 * 60 * 60 // interval)  # retention cleanup ~daily
        cycle = 0
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(self.flush)
                cycle += 1
                if cycle >= cleanup_every:
                    cycle = 0
                    await asyncio.to_thread(self.cleanup_expired)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — next cycle retries
                logger.exception("API 性能监控后台维护失败")

    def start(self) -> None:
        if self._maintenance_task is None or self._maintenance_task.done():
            self._maintenance_task = asyncio.create_task(self.maintenance_loop())

    async def stop(self) -> None:
        task = self._maintenance_task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


collector = _PerfCollector()
