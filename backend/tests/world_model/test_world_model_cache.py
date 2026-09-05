"""世界模型读缓存：键作用域/失效/命中与写路径 bump 语义。

不依赖真实 Redis：用假客户端验证缓存行为；conftest 已将
WORLD_MODEL_CACHE_ENABLED 置 false，本文件的用例按需显式开启。
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import event

from app.shared import redis_cache
from app.world_model import cache as wm_cache


class FakeClient:
    def __init__(self):
        self.store: dict = {}
        self.bumps: list = []

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def incr(self, key):
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        self.bumps.append(key)
        return int(self.store[key])


class QueryCounter:
    """统计测试会话引擎上实际执行的 SQL 条数（上下文管理器）。"""

    def __init__(self, session):
        self._session = session
        self.count = 0

    def _incr(self, *args, **kwargs):
        self.count += 1

    def __enter__(self):
        event.listen(self._session.get_bind(), "before_cursor_execute", self._incr)
        return self

    def __exit__(self, *exc):
        event.remove(self._session.get_bind(), "before_cursor_execute", self._incr)


BASE = "/api/v2/world-model"


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(redis_cache, "_client", client)
    monkeypatch.setattr(redis_cache, "_client_failed_at", 0.0)
    return client


@pytest.fixture
def cache_on(monkeypatch):
    monkeypatch.setattr(wm_cache, "_enabled", lambda: True)


def _create_project(client, auth_headers, name: str) -> dict:
    r = client.post(
        f"{BASE}/projects",
        json={"name": name, "description": "缓存测试", "engine_type": "statistical"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_list_cache_keys_scope_params(fake_client, cache_on):
    projects = {
        wm_cache.projects_cache_key("", "", 1, 100),
        wm_cache.projects_cache_key("负荷", "", 1, 100),
        wm_cache.projects_cache_key("", "statistical", 1, 100),
        wm_cache.projects_cache_key("", "", 2, 100),
    }
    assert len(projects) == 4
    calls = {
        wm_cache.calls_cache_key("", "all", "", None, None, 1, 20),
        wm_cache.calls_cache_key("", "failed", "", None, None, 1, 20),
        wm_cache.calls_cache_key("", "all", "svc-1", None, None, 1, 20),
        wm_cache.calls_cache_key(
            "", "all", "", datetime(2026, 1, 1), None, 1, 20),
        wm_cache.calls_cache_key("", "all", "", None, None, 2, 20),
    }
    assert len(calls) == 5
    # 概览/趋势为全局单键（daily 按天数分键）。
    assert wm_cache.services_overview_cache_key() == f"ob:wm:services-overview:v0"
    assert wm_cache.calls_daily_cache_key(14) != wm_cache.calls_daily_cache_key(30)


def test_cache_key_changes_after_bump(fake_client, cache_on):
    before = wm_cache.services_overview_cache_key()
    wm_cache.invalidate_world_model()
    assert wm_cache.services_overview_cache_key() != before
    assert "ob:wm:ver" in fake_client.bumps


def test_cached_call_hit_and_miss(fake_client, cache_on):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"items": calls["n"]}

    key = "ob:wm:test:hit"
    assert wm_cache.cached_call(key, 30, builder) == {"items": 1}
    assert wm_cache.cached_call(key, 30, builder) == {"items": 1}
    assert calls["n"] == 1
    assert key in fake_client.store


def test_cached_call_bypasses_when_disabled(fake_client):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"items": calls["n"]}

    wm_cache.cached_call("ob:wm:test:off", 30, builder)
    wm_cache.cached_call("ob:wm:test:off", 30, builder)
    assert calls["n"] == 2
    assert fake_client.store == {}


def test_cached_call_skips_backfill_for_oversized_payload(fake_client, cache_on):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"blob": "x" * (wm_cache.RESPONSE_MAX_BYTES + 1)}

    key = "ob:wm:test:big"
    wm_cache.cached_call(key, 30, builder)
    wm_cache.cached_call(key, 30, builder)
    assert calls["n"] == 2
    assert key not in fake_client.store


def test_projects_endpoint_hits_cache_on_second_call(
    client, db, auth_headers, fake_client, cache_on
):
    _create_project(client, auth_headers, "项目A")
    with QueryCounter(db) as first:
        r1 = client.get(f"{BASE}/projects", headers=auth_headers)
    with QueryCounter(db) as second:
        r2 = client.get(f"{BASE}/projects", headers=auth_headers)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert r2.json()["data"]["total"] == 1
    assert second.count <= 2


def test_services_overview_endpoint_hits_cache_on_second_call(
    client, db, auth_headers, fake_client, cache_on
):
    with QueryCounter(db) as first:
        r1 = client.get(f"{BASE}/services/overview", headers=auth_headers)
    with QueryCounter(db) as second:
        r2 = client.get(f"{BASE}/services/overview", headers=auth_headers)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert wm_cache.services_overview_cache_key() in fake_client.store
    assert second.count <= 2


def test_calls_daily_endpoint_hits_cache_on_second_call(
    client, db, auth_headers, fake_client, cache_on
):
    with QueryCounter(db) as first:
        r1 = client.get(f"{BASE}/calls/daily", params={"days": 14}, headers=auth_headers)
    with QueryCounter(db) as second:
        r2 = client.get(f"{BASE}/calls/daily", params={"days": 14}, headers=auth_headers)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert wm_cache.calls_daily_cache_key(14) in fake_client.store
    assert second.count <= 2


def test_create_and_update_project_bump_version(
    client, auth_headers, fake_client, cache_on
):
    project = _create_project(client, auth_headers, "失效接线项目")
    assert "ob:wm:ver" in fake_client.bumps
    r = client.patch(
        f"{BASE}/projects/{project['id']}",
        json={"description": "改描述触发换键"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert fake_client.bumps.count("ob:wm:ver") >= 2


def test_projects_list_reflects_creation_after_bump(
    client, auth_headers, fake_client, cache_on
):
    r1 = client.get(f"{BASE}/projects", headers=auth_headers)
    assert r1.json()["data"]["total"] == 0
    _create_project(client, auth_headers, "可见性项目")
    r2 = client.get(f"{BASE}/projects", headers=auth_headers)
    assert r2.json()["data"]["total"] == 1
