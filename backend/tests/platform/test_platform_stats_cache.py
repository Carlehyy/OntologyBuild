"""平台概览统计读缓存与 N+1 回归：命中/直查/降级语义。

不依赖真实 Redis：用假客户端验证缓存行为；fail-open 测试在无 Redis 的
测试环境下天然走降级路径（本机若恰好有 Redis 也只会得到相同结果断言）。
conftest 已将 PLATFORM_STATS_CACHE_ENABLED 置 false，本文件的用例按需
显式开启。
"""
from __future__ import annotations

import pytest

from app.ontologies.entities.models import Entity
from app.ontologies.logic.models import LogicRule
from app.platform import cache as platform_cache
from app.shared import redis_cache
from tests.conftest import QueryCounter


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


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(redis_cache, "_client", client)
    monkeypatch.setattr(redis_cache, "_client_failed_at", 0.0)
    return client


@pytest.fixture
def stats_cache_on(monkeypatch):
    monkeypatch.setattr(platform_cache, "_enabled", lambda: True)


def test_stats_cache_key_is_global_single_key():
    assert platform_cache.stats_cache_key() == "ob:plat:stats"


def test_cached_call_bypasses_cache_when_disabled(fake_client):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"rows": calls["n"]}

    first = platform_cache.cached_call(platform_cache.stats_cache_key(), 30, builder)
    second = platform_cache.cached_call(platform_cache.stats_cache_key(), 30, builder)
    assert first == {"rows": 1}
    assert second == {"rows": 2}
    assert fake_client.store == {}


def test_stats_avoids_per_ontology_count_queries(client, db, auth_headers):
    for i in range(6):
        r = client.post(
            "/api/v1/ontologies",
            json={"name": f"统计本体{i}", "domain": "供应链"},
            headers=auth_headers,
        )
        assert r.status_code == 201
    with QueryCounter(db) as counter:
        r = client.get("/api/v1/overview/stats", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data["recent_ontologies"]) == 6
    # 1 认证 + 1 最近本体 + 3 分组计数 + 4 全表计数 + 2 分布聚合；
    # 修复前是逐本体 3 次 COUNT 的 N+1（6 本体共 25 条查询）。
    assert counter.count <= 12


def test_stats_recent_cards_match_seeded_counts(client, db, auth_headers):
    r = client.post(
        "/api/v1/ontologies",
        json={"name": "计数本体", "domain": "供应链"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    ontology_id = r.json()["data"]["id"]
    db.add_all(
        [Entity(ontology_id=ontology_id, name_cn=f"实体{i}") for i in range(2)]
    )
    db.add(LogicRule(ontology_id=ontology_id, name_cn="规则1"))
    db.commit()

    r = client.get("/api/v1/overview/stats", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    card = data["recent_ontologies"][0]
    assert card["name"] == "计数本体"
    assert card["entity_count"] == 2
    assert card["logic_count"] == 1
    assert card["action_count"] == 0
    assert data["entity_count"] == 2
    assert data["logic_count"] == 1


def test_stats_endpoint_hits_cache_on_second_call(
    client, db, auth_headers, fake_client, stats_cache_on
):
    with QueryCounter(db) as first:
        r1 = client.get("/api/v1/overview/stats", headers=auth_headers)
    with QueryCounter(db) as second:
        r2 = client.get("/api/v1/overview/stats", headers=auth_headers)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert "ob:plat:stats" in fake_client.store
    # 命中后只剩认证查询，统计聚合不再执行。
    assert second.count <= 2
    assert second.count < first.count


def test_stats_endpoint_queries_directly_when_cache_disabled(
    client, db, auth_headers, fake_client
):
    with QueryCounter(db) as first:
        client.get("/api/v1/overview/stats", headers=auth_headers)
    with QueryCounter(db) as second:
        client.get("/api/v1/overview/stats", headers=auth_headers)
    assert fake_client.store == {}
    assert second.count > 2
