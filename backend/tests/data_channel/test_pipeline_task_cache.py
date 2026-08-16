"""任务池读接口缓存：键/失效/命中/降级语义。

不依赖真实 Redis：用假客户端验证缓存行为；fail-open 测试在无 Redis 的
测试环境下天然走降级路径（本机若恰好有 Redis 也只会得到相同结果断言）。
conftest 已将 PIPELINE_TASK_CACHE_ENABLED 置 false，本文件的用例按需
显式开启。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.data_channel.pipeline_tasks import cache as pt_cache
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
    monkeypatch.setattr(pt_cache, "_enabled", lambda: True)


def test_cache_aside_hit_and_miss(fake_client, cache_on):
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"rows": calls["n"]}

    first = redis_cache.cache_aside("ob:pt:test", 60, builder)
    second = redis_cache.cache_aside("ob:pt:test", 60, builder)
    assert first == {"rows": 1}
    assert second == {"rows": 1}
    assert calls["n"] == 1
    assert "ob:pt:test" in fake_client.store


def test_cache_aside_does_not_swallow_builder_errors(fake_client, cache_on):
    def boom():
        raise HTTPException(404, "gone")

    with pytest.raises(HTTPException):
        redis_cache.cache_aside("ob:pt:boom", 60, boom)
    assert "ob:pt:boom" not in fake_client.store


def test_fail_open_without_redis(monkeypatch, cache_on):
    """Redis 不可用时必须静默降级：builder 结果原样返回。"""
    monkeypatch.setattr(redis_cache, "_client", None)
    monkeypatch.setattr(redis_cache, "_client_failed_at", 0.0)

    def builder():
        return {"ok": True}

    assert redis_cache.cache_aside("ob:pt:test", 5, builder) == {"ok": True}


def test_invalidate_all_bumps_versions_and_changes_keys(fake_client, cache_on):
    params = {
        "search": None,
        "status": None,
        "enabled": None,
        "pipeline_id": None,
        "page": 1,
        "page_size": 50,
    }
    before_list = pt_cache.list_cache_key(params)
    before_stats = pt_cache.stats_cache_key()
    before_options = pt_cache.options_cache_key()
    pt_cache.invalidate_all()
    assert pt_cache.list_cache_key(params) != before_list
    assert pt_cache.stats_cache_key() != before_stats
    assert pt_cache.options_cache_key() != before_options
    assert set(fake_client.bumps) == {
        "ob:pt:list:ver",
        "ob:pt:stats:ver",
        "ob:pt:options:ver",
    }


def test_list_cache_key_distinguishes_params(fake_client, cache_on):
    base = {
        "search": None,
        "status": None,
        "enabled": None,
        "pipeline_id": None,
        "page": 1,
        "page_size": 50,
    }
    a = pt_cache.list_cache_key(base)
    b = pt_cache.list_cache_key({**base, "page": 2})
    c = pt_cache.list_cache_key({**base, "status": "running"})
    assert len({a, b, c}) == 3


def test_disabled_cache_bypasses_redis(fake_client):
    """开关关闭时绝不读 Redis，等价于现状直查路径。"""
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return calls["n"]

    assert pt_cache.cached_call("any", 60, builder) == 1
    assert pt_cache.cached_call("any", 60, builder) == 2
    assert fake_client.store == {}


def test_list_and_stats_endpoints_serve_cached_response(
    db, client, auth_headers, fake_client, cache_on,
):
    """端到端：缓存开启时第二次请求命中缓存，响应与首次一致。"""
    from app.models.v2.pipeline import Pipeline

    db.add(
        Pipeline(
            id="pipe-cache-e2e",
            name="缓存端到端流水线",
            spec={},
            status="published",
            enabled=True,
        )
    )
    db.commit()

    first = client.get("/api/v2/pipeline-tasks", headers=auth_headers)
    second = client.get("/api/v2/pipeline-tasks", headers=auth_headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    stats_first = client.get(
        "/api/v2/pipeline-tasks/stats", headers=auth_headers
    )
    stats_second = client.get(
        "/api/v2/pipeline-tasks/stats", headers=auth_headers
    )
    assert stats_first.status_code == 200
    assert stats_second.status_code == 200
    assert stats_first.json() == stats_second.json()

    assert any(key.startswith("ob:pt:list:") for key in fake_client.store)
    assert any(key.startswith("ob:pt:stats:") for key in fake_client.store)
