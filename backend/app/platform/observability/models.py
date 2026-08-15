"""Persisted schema for API performance monitoring (平台运行健康度).

Minute rollups are insert-only: query-time aggregation merges rows from
multiple API replicas.  Histogram buckets are plain integer columns so both
PostgreSQL and SQLite can SUM them.  Slow requests are single-row evidence
records that also carry the per-layer breakdown captured by the span
instrumentation.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApiPerfMinuteRollup(Base):
    __tablename__ = "api_perf_minute_rollups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    minute_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    route: Mapped[str] = mapped_column(String(512), nullable=False)
    status_class: Mapped[str] = mapped_column(String(8), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    max_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Histogram bucket counts; edges in collector.HISTOGRAM_EDGES_MS
    # (bucket i covers [edge[i-1], edge[i]) except the open-ended last one).
    bucket_0: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bucket_1: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bucket_2: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bucket_3: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bucket_4: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bucket_5: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bucket_6: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bucket_7: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bucket_8: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bucket_9: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ApiPerfSlowRequest(Base):
    __tablename__ = "api_perf_slow_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    route: Mapped[str] = mapped_column(String(512), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    username: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source_ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # JSON object: {layer: {count, total_ms}} for db/llm/http spans.
    breakdown: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

