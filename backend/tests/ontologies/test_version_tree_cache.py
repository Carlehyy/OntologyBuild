"""本体版本树读缓存：键作用域/失效/命中与写路径 bump 语义。

不依赖真实 Redis：用假客户端验证缓存行为；conftest 已将
ONTOLOGY_VERSION_TREE_CACHE_ENABLED 置 false，本文件的用例按需显式开启。
"""
from __future__ import annotations

import pytest
from sqlalchemy import event

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
    monkeypatch.setattr(ont_cache, "_vtree_enabled", lambda: True)


def test_version_tree_cache_key_scopes_by_ontology(fake_client, cache_on):
    a = ont_cache.version_tree_cache_key("ont-1")
    b = ont_cache.version_tree_cache_key("ont-2")
    assert a != b
    assert a.startswith("ob:ont:vtree:v0:ont-1")
    ont_cache.invalidate_version_tree()
    assert ont_cache.version_tree_cache_key("ont-1") != a
    assert "ob:ont:vtree:ver" in fake_client.bumps


def test_vtree_cached_call_hit_and_miss(fake_client, cache_on):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"versions": calls["n"]}

    key = "ob:ont:vtree:test:hit"
    assert ont_cache.vtree_cached_call(key, 15, builder) == {"versions": 1}
    assert ont_cache.vtree_cached_call(key, 15, builder) == {"versions": 1}
    assert calls["n"] == 1
    assert key in fake_client.store


def test_vtree_cached_call_bypasses_when_disabled(fake_client):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"versions": calls["n"]}

    ont_cache.vtree_cached_call("ob:ont:vtree:test:off", 15, builder)
    ont_cache.vtree_cached_call("ob:ont:vtree:test:off", 15, builder)
    assert calls["n"] == 2
    assert fake_client.store == {}


def test_vtree_cached_call_skips_backfill_for_oversized_payload(fake_client, cache_on):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"blob": "x" * (ont_cache.VERSION_TREE_MAX_BYTES + 1)}

    key = "ob:ont:vtree:test:big"
    ont_cache.vtree_cached_call(key, 15, builder)
    ont_cache.vtree_cached_call(key, 15, builder)
    assert calls["n"] == 2
    assert key not in fake_client.store


def test_version_tree_endpoint_hits_cache_on_second_call(
    client, db, auth_headers, fake_client, cache_on
):
    ontology = _create_ontology(client, auth_headers, "版本树缓存本体")
    url = f"/api/v2/ontologies/{ontology['id']}/version-tree"
    with QueryCounter(db) as first:
        r1 = client.get(url, headers=auth_headers)
    with QueryCounter(db) as second:
        r2 = client.get(url, headers=auth_headers)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert ont_cache.version_tree_cache_key(ontology["id"]) in fake_client.store
    # 命中后只剩认证查询，版本树聚合不再执行。
    assert second.count <= 2


def test_version_tree_error_response_is_not_cached(
    client, auth_headers, fake_client, cache_on
):
    r = client.get("/api/v2/ontologies/missing-ont/version-tree", headers=auth_headers)
    assert r.status_code == 404
    assert fake_client.store == {}


def test_create_and_draft_bump_version_tree(
    client, auth_headers, fake_client, cache_on
):
    ontology = _create_ontology(client, auth_headers, "失效接线本体")
    assert "ob:ont:vtree:ver" in fake_client.bumps
    tree = client.get(
        f"/api/v2/ontologies/{ontology['id']}/version-tree", headers=auth_headers
    ).json()["data"]
    source_id = tree["versions"][0]["id"]
    r = client.post(
        f"/api/v2/ontologies/{ontology['id']}/versions/{source_id}/drafts",
        json={}, headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert fake_client.bumps.count("ob:ont:vtree:ver") >= 2


def test_version_tree_reflects_draft_after_bump(
    client, auth_headers, fake_client, cache_on
):
    ontology = _create_ontology(client, auth_headers, "可见性本体")
    url = f"/api/v2/ontologies/{ontology['id']}/version-tree"
    tree1 = client.get(url, headers=auth_headers).json()["data"]
    assert len(tree1["versions"]) == 1
    source_id = tree1["versions"][0]["id"]
    client.post(
        f"/api/v2/ontologies/{ontology['id']}/versions/{source_id}/drafts",
        json={}, headers=auth_headers,
    )
    tree2 = client.get(url, headers=auth_headers).json()["data"]
    assert len(tree2["versions"]) == 2
