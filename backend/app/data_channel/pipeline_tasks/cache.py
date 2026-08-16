"""任务池读接口缓存胶水层。

键名空间 ob:pt:*，独立于其他数据通道模块的缓存键（资产湖 ob:lake:*、
流水线 ob:pl:* 等）。失效策略：Web 进程内的写操作（创建/编辑/删除/启停/
手动触发）自增版本键，旧键依赖短 TTL 自然过期；executor 进程侧的状态
推进（运行中→成功/失败）无法事件失效，由短 TTL 兜底。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from app.config import settings
from app.shared import redis_cache

_LIST_VERSION_KEY = "ob:pt:list:ver"
_STATS_VERSION_KEY = "ob:pt:stats:ver"
_OPTIONS_VERSION_KEY = "ob:pt:options:ver"


def _enabled() -> bool:
    return bool(settings.pipeline_task_cache_enabled)


def _version(key: str) -> str:
    if not _enabled():
        return "0"
    return redis_cache.cache_version(key)


def list_cache_key(params: dict) -> str:
    """列表键携带参数指纹与版本号：任何写操作 bump 版本即整体失效。"""
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]
    return f"ob:pt:list:v{_version(_LIST_VERSION_KEY)}:{digest}"


def stats_cache_key() -> str:
    return f"ob:pt:stats:v{_version(_STATS_VERSION_KEY)}"


def options_cache_key() -> str:
    return f"ob:pt:options:v{_version(_OPTIONS_VERSION_KEY)}"


def cached_call(key: str, ttl_seconds: int, builder: Callable[[], Any]) -> Any:
    """开关关闭时完全绕过缓存，等价于现状直查路径。"""
    if not _enabled():
        return builder()
    return redis_cache.cache_aside(key, ttl_seconds, builder)


def invalidate_list() -> None:
    redis_cache.cache_bump(_LIST_VERSION_KEY)


def invalidate_stats() -> None:
    redis_cache.cache_bump(_STATS_VERSION_KEY)


def invalidate_options() -> None:
    redis_cache.cache_bump(_OPTIONS_VERSION_KEY)


def invalidate_all() -> None:
    """任务写操作统一失效入口：列表/统计/筛选候选一次全部换版本。"""
    invalidate_list()
    invalidate_stats()
    invalidate_options()
