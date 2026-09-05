"""平台概览统计读接口缓存胶水层。

键名空间 ob:plat:*。统计是全局只读聚合（全表计数 + 最近本体卡片），
没有写路径与之联动，新鲜度完全由短 TTL 兜底；因此不需要版本键，
TTL 过期后下一次请求自然回填。

设计契约与 ``app.shared.redis_cache`` 一致：Redis 只是加速器，任何连接/
读写异常都静默降级直查，绝不影响接口可用性与正确性；开关关闭时完全
绕过缓存，等价于现状直查路径。
"""
from __future__ import annotations

from typing import Any, Callable

from app.config import settings
from app.shared import redis_cache


def _enabled() -> bool:
    return bool(settings.platform_stats_cache_enabled)


def stats_cache_key() -> str:
    """平台概览统计（全局单键，无维度参数）。"""
    return "ob:plat:stats"


def cached_call(key: str, ttl_seconds: int, builder: Callable[[], Any]) -> Any:
    """开关关闭时完全绕过缓存，等价于现状直查路径。"""
    if not _enabled():
        return builder()
    return redis_cache.cache_aside(key, ttl_seconds, builder)
