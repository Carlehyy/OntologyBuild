"""Request-timing middleware (pure ASGI) for API performance monitoring.

A plain ASGI wrapper is mandatory here: BaseHTTPMiddleware would buffer the
four SSE chat endpoints and break streaming.  The middleware:

* generates a request_id, echoes it as X-Request-ID and injects it into
  log records for the duration of the request;
* wraps send to capture status code and content type;
* records one rollup per matched route template per minute;
* persists slow requests (>= API_PERF_SLOW_THRESHOLD_MS) with the span
  breakdown accumulated by the instrumentation layers.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

from app.config import settings
from app.platform.observability import collector as perf_collector
from app.shared import perf_spans

logger = logging.getLogger(__name__)

_request_id_var: ContextVar[str | None] = ContextVar(
    "api_perf_request_id", default=None
)

#: Paths that never enter performance accounting.
_EXCLUDED_PATH_PREFIXES = (
    "/health",
    "/health/",
    "/api/health",
    "/api-hub/mcp",
    "/api/v1/settings/monitoring",
)

SSE_CONTENT_TYPE = "text/event-stream"


class _RequestIdLogFilter(logging.Filter):
    """Attach the current request id to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = _request_id_var.get()
        record.request_id = request_id if request_id else "-"
        return True


def _install_log_filter() -> None:
    root = logging.getLogger()
    if not any(isinstance(item, _RequestIdLogFilter) for item in root.filters):
        root.addFilter(_RequestIdLogFilter())


def _excluded(path: str, method: str) -> bool:
    if method == "OPTIONS":
        return True
    return path.startswith(_EXCLUDED_PATH_PREFIXES)


def _header_value(headers, name: bytes) -> str:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1", errors="replace")
    return ""


def _client_ip(scope: dict[str, Any], headers) -> str:
    forwarded = _header_value(headers, b"x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        return str(client[0])[:64]
    return ""


class PerfMonitoringMiddleware:
    def __init__(self, app):
        self.app = app
        _install_log_filter()
        from app.database import engine

        perf_spans.install_db_span_listeners(engine)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "")
        if not settings.api_perf_enabled or _excluded(path, method):
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        token = _request_id_var.set(request_id)
        perf_spans.begin_request()
        started = time.perf_counter()
        captured: dict[str, Any] = {
            "status_code": 0,
            "content_type": "",
            "started": False,
        }

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                captured["status_code"] = message.get("status", 0)
                captured["started"] = True
                raw_headers = message.get("headers") or []
                captured["content_type"] = _header_value(raw_headers, b"content-type")
                headers = [list(item) for item in raw_headers]
                headers.append(
                    (b"x-request-id", request_id.encode("ascii")),
                )
                message = {**message, "headers": headers}
            await send(message)

        completed_normally = False
        try:
            await self.app(scope, receive, send_wrapper)
            completed_normally = True
        finally:
            # Never return from this finally block: doing so would swallow an
            # exception still propagating from the application (e.g. a
            # RuntimeError re-raised through TestClient).  Every skip decision
            # is a nested condition instead.
            duration_ms = int((time.perf_counter() - started) * 1000)
            spans = perf_spans.end_request()
            _request_id_var.reset(token)
            if completed_normally and captured["started"]:
                if not captured["content_type"].startswith(SSE_CONTENT_TYPE):
                    route = scope.get("route")
                    route_path = getattr(route, "path", "") or ""
                    if route_path:
                        status_code = captured["status_code"]
                        status_class = perf_collector.status_class_of(status_code)
                        if status_class:
                            await self._record_request(
                                scope,
                                method,
                                route_path,
                                status_code,
                                status_class,
                                duration_ms,
                                request_id,
                                spans,
                            )

    async def _record_request(
        self,
        scope,
        method: str,
        route_path: str,
        status_code: int,
        status_class: str,
        duration_ms: int,
        request_id: str,
        spans: list[dict[str, Any]],
    ) -> None:
        now = perf_collector.utc_now()
        perf_collector.collector.record(
            minute_ts=perf_collector.minute_bucket(now),
            method=method,
            route=route_path,
            status_class=status_class,
            duration_ms=duration_ms,
        )
        threshold = int(settings.api_perf_slow_threshold_ms)
        if duration_ms >= threshold:
            raw_headers = list(scope.get("headers") or [])
            authorization = _header_value(raw_headers, b"authorization")
            record = {
                "created_at": now,
                "method": method,
                "route": route_path,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "request_id": request_id,
                "username": "",
                "source_ip": _client_ip(scope, raw_headers),
                "user_agent": _header_value(raw_headers, b"user-agent")[:512],
                "breakdown": perf_spans.serialize_spans(spans),
                "_authorization": authorization,
            }
            logger.warning(
                "slow request %s %s %sms status=%s request_id=%s",
                method,
                route_path,
                duration_ms,
                status_code,
                request_id,
            )
            try:
                await asyncio.to_thread(
                    perf_collector.collector.persist_slow, record
                )
            except Exception:  # noqa: BLE001 — must not fail the request
                logger.exception(
                    "慢请求落库失败 request_id=%s", record.get("request_id")
                )
