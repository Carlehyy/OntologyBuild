"""平台运行健康度（API 性能监控）域测试。

覆盖：纯函数（状态类别/直方图/百分位）、中间件（request_id、路由模板
聚合、排除清单、慢请求落库）、admin 权限、查询聚合与 OpenAPI 契约。
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.database import SessionLocal
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

