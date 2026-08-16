"""Cross-cutting performance-span primitives for request observability.

A request-scoped ContextVar carries a mutable span bag.  The
platform.observability middleware owns the bag lifecycle; instrumented
sites (LLM gateways, database engine events, downstream HTTP clients) only
append tiny entries.  The bag is a mutable list, so mutations made inside
Starlette worker threads (sync def handlers run through anyio.to_thread,
which copies the context by reference) remain visible to the middleware
task.

Spans are the raw material of the per-request call chain (调用链) shown on
the monitoring page.  Each span is an ordered, offset-timestamped record
with layer / name / target / status / detail:

    {"seq": 3, "layer": "db", "name": "SELECT", "target": "ontologies",
     "start_ms": 12, "duration_ms": 240, "status": "", "detail": "SELECT ..."}

Only slow requests persist their spans; every other request discards the
bag at the end.  Everything here must stay allocation-light on the hot
path: when no request bag is active every helper is a contextvar read plus
a cheap branch.
"""
from __future__ import annotations

import json
import re
import threading
import time
from contextvars import ContextVar
from typing import Any, Final
from urllib.parse import urlsplit

MAX_SPANS_PER_REQUEST: Final[int] = 500
MAX_DETAIL_CHARS: Final[int] = 400
MAX_TARGET_CHARS: Final[int] = 200
MAX_NAME_CHARS: Final[int] = 120
#: Upper bound for the serialized breakdown JSON (worst-case row payload).
MAX_BREAKDOWN_BYTES: Final[int] = 128 * 1024

#: Layer names persisted in the slow-request breakdown JSON.
_LAYER_ORDER: Final[tuple[str, ...]] = ("db", "llm", "http")

_span_bag: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "api_perf_span_bag", default=None
)
_request_start: ContextVar[float | None] = ContextVar(
    "api_perf_request_start", default=None
)
#: Per-thread stack of (monotonic_start, statement) for in-flight statements.
_db_stack: ContextVar[dict[int, list[tuple[float, str]]] | None] = ContextVar(
    "api_perf_db_stack", default=None
)

_SPACE_RUN = re.compile(r"\s+")
_SQL_OP = re.compile(r"^\s*([A-Za-z]+)")
_TABLE_HINT = re.compile(
    r"\b(?:from|join|into|update)\s+([\w.\x60\"\[\]]+)", re.IGNORECASE
)


def begin_request() -> None:
    """Open a fresh span bag for the current request context."""
    _span_bag.set([])
    _db_stack.set({})
    _request_start.set(time.monotonic())


def end_request() -> list[dict[str, Any]]:
    """Close the bag and return accumulated spans."""
    spans = _span_bag.get() or []
    _span_bag.set(None)
    _db_stack.set(None)
    _request_start.set(None)
    return spans


def _now_offset_ms() -> int:
    """Milliseconds since the request started (0 when outside a request)."""
    started = _request_start.get()
    if started is None:
        return 0
    return max(0, int((time.monotonic() - started) * 1000))


def _clean_detail(detail: Any) -> str:
    text = str(detail or "")
    return _SPACE_RUN.sub(" ", text).strip()[:MAX_DETAIL_CHARS]


def begin_span(layer: str, name: str = "", target: str = "") -> dict[str, Any] | None:
    """Open a span on the active request bag; None when no bag is active.

    Never raises.  The returned dict is closed with
    :func:`end_span` (idempotent) and must never be mutated by callers.
    """
    bag = _span_bag.get()
    if bag is None:
        return None
    try:
        span: dict[str, Any] = {
            "seq": len(bag) + 1,
            "layer": str(layer or "")[:16],
            "name": str(name or "")[:MAX_NAME_CHARS],
            "target": str(target or "")[:MAX_TARGET_CHARS],
            "start_ms": _now_offset_ms(),
            "duration_ms": None,
            "status": "",
            "detail": "",
        }
        if len(bag) < MAX_SPANS_PER_REQUEST:
            bag.append(span)
            return span
    except Exception:  # noqa: BLE001 — observability must not break requests
        pass
    return None


def end_span(
    span: dict[str, Any] | None,
    *,
    status: str = "",
    detail: str = "",
) -> None:
    """Close a span, freezing its duration/status/detail. Idempotent, never raises."""
    if not span:
        return
    try:
        if span.get("duration_ms") is not None:
            return
        span["duration_ms"] = max(0, _now_offset_ms() - int(span.get("start_ms") or 0))
        span["status"] = str(status or "")[:24]
        span["detail"] = _clean_detail(detail)
    except Exception:  # noqa: BLE001
        pass


def record_span(layer: str, elapsed_ms: float, *, count: int = 1) -> None:
    """Legacy aggregate-only append for existing call sites.

    New call sites should prefer begin_span/end_span so the call chain
    carries name/target/status/detail.  Never raises.
    """
    bag = _span_bag.get()
    if bag is None:
        return
    try:
        duration = max(0, int(round(float(elapsed_ms or 0))))
        room = max(0, MAX_SPANS_PER_REQUEST - len(bag))
        for _ in range(max(0, min(int(count or 1), room))):
            bag.append(
                {
                    "seq": len(bag) + 1,
                    "layer": str(layer or "")[:16],
                    "name": "",
                    "target": "",
                    "start_ms": max(0, _now_offset_ms() - duration),
                    "duration_ms": duration,
                    "status": "",
                    "detail": "",
                }
            )
    except Exception:  # noqa: BLE001
        pass


