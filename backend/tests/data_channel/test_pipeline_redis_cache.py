"""数据流水线读接口 Redis 缓存：命中/失效/降级语义（不依赖真实 Redis）。

测试环境默认关闭缓存（各胶水层 `_enabled()`），本文件显式打开并注入
FakeClient 验证键空间、失效与 fail-open 契约；真实 Redis 行为由
docs/operations 与生产环境验收覆盖。
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.data_channel.pipelines import cache as pl_cache
from app.data_channel.steward import cache as steward_cache
from app.data_channel.sync_tasks import cache as st_cache
from app.shared import redis_cache


class FakeClient:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def incr(self, key):
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def scan_iter(self, match=None, count=100):
        import fnmatch

        return iter(
            [key for key in self.store if fnmatch.fnmatch(key, match or "*")]
        )

    def delete(self, key):
        return bool(self.store.pop(key, None))


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(redis_cache, "_client", client)
    monkeypatch.setattr(redis_cache, "_client_failed_at", 0.0)
    # 测试环境默认关闭缓存：显式打开以验证胶水层语义。
    monkeypatch.setattr(st_cache, "_enabled", lambda: True)
    monkeypatch.setattr(pl_cache, "_enabled", lambda: True)
    monkeypatch.setattr(steward_cache, "_enabled", lambda: True)
    return client


def test_shared_cache_aside_hit_returns_cached_value(fake_client):
    calls = []

    def builder():
        calls.append(1)
        return {"v": 1}

    first = redis_cache.cache_aside("ob:test:k", 10, builder)
    second = redis_cache.cache_aside("ob:test:k", 10, builder)
    assert first == {"v": 1}
    assert second == first
    assert len(calls) == 1


def test_shared_cache_aside_builder_exception_not_cached(fake_client):
    class Boom(Exception):
        pass

    def builder():
        raise Boom()

    with pytest.raises(Boom):
        redis_cache.cache_aside("ob:test:boom", 10, builder)
    assert fake_client.store.get("ob:test:boom") is None
    # 异常后再次调用仍走 builder，不缓存错误结果
    with pytest.raises(Boom):
        redis_cache.cache_aside("ob:test:boom", 10, builder)


def test_shared_cache_aside_fail_open_when_client_unavailable(monkeypatch):
    monkeypatch.setattr(redis_cache, "_client", None)
    monkeypatch.setattr(redis_cache, "_client_failed_at", float("inf"))

    def builder():
        return {"ok": True}

    assert redis_cache.cache_aside("ob:test:x", 10, builder) == {"ok": True}


def test_sync_task_source_invalidation_bumps_version(fake_client):
    before = st_cache.source_tables_key("conn-1")
    st_cache.invalidate_source("conn-1")
    after = st_cache.source_tables_key("conn-1")
    assert before != after
    assert (
        st_cache.source_sample_key("conn-1", "t1")
        != st_cache.source_sample_key("conn-1", "t2")
    )


def test_sync_task_list_key_varied_by_params(fake_client):
    a = st_cache.list_tasks_key({"search": "x", "page": 1, "page_size": 50})
    b = st_cache.list_tasks_key({"search": "x", "page": 2, "page_size": 50})
    c = st_cache.list_tasks_key({"search": "x", "page": 1, "page_size": 50})
    assert a != b
    assert a == c


def test_dryrun_payload_skipped_when_oversized(fake_client, monkeypatch):
    monkeypatch.setattr(settings, "pipeline_dryrun_cache_max_bytes", 100)
    key = pl_cache.dryrun_key("p-1", "d-1")
    pl_cache.cache_dryrun_payload(key, {"rows": ["x" * 200]})
    assert fake_client.store.get(key) is None
    pl_cache.cache_dryrun_payload(key, {"rows": ["小"]})
    assert fake_client.store.get(key) is not None
    assert pl_cache.get_dryrun_payload(key) == {"rows": ["小"]}


def test_invalidate_pipeline_dryruns_clears_only_own_prefix(fake_client):
    pl_cache.cache_dryrun_payload(
        pl_cache.dryrun_key("p-1", "d-1"), {"rows": [1]}
    )
    pl_cache.cache_dryrun_payload(
        pl_cache.dryrun_key("p-1", "d-2"), {"rows": [2]}
    )
    pl_cache.cache_dryrun_payload(
        pl_cache.dryrun_key("p-2", "d-1"), {"rows": [3]}
    )
    pl_cache.invalidate_pipeline_dryruns("p-1")
    assert pl_cache.get_dryrun_payload(
        pl_cache.dryrun_key("p-1", "d-1")
    ) is None
    assert pl_cache.get_dryrun_payload(
        pl_cache.dryrun_key("p-1", "d-2")
    ) is None
    assert pl_cache.get_dryrun_payload(
        pl_cache.dryrun_key("p-2", "d-1")
    ) == {"rows": [3]}


def test_disabled_glue_bypasses_cache(monkeypatch):
    monkeypatch.setattr(st_cache, "_enabled", lambda: False)
    calls = []

    def builder():
        calls.append(1)
        return {"v": 1}

    first = st_cache.cached_call("ob:st:stats", 5, builder)
    second = st_cache.cached_call("ob:st:stats", 5, builder)
    assert first == second == {"v": 1}
    assert len(calls) == 2


def test_test_env_disables_cache_by_default():
    assert settings.environment.strip().lower() == "test"
    assert st_cache._enabled() is False
    assert pl_cache._enabled() is False
    assert steward_cache._enabled() is False
