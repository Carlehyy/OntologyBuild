"""本体网络读缓存：键作用域/失效/命中/fresh 绕过与写路径 bump 语义。

不依赖真实 Redis：用假客户端验证缓存行为；conftest 已将
ONTOLOGY_NETWORK_CACHE_ENABLED 置 false，本文件的用例按需显式开启。
"""
from __future__ import annotations

import pytest
from sqlalchemy import event

from app.ontologies.network import cache as network_cache
from app.shared import redis_cache


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


def _network(path: str) -> str:
    return f"/api/v2/ontology-network{path}"


def _create_ontology(client, auth_headers, name: str) -> dict:
    r = client.post(
        "/api/v1/ontologies", headers=auth_headers,
        json={"name": name, "domain": "供应链"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(redis_cache, "_client", client)
    monkeypatch.setattr(redis_cache, "_client_failed_at", 0.0)
    return client


@pytest.fixture
def cache_on(monkeypatch):
    monkeypatch.setattr(network_cache, "_enabled", lambda: True)


def test_graph_cache_key_scopes_params(fake_client, cache_on):
    keys = {
        network_cache.graph_cache_key("a,b", 2, None, 10, True),
        network_cache.graph_cache_key("b,a", 2, None, 10, True),
        network_cache.graph_cache_key("a,b", 1, None, 10, True),
        network_cache.graph_cache_key("a,b", 2, "订单", 10, True),
        network_cache.graph_cache_key("a,b", 2, None, 20, True),
        network_cache.graph_cache_key("a,b", 2, None, 10, False),
    }
    assert len(keys) == 6
    # 重复 id 与首尾空白归一到同一键；顺序保持敏感（响应合并顺序与入参一致）。
    assert (
        network_cache.graph_cache_key(" a , a ,b ", 2, None, 10, True)
        == network_cache.graph_cache_key("a,b", 2, None, 10, True)
    )


def test_cache_key_changes_after_bump(fake_client, cache_on):
    before = network_cache.overview_cache_key()
    network_cache.invalidate_network()
    assert network_cache.overview_cache_key() != before
    assert "ob:ont:network:ver" in fake_client.bumps


def test_cached_call_hit_and_miss(fake_client, cache_on):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"nodes": calls["n"]}

    key = "ob:ont:network:test:hit"
    assert network_cache.cached_call(key, 60, builder) == {"nodes": 1}
    assert network_cache.cached_call(key, 60, builder) == {"nodes": 1}
    assert calls["n"] == 1
    assert key in fake_client.store


def test_cached_call_skips_backfill_for_oversized_payload(fake_client, cache_on):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"blob": "x" * (network_cache.RESPONSE_MAX_BYTES + 1)}

    key = "ob:ont:network:test:big"
    network_cache.cached_call(key, 60, builder)
    network_cache.cached_call(key, 60, builder)
    assert calls["n"] == 2
    assert key not in fake_client.store


def test_cached_call_bypasses_when_disabled(fake_client):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"nodes": calls["n"]}

    network_cache.cached_call("ob:ont:network:test:off", 60, builder)
    network_cache.cached_call("ob:ont:network:test:off", 60, builder)
    assert calls["n"] == 2
    assert fake_client.store == {}


def test_overview_endpoint_hits_cache_on_second_call(
    client, db, auth_headers, fake_client, cache_on
):
    with QueryCounter(db) as first:
        r1 = client.get(_network("/overview"), headers=auth_headers)
    with QueryCounter(db) as second:
        r2 = client.get(_network("/overview"), headers=auth_headers)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert network_cache.overview_cache_key() in fake_client.store
    # 命中后只剩认证查询，聚合不再执行。
    assert second.count <= 2


def test_overview_fresh_param_bypasses_cache(
    client, auth_headers, fake_client, cache_on
):
    client.get(_network("/overview"), headers=auth_headers)
    assert fake_client.store
    fake_client.store.clear()
    r = client.get(_network("/overview"), params={"fresh": True}, headers=auth_headers)
    assert r.status_code == 200
    # fresh 手动刷新：既不读也不写缓存。
    assert fake_client.store == {}


def test_graph_endpoint_hits_cache_on_second_call(
    client, db, auth_headers, fake_client, cache_on
):
    ontology = _create_ontology(client, auth_headers, "图谱缓存本体")
    params = {"ontology_ids": ontology["id"], "level": 2}
    with QueryCounter(db) as first:
        r1 = client.get(_network("/graph"), params=params, headers=auth_headers)
    with QueryCounter(db) as second:
        r2 = client.get(_network("/graph"), params=params, headers=auth_headers)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert second.count <= 2


def test_graph_error_response_is_not_cached(client, auth_headers, fake_client, cache_on):
    r = client.get(
        _network("/graph"),
        params={"ontology_ids": ""},
        headers=auth_headers,
    )
    assert r.status_code == 422
    assert fake_client.store == {}


def test_create_and_update_bump_network_version(
    client, auth_headers, fake_client, cache_on
):
    ontology = _create_ontology(client, auth_headers, "失效接线本体")
    assert "ob:ont:network:ver" in fake_client.bumps
    r = client.put(
        f"/api/v1/ontologies/{ontology['id']}",
        json={"description": "改描述触发网络图换键"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert fake_client.bumps.count("ob:ont:network:ver") >= 2


def test_overview_reflects_creation_after_bump(
    client, auth_headers, fake_client, cache_on
):
    r1 = client.get(_network("/overview"), headers=auth_headers)
    assert r1.status_code == 200
    assert r1.json()["data"] == []
    _create_ontology(client, auth_headers, "可见性本体")
    r2 = client.get(_network("/overview"), headers=auth_headers)
    assert len(r2.json()["data"]) == 1
