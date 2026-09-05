"""本体列表读缓存：键作用域/失效/命中/回填上限与写路径 bump 语义。

不依赖真实 Redis：用假客户端验证缓存行为；conftest 已将
ONTOLOGY_LIST_CACHE_ENABLED 置 false，本文件的用例按需显式开启。
"""
from __future__ import annotations

import pytest

from app.ontologies import cache as ont_cache
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
def list_cache_on(monkeypatch):
    monkeypatch.setattr(ont_cache, "_list_enabled", lambda: True)


def test_list_cache_key_scopes_filters_and_page(fake_client, list_cache_on):
    keys = {
        ont_cache.list_cache_key(None, None, 1, 20),
        ont_cache.list_cache_key("名", None, 1, 20),
        ont_cache.list_cache_key(None, "供应链", 1, 20),
        ont_cache.list_cache_key(None, None, 2, 20),
        ont_cache.list_cache_key(None, None, 1, 1000),
    }
    assert len(keys) == 5
    assert ont_cache.list_cache_key(None, None, 1, 20).startswith("ob:ont:list:v")


def test_list_cache_key_changes_after_bump(fake_client, list_cache_on):
    before = ont_cache.list_cache_key(None, None, 1, 20)
    ont_cache.invalidate_list()
    after = ont_cache.list_cache_key(None, None, 1, 20)
    assert before != after
    assert "ob:ont:list:ver" in fake_client.bumps


def test_release_counts_cache_key_scopes_by_release_id():
    assert ont_cache.release_counts_cache_key("rel-1") == "ob:ont:relcounts:rel-1"


def test_list_cached_call_hit_and_miss(fake_client, list_cache_on):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"items": calls["n"]}

    key = "ob:ont:test:list"
    assert ont_cache.list_cached_call(key, 60, builder) == {"items": 1}
    assert ont_cache.list_cached_call(key, 60, builder) == {"items": 1}
    assert calls["n"] == 1
    assert key in fake_client.store


def test_list_cached_call_skips_backfill_for_oversized_payload(
    fake_client, list_cache_on
):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"blob": "x" * (ont_cache.LIST_CACHE_MAX_BYTES + 1)}

    key = "ob:ont:test:big"
    ont_cache.list_cached_call(key, 60, builder)
    ont_cache.list_cached_call(key, 60, builder)
    assert calls["n"] == 2
    assert key not in fake_client.store


def test_list_cached_call_bypasses_when_disabled(fake_client):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"items": calls["n"]}

    ont_cache.list_cached_call("ob:ont:test:off", 60, builder)
    ont_cache.list_cached_call("ob:ont:test:off", 60, builder)
    assert calls["n"] == 2
    assert fake_client.store == {}


def test_list_endpoint_serves_second_call_from_cache(
    client, db, auth_headers, fake_client, list_cache_on
):
    r = client.post(
        "/api/v1/ontologies",
        json={"name": "列表缓存本体", "domain": "供应链"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    with QueryCounter(db) as first:
        r1 = client.get(
            "/api/v1/ontologies", params={"page_size": 5}, headers=auth_headers
        )
    with QueryCounter(db) as second:
        r2 = client.get(
            "/api/v1/ontologies", params={"page_size": 5}, headers=auth_headers
        )
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert r2.json()["data"]["total"] == 1
    assert second.count <= 2


def test_create_ontology_bumps_list_version(client, auth_headers, fake_client, list_cache_on):
    r = client.post(
        "/api/v1/ontologies",
        json={"name": "失效接线本体", "domain": "供应链"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert "ob:ont:list:ver" in fake_client.bumps


def test_list_cache_reflects_creation_after_bump(
    client, auth_headers, fake_client, list_cache_on
):
    r1 = client.get("/api/v1/ontologies", headers=auth_headers)
    assert r1.json()["data"]["total"] == 0
    client.post(
        "/api/v1/ontologies",
        json={"name": "可见性本体", "domain": "供应链"},
        headers=auth_headers,
    )
    r2 = client.get("/api/v1/ontologies", headers=auth_headers)
    assert r2.json()["data"]["total"] == 1