def summarize_spans(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse raw spans into {layer: {count, total_ms}} for persistence."""
    summary: dict[str, dict[str, int]] = {}
    for span in spans:
        layer = span.get("layer")
        if not isinstance(layer, str) or layer not in _LAYER_ORDER:
            continue
        entry = summary.setdefault(layer, {"count": 0, "total_ms": 0})
        entry["count"] += int(span.get("count") or 1)
        entry["total_ms"] += int(
            span.get("duration_ms") if span.get("duration_ms") is not None
            else span.get("total_ms") or 0
        )
    return summary


def _span_to_json(span: dict[str, Any]) -> dict[str, Any]:
    """Normalize one span to the persisted JSON shape."""
    return {
        "seq": int(span.get("seq") or 0),
        "layer": str(span.get("layer") or "")[:16],
        "name": str(span.get("name") or "")[:MAX_NAME_CHARS],
        "target": str(span.get("target") or "")[:MAX_TARGET_CHARS],
        "start_ms": max(0, int(span.get("start_ms") or 0)),
        "duration_ms": max(0, int(span.get("duration_ms") or 0)),
        "status": str(span.get("status") or "")[:24],
        "detail": str(span.get("detail") or "")[:MAX_DETAIL_CHARS],
    }


def _fit_spans(
    ordered: list[dict[str, Any]], budget_bytes: int
) -> tuple[list[dict[str, Any]], bool]:
    """Keep as many spans as fit the byte budget, preferring slowest spans."""
    kept: list[dict[str, Any]] = []
    size = 0
    truncated = False
    by_duration = sorted(
        ordered, key=lambda s: (int(s.get("duration_ms") or 0), int(s.get("seq") or 0)),
        reverse=True,
    )
    for span in by_duration:
        payload = _span_to_json(span)
        added = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) + 1
        if size + added > budget_bytes:
            truncated = True
            continue
        kept.append(payload)
        size += added
    kept.sort(key=lambda s: (s["start_ms"], s["seq"]))
    return kept, truncated


def serialize_spans(spans: list[dict[str, Any]]) -> str:
    """Serialize the breakdown JSON persisted in the slow-request row.

    Layout: legacy per-layer aggregates ({layer: {count, total_ms}}) plus an
    ordered `spans` array and a `spans_truncated` flag, so old readers
    keep working while new readers can render the call chain.
    """
    summary = summarize_spans(spans)
    ordered = sorted(
        spans, key=lambda s: (int(s.get("start_ms") or 0), int(s.get("seq") or 0))
    )
    aggregate_bytes = len(
        json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
    )
    budget = max(1, MAX_BREAKDOWN_BYTES - aggregate_bytes - 64)
    kept, truncated = _fit_spans(ordered, budget)
    payload: dict[str, Any] = dict(summary)
    payload["spans"] = kept
    payload["spans_truncated"] = truncated
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def parse_breakdown(raw: str | None) -> dict[str, Any]:
    """Parse a persisted breakdown JSON, tolerating legacy/malformed rows."""
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def http_target(url: str) -> str:
    """Redacted HTTP target for span attribution: scheme://host/path, no query."""
    try:
        parts = urlsplit(url or "")
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"[:MAX_TARGET_CHARS]
    except ValueError:
        pass
    return str(url or "")[:MAX_TARGET_CHARS]


# --------------------------------------------------------------------------
# SQLAlchemy engine events: count SQL statements, time their execution and
# capture a truncated statement signature for the call chain.  Listeners are
# no-ops unless a request bag is currently active.
# --------------------------------------------------------------------------
#: Engines (by id) that already carry the span listeners.
_engines_with_listeners: set[int] = set()


def _db_stack_slot() -> list[tuple[float, str]] | None:
    stacks = _db_stack.get()
    if stacks is None:
        return None
    return stacks.setdefault(threading.get_ident(), [])


def _sql_signature(statement: str) -> tuple[str, str]:
    """Best-effort (operation, table) from a SQL statement for span naming."""
    text = _SPACE_RUN.sub(" ", statement or "").strip()
    op_match = _SQL_OP.match(text)
    operation = (op_match.group(1).upper() if op_match else "SQL")[:16]
    table = ""
    hint = _TABLE_HINT.search(text)
    if hint:
        table = hint.group(1).strip("\x60" + '"[]')[:MAX_TARGET_CHARS]
    return operation, table


def _db_before(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
    slot = _db_stack_slot()
    if slot is None:
        return
    slot.append((time.monotonic(), str(statement or "")))


def _db_after(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
    stacks = _db_stack.get()
    if not stacks:
        return
    slot = stacks.get(threading.get_ident())
    if not slot:
        return
    started, sql = slot.pop()
    if not slot:
        stacks.pop(threading.get_ident(), None)
    operation, table = _sql_signature(sql)
    span = begin_span("db", name=operation, target=table)
    if span is None:
        return
    # begin_span 在查询结束后才被创建，这里回填查询真实起点偏移，
    # 使 end_span 计算出的 duration 等于实际执行耗时。
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    span["start_ms"] = max(0, _now_offset_ms() - elapsed_ms)
    end_span(span, detail=sql)


def install_db_span_listeners(engine) -> None:
    """Attach the engine event listeners exactly once per engine."""
    if id(engine) in _engines_with_listeners:
        return
    from sqlalchemy import event

    event.listen(engine, "before_cursor_execute", _db_before)
    event.listen(engine, "after_cursor_execute", _db_after)
    _engines_with_listeners.add(id(engine))
