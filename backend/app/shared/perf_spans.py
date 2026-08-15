"""Cross-cutting performance-span primitives for request observability.

A request-scoped ContextVar carries a mutable span bag.  The
platform.observability middleware owns the bag lifecycle; instrumented
sites (LLM gateway, database engine events) only append tiny entries.  The
bag is a mutable list, so mutations made inside Starlette worker threads
(sync def handlers run through anyio.to_thread, which copies the context
by reference) remain visible to the middleware task.

Everything here must stay allocation-light on the hot path: when no request
bag is active every helper is a contextvar read plus a cheap branch.
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any, Final

MAX_SPANS_PER_REQUEST: Final[int] = 500

#: Layer names persisted in the slow-request breakdown JSON.
_LAYER_ORDER: Final[tuple[str, ...]] = ("db", "llm", "http")

_span_bag: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "api_perf_span_bag", default=None
)
_db_stack: ContextVar[list[float] | None] = ContextVar(
    "api_perf_db_stack", default=None
)


def begin_request() -> None:
    """Open a fresh span bag for the current request context."""
    _span_bag.set([])
    _db_stack.set([])


def end_request() -> list[dict[str, Any]]:
    """Close the bag and return accumulated spans."""
    spans = _span_bag.get() or []
    _span_bag.set(None)
    _db_stack.set(None)
    return spans


def record_span(layer: str, elapsed_ms: float, *, count: int = 1) -> None:
    """Append a span if a request bag is active. Never raises."""
    bag = _span_bag.get()
    if bag is None:
        return
    try:
        if len(bag) < MAX_SPANS_PER_REQUEST:
            bag.append(
                {"layer": layer, "total_ms": int(round(elapsed_ms)), "count": count}
            )
    except Exception:  # noqa: BLE001 — observability must not break requests
        pass


def summarize_spans(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse raw spans into {layer: {count, total_ms}} for persistence."""
    summary: dict[str, dict[str, int]] = {}
    for span in spans:
        layer = span.get("layer")
        if not isinstance(layer, str) or layer not in _LAYER_ORDER:
            continue
        entry = summary.setdefault(layer, {"count": 0, "total_ms": 0})
        entry["count"] += int(span.get("count") or 0)
        entry["total_ms"] += int(span.get("total_ms") or 0)
    return summary


# --------------------------------------------------------------------------
# SQLAlchemy engine events: count SQL statements and time their execution.
# Listeners are no-ops unless a request bag is currently active.
# --------------------------------------------------------------------------
_engine_listeners_installed = False


def _db_before(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
    stack = _db_stack.get()
    if stack is None:
        return
    stack.append(time.monotonic())


def _db_after(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
    stack = _db_stack.get()
    if not stack:
        return
    started = stack.pop()
    record_span("db", (time.monotonic() - started) * 1000.0)


def install_db_span_listeners(engine) -> None:
    """Attach the engine event listeners exactly once."""
    global _engine_listeners_installed
    if _engine_listeners_installed:
        return
    from sqlalchemy import event

    event.listen(engine, "before_cursor_execute", _db_before)
    event.listen(engine, "after_cursor_execute", _db_after)
    _engine_listeners_installed = True
