"""本体详情页读接口缓存：键/失效/命中/降级语义。

不依赖真实 Redis：用假客户端验证缓存行为；fail-open 测试在无 Redis 的
测试环境下天然走降级路径（本机若恰好有 Redis 也只会得到相同结果断言）。
conftest 已将 ONTOLOGY_DETAIL_CACHE_ENABLED 置 false，本文件的用例按需
显式开启。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.ontologies import cache as ont_cache
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


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(redis_cache, "_client", client)
    monkeypatch.setattr(redis_cache, "_client_failed_at", 0.0)
    return client


@pytest.fixture
def cache_on(monkeypatch):
    monkeypatch.setattr(ont_cache, "_enabled", lambda: True)


def test_detail_cache_key_contains_ontology_and_version(fake_client, cache_on):
    a = ont_cache.detail_cache_key("ont-1")
    b = ont_cache.detail_cache_key("ont-2")
    assert a != b
    assert a.startswith("ob:ont:detail:v0:ont-1")
    ont_cache.invalidate_detail()
    assert ont_cache.detail_cache_key("ont-1") != a


def test_pending_cache_key_scopes_by_release(fake_client, cache_on):
    a = ont_cache.pending_cache_key("ont-1", None)
    b = ont_cache.pending_cache_key("ont-1", "rel-9")
    c = ont_cache.pending_cache_key("ont-1", "rel-10")
    assert a != b
    assert b != c
    assert a.endswith(":ont-1:any")
    assert b.endswith(":ont-1:rel-9")


def test_cached_call_hit_and_miss(fake_client, cache_on):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"rows": calls["n"]}

    key = "ob:ont:test:hit"
    first = ont_cache.cached_call(key, 60, builder)
    second = ont_cache.cached_call(key, 60, builder)
    assert first == {"rows": 1}
    assert second == {"rows": 1}
    assert calls["n"] == 1
    assert key in fake_client.store


def test_cached_call_does_not_swallow_builder_errors(fake_client, cache_on):
    def boom():
        raise HTTPException(404, "gone")

    with pytest.raises(HTTPException):
        ont_cache.cached_call("ob:ont:test:boom", 60, boom)
    assert "ob:ont:test:boom" not in fake_client.store


def test_fail_open_without_redis(monkeypatch, cache_on):
    """Redis 不可用时必须静默降级：builder 结果原样返回。"""
    monkeypatch.setattr(redis_cache, "_client", None)
    monkeypatch.setattr(redis_cache, "_client_failed_at", 0.0)

    def builder():
        return {"ok": True}

    assert ont_cache.cached_call("ob:ont:test:fo", 5, builder) == {"ok": True}


def test_disabled_cache_bypasses_redis(fake_client):
    """开关关闭时绝不读 Redis，等价于现状直查路径。"""
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return calls["n"]

    assert ont_cache.cached_call("any", 60, builder) == 1
    assert ont_cache.cached_call("any", 60, builder) == 2
    assert fake_client.store == {}


def test_invalidations_bump_own_version_keys(fake_client, cache_on):
    ont_cache.invalidate_detail()
    ont_cache.invalidate_overview()
    ont_cache.invalidate_pending()
    assert set(fake_client.bumps) == {
        "ob:ont:detail:ver",
        "ob:ont:overview:ver",
        "ob:ont:pending:ver",
    }


def test_detail_endpoint_serves_cached_response(
    client, auth_headers, ontology, fake_client, cache_on,
):
    """端到端：详情头缓存开启时第二次请求命中缓存，响应与首次一致。"""
    oid = ontology["id"]
    first = client.get(f"/api/v1/ontologies/{oid}", headers=auth_headers)
    second = client.get(f"/api/v1/ontologies/{oid}", headers=auth_headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert any(k.startswith("ob:ont:detail:") for k in fake_client.store)


def test_overview_endpoint_serves_cached_response(
    client, auth_headers, ontology, fake_client, cache_on,
):
    oid = ontology["id"]
    first = client.get(
        f"/api/v2/formal/ontologies/{oid}/overview", headers=auth_headers,
    )
    second = client.get(
        f"/api/v2/formal/ontologies/{oid}/overview", headers=auth_headers,
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert any(k.startswith("ob:ont:overview:") for k in fake_client.store)


def test_pending_endpoint_serves_cached_response(
    client, auth_headers, ontology, fake_client, cache_on,
):
    oid = ontology["id"]
    release = ontology["current_release_id"]
    url = (
        f"/api/v2/formal/ontologies/{oid}/pending-actions"
        f"?release_id={release}"
    )
    first = client.get(url, headers=auth_headers)
    second = client.get(url, headers=auth_headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert any(k.startswith("ob:ont:pending:") for k in fake_client.store)


def test_missing_ontology_404_is_not_cached(fake_client, cache_on, client, auth_headers):
    response = client.get(
        "/api/v1/ontologies/does-not-exist", headers=auth_headers,
    )
    assert response.status_code == 404
    assert not any(
        "does-not-exist" in k for k in fake_client.store
    )


def test_update_ontology_invalidates_detail_cache(
    client, auth_headers, ontology, fake_client, cache_on,
):
    oid = ontology["id"]
    assert client.get(
        f"/api/v1/ontologies/{oid}", headers=auth_headers,
    ).status_code == 200
    before = ont_cache.detail_cache_key(oid)

    updated = client.put(
        f"/api/v1/ontologies/{oid}",
        json={"name": "改名后的本体"},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert ont_cache.detail_cache_key(oid) != before
    assert "ob:ont:detail:ver" in fake_client.bumps
