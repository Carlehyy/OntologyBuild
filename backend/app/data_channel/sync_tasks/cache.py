"""同步任务读接口的 Redis 缓存胶水层（fail-open）。

命名空间 ``ob:st:*``。列表/统计使用短 TTL（小于前端轮询间隔，用户无感）；
数据源元数据/样例使用中 TTL，并在连接删除时经版本键失效。旧版
DataSyncTask 的写入口已退役（410），列表/统计没有写侧失效点，由短 TTL
兜底新鲜度。所有键依赖 ``app.shared.redis_cache`` 的降级语义：
Redis 不可用时直接走原查询路径，主流程不受影响。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from app.config import settings
from app.shared import redis_cache

_SOURCE_VERSION_PREFIX = "ob:st:src:ver"


def _enabled() -> bool:
    # 测试环境关闭：保证测试不依赖环境里是否存在 Redis，结果确定。
    if settings.environment.strip().lower() == "test":
        return False
    return bool(settings.pipeline_read_cache_enabled)


def cached_call(key: str, ttl_seconds: int, builder: Callable[[], Any]) -> Any:
    """命中返回缓存；未命中执行 builder 并尽力回填（异常原样上抛）。"""
    if not _enabled():
        return builder()
    return redis_cache.cache_aside(key, ttl_seconds, builder)


def list_tasks_key(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]
    return f"ob:st:list:{digest}"


def stats_key() -> str:
    return "ob:st:stats"


def _source_version(connection_id: str) -> str:
    if not _enabled():
        return "0"
    return redis_cache.cache_version(f"{_SOURCE_VERSION_PREFIX}:{connection_id}")


def source_tables_key(connection_id: str) -> str:
    return f"ob:st:meta:{connection_id}:v{_source_version(connection_id)}"


def source_sample_key(connection_id: str, table: str) -> str:
    return (
        f"ob:st:sample:{connection_id}:{table}:v{_source_version(connection_id)}"
    )


def invalidate_source(connection_id: str) -> None:
    """连接被删除后调用：旧键依赖 TTL 自然过期（best-effort）。"""
    if not _enabled():
        return
    redis_cache.cache_bump(f"{_SOURCE_VERSION_PREFIX}:{connection_id}")
