"""平台运行健康度（API 性能监控）域测试。

覆盖：纯函数（状态类别/直方图/百分位）、中间件（request_id、路由模板
聚合、排除清单、慢请求落库）、admin 权限、查询聚合与 OpenAPI 契约。
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest

from app.database import SessionLocal
from app.shared import perf_spans
from app.shared.perf_spans import http_target, parse_breakdown, serialize_spans
from app.platform.observability import collector as perf_collector
from app.platform.observability.collector import (
    BUCKET_COUNT,
    bucket_index,
    percentile_from_buckets,
    status_class_of,
)
from app.platform.observability.models import ApiPerfMinuteRollup, ApiPerfSlowRequest
from app.config import settings


@pytest.fixture(autouse=True)
def _clear_buffer():
    perf_collector.collector._rollups.clear()
    yield
    perf_collector.collector._rollups.clear()


# ──────────────────────────── 纯函数 ────────────────────────────


def test_status_class_of():
    assert status_class_of(200) == "2xx"
    assert status_class_of(301) == "3xx"
    assert status_class_of(404) == "4xx"
    assert status_class_of(500) == "5xx"
    assert status_class_of(0) == ""
    assert status_class_of(99) == ""


def test_bucket_index_edges():
    assert bucket_index(0) == 0
    assert bucket_index(49) == 0
    assert bucket_index(50) == 1
    assert bucket_index(999) == 4
    assert bucket_index(1000) == 5
    assert bucket_index(29999) == 8
    assert bucket_index(30000) == 9
    assert bucket_index(10 ** 9) == 9


def test_percentile_from_buckets():
    assert percentile_from_buckets([0] * BUCKET_COUNT, 95) is None
    counts = [0] * BUCKET_COUNT
    counts[0] = 10  # all samples inside [0, 50)
    assert 0 <= percentile_from_buckets(counts, 95) <= 50
    counts = [0] * BUCKET_COUNT
    counts[9] = 1  # open-ended top bucket reports its lower edge
    assert percentile_from_buckets(counts, 95) == 30000
    counts = [0] * BUCKET_COUNT
    counts[5] = 100  # [1000, 2000)
    value = percentile_from_buckets(counts, 50)
    assert 1000 <= value < 2000


def test_resolve_username_uses_user_lookup(monkeypatch):
    from app.platform.observability.collector import resolve_username

    class FakeUser:
        username = "alice"

    monkeypatch.setattr(
        "app.auth.service.decode_token",
        lambda token: {"sub": "user-1"},
    )
    monkeypatch.setattr(
        "app.auth.service.get_user_by_id",
        lambda db, user_id: FakeUser() if user_id == "user-1" else None,
    )
    assert resolve_username("Bearer token-1") == "alice"
    assert resolve_username("") == ""
    assert resolve_username("NotBearer x") == ""


def test_resolve_username_falls_back_to_subject(monkeypatch):
    from app.platform.observability.collector import resolve_username

    monkeypatch.setattr(
        "app.auth.service.decode_token",
        lambda token: {"sub": "user-2"},
    )
    monkeypatch.setattr(
        "app.auth.service.get_user_by_id",
        lambda db, user_id: None,
    )
    assert resolve_username("Bearer token-2") == "user-2"


# ──────────────────────────── 中间件 ────────────────────────────


def test_middleware_adds_request_id_header(client, auth_headers):
    r = client.get("/api/v1/overview/stats", headers=auth_headers)
    assert r.status_code == 200
    request_id = r.headers.get("x-request-id")
    assert request_id and len(request_id) == 32


def test_middleware_records_route_template_rollup(client, auth_headers):
    client.get("/api/v1/overview/stats", headers=auth_headers)
    keys = list(perf_collector.collector._rollups.keys())
    recorded = [
        (method, route, status_class)
        for (_minute, method, route, status_class) in keys
    ]
    assert ("GET", "/api/v1/overview/stats", "2xx") in recorded


def test_health_and_monitoring_paths_are_excluded(client):
    before = dict(perf_collector.collector._rollups)
    client.get("/health/live")
    client.get("/health")
    assert list(perf_collector.collector._rollups.keys()) == list(before.keys())


def test_monitoring_endpoints_exclude_themselves(client, auth_headers):
    client.get("/api/v1/settings/monitoring/overview", headers=auth_headers)
    keys = list(perf_collector.collector._rollups.keys())
    assert not any("/settings/monitoring" in route for (_m, _me, route, _s) in keys)


def test_flush_persists_rollup_rows(client, auth_headers):
    client.get("/api/v1/overview/stats", headers=auth_headers)
    written = perf_collector.collector.flush()
    assert written >= 1
    db = SessionLocal()
    try:
        rows = (
            db.query(ApiPerfMinuteRollup)
            .filter(ApiPerfMinuteRollup.route == "/api/v1/overview/stats")
            .all()
        )
        assert rows
        total = sum(row.count for row in rows)
        buckets = [sum(getattr(row, f"bucket_{i}") for row in rows) for i in range(BUCKET_COUNT)]
        assert total >= 1
        assert sum(buckets) == total
    finally:
        db.close()


def test_slow_request_persisted_with_breakdown(client, monkeypatch, auth_headers):
    monkeypatch.setattr(settings, "api_perf_slow_threshold_ms", 0)
    r = client.get("/api/v1/overview/stats", headers=auth_headers)
    request_id = r.headers.get("x-request-id")
    db = SessionLocal()
    try:
        rows = (
            db.query(ApiPerfSlowRequest)
            .filter(ApiPerfSlowRequest.route == "/api/v1/overview/stats")
            .order_by(ApiPerfSlowRequest.id.desc())
            .limit(5)
            .all()
        )
        assert rows
        latest = rows[0]
        assert latest.request_id == request_id
        assert latest.duration_ms >= 0
        assert latest.status_code == 200
        assert isinstance(latest.breakdown, str)
    finally:
        db.close()


# ──────────────────────────── 权限 ────────────────────────────


def test_monitoring_endpoints_require_admin(client):
    r = client.get("/api/v1/settings/monitoring/overview")
    assert r.status_code == 403


def test_monitoring_endpoints_deny_editor(client, editor_user):
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "editor", "password": "editor123"},
    )
    token = login.json()["data"]["access_token"]
    r = client.get(
        "/api/v1/settings/monitoring/overview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


def test_monitoring_overview_returns_envelope(client, auth_headers):
    r = client.get("/api/v1/settings/monitoring/overview", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["window"] == "24h"
    assert data["requests"] == 0
    assert data["success_rate"] == 100.0
    assert data["slow_threshold_ms"] == 1000


# ──────────────────────────── 查询聚合 ────────────────────────────


def _add_rollup(db, *, minutes_ago=1, route="/api/v1/domains", method="GET",
                status_class="2xx", count=1, durations=None):
    durations = durations or [120]
    minute_ts = perf_collector.minute_bucket(perf_collector.utc_now()) - timedelta(
        minutes=minutes_ago
    )
    row = ApiPerfMinuteRollup(
        minute_ts=minute_ts,
        method=method,
        route=route,
        status_class=status_class,
        count=count,
        total_ms=sum(durations),
        max_ms=max(durations),
    )
    for duration in durations:
        bucket = f"bucket_{bucket_index(duration)}"
        setattr(row, bucket, (getattr(row, bucket) or 0) + 1)
    db.add(row)
    db.commit()
    return row


def test_overview_aggregates_rollups(client, db, auth_headers):
    _add_rollup(db, route="/api/v1/domains", status_class="2xx", count=2, durations=[80, 120])
    _add_rollup(db, route="/api/v1/domains", status_class="5xx", count=1, durations=[1500])
    r = client.get("/api/v1/settings/monitoring/overview", headers=auth_headers)
    data = r.json()["data"]
    assert data["requests"] == 3
    assert data["server_error_rate"] == round(1 * 100 / 3, 1)
    assert data["success_rate"] == round(2 * 100 / 3, 1)
    assert data["p50_ms"] is not None


def test_top_routes_orders_by_slow_count(client, db, auth_headers):
    _add_rollup(db, route="/api/v1/ontologies", count=5, durations=[200] * 5)
    _add_rollup(db, route="/api/v2/exploration/sessions", count=2, durations=[2500, 3000])
    slow = ApiPerfSlowRequest(
        created_at=perf_collector.utc_now(),
        method="POST",
        route="/api/v2/exploration/sessions",
        status_code=200,
        duration_ms=2500,
        request_id=uuid.uuid4().hex,
    )
    db.add(slow)
    db.commit()
    r = client.get(
        "/api/v1/settings/monitoring/top",
        params={"window": "24h", "sort_by": "slow_count"},
        headers=auth_headers,
    )
    items = r.json()["data"]["items"]
    assert items[0]["route"] == "/api/v2/exploration/sessions"
    assert items[0]["slow_count"] == 1
    assert items[0]["max_ms"] == 3000


def test_trend_returns_series(client, db, auth_headers):
    _add_rollup(db, route="/api/v1/domains", count=3, durations=[50, 60, 70])
    r = client.get(
        "/api/v1/settings/monitoring/trend",
        params={"window": "24h"},
        headers=auth_headers,
    )
    data = r.json()["data"]
    assert data["window"] == "24h"
    assert len(data["points"]) > 0
    assert any(point["count"] == 3 for point in data["points"])


def test_slow_requests_listing_and_breakdown(client, db, auth_headers):
    slow = ApiPerfSlowRequest(
        created_at=perf_collector.utc_now(),
        method="POST",
        route="/api/v2/super-assistant/conversations/x/chat",
        status_code=200,
        duration_ms=4200,
        request_id=uuid.uuid4().hex,
        username="admin",
        source_ip="10.0.0.8",
        user_agent="pytest",
        breakdown='{"llm": {"count": 1, "total_ms": 3800}}',
    )
    db.add(slow)
    db.commit()
    r = client.get(
        "/api/v1/settings/monitoring/slow-requests",
        params={"route": "super-assistant"},
        headers=auth_headers,
    )
    data = r.json()["data"]
    assert data["total"] == 1
    item = data["items"][0]
    assert item["duration_ms"] == 4200
    assert item["username"] == "admin"
    assert item["breakdown"] == {"llm": {"count": 1, "total_ms": 3800}}


# ──────────────────────────── OpenAPI 契约 ────────────────────────────


def test_monitoring_openapi_operations_exist():
    from app.main import app

    paths = app.openapi()["paths"]
    expected = {
        "/api/v1/settings/monitoring/overview",
        "/api/v1/settings/monitoring/trend",
        "/api/v1/settings/monitoring/top",
        "/api/v1/settings/monitoring/slow-requests",
    }
    assert expected <= set(paths)
    for path in expected:
        assert "get" in paths[path]


# ──────────────────────────── 调用链 span 机制 ────────────────────────────


def test_span_lifecycle_offsets_and_idempotent_end():
    perf_spans.begin_request()
    spans = []
    try:
        span = perf_spans.begin_span("http", name="GET", target="https://example.com/x")
        assert span is not None
        assert span["seq"] == 1
        assert span["start_ms"] >= 0
        assert span["duration_ms"] is None
        perf_spans.end_span(span, status="200", detail="ok")
        duration = span["duration_ms"]
        assert duration is not None and duration >= 0
        perf_spans.end_span(span, status="999")
        assert span["duration_ms"] == duration
        assert span["status"] == "200"
        spans = perf_spans.end_request()
    finally:
        perf_spans.end_request()
    assert len(spans) == 1
    assert spans[0]["name"] == "GET"


def test_begin_span_without_request_bag_is_noop():
    assert perf_spans.begin_span("db", name="SELECT") is None
    perf_spans.end_span(None, status="x")  # must not raise


def test_db_listener_records_real_elapsed_and_sql(monkeypatch):
    import time as _time

    perf_spans.begin_request()
    _time.sleep(0.06)  # 让请求偏移超过伪造的查询耗时，避免起点被钳制为 0
    spans = []
    try:
        slot = perf_spans._db_stack_slot()
        assert slot is not None
        slot.append((_time.monotonic() - 0.05, "SELECT * FROM domains WHERE id = ?"))
        perf_spans._db_after(None, None, "SELECT * FROM domains WHERE id = ?", None, None, False)
        spans = perf_spans.end_request()
    finally:
        perf_spans.end_request()
    assert len(spans) == 1
    db_span = spans[0]
    assert db_span["layer"] == "db"
    assert db_span["name"] == "SELECT"
    assert db_span["target"] == "domains"
    assert db_span["duration_ms"] >= 40  # 真实耗时，而非 0
    assert db_span["start_ms"] >= 0
    assert db_span["detail"].startswith("SELECT * FROM domains")




def test_legacy_record_span_keeps_layer_summary():
    perf_spans.begin_request()
    try:
        perf_spans.record_span("llm", 123.4, count=2)
        spans = perf_spans.end_request()
    finally:
        perf_spans.end_request()
    summary = perf_spans.summarize_spans(spans)
    assert summary["llm"]["count"] == 2
    assert summary["llm"]["total_ms"] == 246


def test_sql_signature_extracts_operation_and_table():
    assert perf_spans._sql_signature("  SELECT * FROM public.ontologies o") == (
        "SELECT", "public.ontologies",
    )
    assert perf_spans._sql_signature("UPDATE domains SET name=$1") == ("UPDATE", "domains")
    assert perf_spans._sql_signature("INSERT INTO aiot.news (title) VALUES (%s)") == (
        "INSERT", "aiot.news",
    )
    assert perf_spans._sql_signature("") == ("SQL", "")


def test_detail_is_single_line_and_truncated():
    perf_spans.begin_request()
    try:
        span = perf_spans.begin_span("db", name="SELECT", target="t")
        raw = "SELECT col\nFROM t\nWHERE x = 1 " + "y" * 600
        perf_spans.end_span(span, detail=raw)
        spans = perf_spans.end_request()
    finally:
        perf_spans.end_request()
    detail = spans[0]["detail"]
    assert "\n" not in detail
    assert len(detail) <= perf_spans.MAX_DETAIL_CHARS
    assert detail.startswith("SELECT col FROM t WHERE x = 1")


def test_serialize_spans_layout_and_backward_compat():
    perf_spans.begin_request()
    try:
        span = perf_spans.begin_span("llm", name="chat.completions", target="openai/m1")
        perf_spans.end_span(span, status="success")
        spans = perf_spans.end_request()
    finally:
        perf_spans.end_request()
    parsed = parse_breakdown(serialize_spans(spans))
    assert parsed["llm"]["count"] == 1
    assert isinstance(parsed["spans"], list) and len(parsed["spans"]) == 1
    assert parsed["spans_truncated"] is False
    chain = parsed["spans"][0]
    assert chain["layer"] == "llm"
    assert chain["name"] == "chat.completions"
    assert chain["target"] == "openai/m1"
    assert chain["status"] == "success"


def test_serialize_spans_truncates_large_payloads():
    perf_spans.begin_request()
    try:
        for index in range(400):
            span = perf_spans.begin_span("db", name="SELECT", target=f"table_{index}")
            perf_spans.end_span(span, detail="x" * 390)
        spans = perf_spans.end_request()
    finally:
        perf_spans.end_request()
    parsed = parse_breakdown(serialize_spans(spans))
    assert parsed["spans_truncated"] is True
    assert len(parsed["spans"]) < 400
    # 保留的是耗时最大的 span，且仍按时间顺序排列
    starts = [s["start_ms"] for s in parsed["spans"]]
    assert starts == sorted(starts)


def test_parse_breakdown_tolerates_legacy_and_garbage():
    assert parse_breakdown('{"llm": {"count": 1, "total_ms": 100}}') == {
        "llm": {"count": 1, "total_ms": 100}
    }
    assert parse_breakdown("not-json") == {}
    assert parse_breakdown("[1, 2]") == {}
    assert parse_breakdown(None) == {}


def test_http_target_redacts_query():
    assert http_target("https://engine.internal:8000/api/kernels/x?token=abc") == (
        "https://engine.internal:8000/api/kernels/x"
    )
    assert http_target("http://127.0.0.1:5678/webhook/demo") == (
        "http://127.0.0.1:5678/webhook/demo"
    )
    assert http_target("not a url") == "not a url"


# ──────────────────────────── 端到端：慢请求携带调用链 ────────────────────────────


def test_slow_request_persists_db_span_with_sql_detail(client, monkeypatch, auth_headers, db):
    # 测试夹具的 get_db 覆盖绑定在夹具引擎上，需为其安装 span 监听器
    # （生产环境只使用 app.database.engine，中间件启动时已安装）。
    perf_spans.install_db_span_listeners(db.get_bind())
    monkeypatch.setattr(settings, "api_perf_slow_threshold_ms", 0)
    r = client.get("/api/v1/domains", headers=auth_headers)
    assert r.status_code == 200
    request_id = r.headers.get("x-request-id")
    db = SessionLocal()
    try:
        row = None
        for _ in range(30):
            row = (
                db.query(ApiPerfSlowRequest)
                .filter(ApiPerfSlowRequest.request_id == request_id)
                .first()
            )
            if row is not None:
                break
            import time as _time

            _time.sleep(0.05)
        assert row is not None
        parsed = parse_breakdown(row.breakdown)
        assert isinstance(parsed.get("spans"), list)
        db_spans = [s for s in parsed["spans"] if s.get("layer") == "db"]
        assert db_spans
        assert any(s.get("detail") for s in db_spans)
        assert all(s.get("duration_ms") is not None for s in db_spans)
        assert parsed["db"]["count"] >= len(db_spans)
    finally:
        db.close()


def test_slow_requests_api_returns_spans(client, db, auth_headers):
    breakdown = {
        "llm": {"count": 1, "total_ms": 3800},
        "spans": [
            {
                "seq": 1, "layer": "llm", "name": "chat.completions",
                "target": "openai/m1", "start_ms": 10, "duration_ms": 3800,
                "status": "success", "detail": "",
            },
        ],
        "spans_truncated": False,
    }
    slow = ApiPerfSlowRequest(
        created_at=perf_collector.utc_now(),
        method="POST",
        route="/api/v2/super-assistant/conversations/x/chat",
        status_code=200,
        duration_ms=4200,
        request_id=uuid.uuid4().hex,
        username="admin",
        source_ip="10.0.0.8",
        user_agent="pytest",
        breakdown=json.dumps(breakdown),
    )
    db.add(slow)
    db.commit()
    r = client.get(
        "/api/v1/settings/monitoring/slow-requests",
        params={"route": "super-assistant"},
        headers=auth_headers,
    )
    item = r.json()["data"]["items"][0]
    assert item["breakdown"] == {"llm": {"count": 1, "total_ms": 3800}}
    assert item["spans"][0]["name"] == "chat.completions"
    assert item["spans_truncated"] is False


def test_slow_requests_api_tolerates_legacy_breakdown(client, db, auth_headers):
    slow = ApiPerfSlowRequest(
        created_at=perf_collector.utc_now(),
        method="GET",
        route="/api/v1/domains",
        status_code=200,
        duration_ms=1500,
        request_id=uuid.uuid4().hex,
        breakdown='{"db": {"count": 3, "total_ms": 120}}',
    )
    db.add(slow)
    db.commit()
    r = client.get(
        "/api/v1/settings/monitoring/slow-requests",
        params={"route": "domains"},
        headers=auth_headers,
    )
    item = r.json()["data"]["items"][0]
    assert item["breakdown"] == {"db": {"count": 3, "total_ms": 120}}
    assert item["spans"] == []
    assert item["spans_truncated"] is False


# ──────────────────────────── 埋点站点：LLM / HTTP ────────────────────────────


def _fake_openai_client():
    class FakeMessage:
        content = "PONG"
        tool_calls = None

    class FakeChoice:
        message = FakeMessage()

    class FakeUsage:
        prompt_tokens = 1
        completion_tokens = 1

    class FakeResponse:
        choices = [FakeChoice()]
        usage = FakeUsage()

    class FakeCompletions:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        chat = FakeChat()

    return FakeClient


def test_llm_gateway_chat_records_call_chain_span(monkeypatch):
    from app.model_configs import llm_gateway

    monkeypatch.setattr("openai.OpenAI", _fake_openai_client())
    perf_spans.begin_request()
    try:
        result = llm_gateway.chat(
            {"provider": "openai", "model": "test-model"},
            [{"role": "user", "content": "ping"}],
            [],
        )
        spans = perf_spans.end_request()
    finally:
        perf_spans.end_request()
    assert result["content"] == "PONG"
    llm_spans = [s for s in spans if s.get("layer") == "llm"]
    assert len(llm_spans) == 1
    assert llm_spans[0]["name"] == "chat.completions"
    assert llm_spans[0]["target"] == "openai/test-model"
    assert llm_spans[0]["status"] == "success"


def test_super_assistant_chat_records_call_chain_span(monkeypatch):
    from app.super_assistant import provider

    monkeypatch.setattr("openai.OpenAI", _fake_openai_client())
    perf_spans.begin_request()
    try:
        result = provider.chat(
            {"provider": "openai", "model": "m1"},
            [{"role": "user", "content": "hi"}],
            [],
        )
        spans = perf_spans.end_request()
    finally:
        perf_spans.end_request()
    assert result["content"] == "PONG"
    llm_spans = [s for s in spans if s.get("layer") == "llm"]
    assert len(llm_spans) == 1
    assert llm_spans[0]["target"] == "openai/m1"


def test_outbound_request_records_http_span():
    from app.api_hub import outbound_security

    class FakeResponse:
        status_code = 200
        headers = {}

    class FakeSession:
        def request(self, method, url, **kwargs):
            return FakeResponse()

    perf_spans.begin_request()
    try:
        response = outbound_security.request_with_safe_redirects(
            FakeSession(),
            "GET",
            "https://example.com/data?token=secret123",
            validator=lambda target: target,
        )
        spans = perf_spans.end_request()
    finally:
        perf_spans.end_request()
    assert response.status_code == 200
    http_spans = [s for s in spans if s.get("layer") == "http"]
    assert len(http_spans) == 1
    assert http_spans[0]["name"] == "GET"
    assert http_spans[0]["target"] == "https://example.com/data"
    assert http_spans[0]["status"] == "200"

