"""世界模型（演化层）读接口缓存胶水层。

键名空间 ob:wm:*。覆盖推演项目/服务/调用记录的列表与看板聚合读。
失效面：项目增删改、脚本保存（版本计数）、服务发布与状态切换、
服务调用（写入调用记录）——全部在 Web 进程内的 service 层提交点
bump 单一版本键换键，旧键由短 TTL 自然回收。

设计契约与 ``app.shared.redis_cache`` 一致：Redis 只是加速器，任何连接/
读写异常都静默降级直查，绝不影响接口可用性与正确性；开关关闭时完全
绕过缓存，等价于现状直查路径。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Optional

from app.config import settings
from app.shared import redis_cache

_VERSION_KEY = "ob:wm:ver"

# 响应回填上限：超限放弃缓存只走直查，防止单键占用过多 Redis 内存。
RESPONSE_MAX_BYTES = 1_000_000


def _enabled() -> bool:
    return bool(settings.world_model_cache_enabled)


def _version() -> str:
    if not _enabled():
        return "0"
    return redis_cache.cache_version(_VERSION_KEY)


def _hash_scope(*parts: Any) -> str:
    joined = "|".join(str(part) for part in parts)
    return hashlib.md5(joined.encode("utf-8")).hexdigest()[:16]


def projects_cache_key(keyword: str, engine_type: str, page: int, size: int) -> str:
    """推演项目列表；筛选/分页维度进哈希作用域。"""
    scope = _hash_scope(keyword or "", engine_type or "", page, size)
    return f"ob:wm:projects:v{_version()}:{scope}"


def services_cache_key(keyword: str, status: str, page: int, size: int) -> str:
    """推演服务注册表列表。"""
    scope = _hash_scope(keyword or "", status or "", page, size)
    return f"ob:wm:services:v{_version()}:{scope}"


def calls_cache_key(
    keyword: str,
    result: str,
    service_id: str,
    start: Optional[datetime],
    end: Optional[datetime],
    page: int,
    size: int,
) -> str:
    """调用记录列表；时间窗进键避免跨窗口串数据。"""
    scope = _hash_scope(
        keyword or "",
        result or "",
        service_id or "",
        start.isoformat() if start else "",
        end.isoformat() if end else "",
        page,
        size,
    )
    return f"ob:wm:calls:v{_version()}:{scope}"


def services_overview_cache_key() -> str:
    """服务概览统计（全局单键）。"""
    return f"ob:wm:services-overview:v{_version()}"


def calls_overview_cache_key() -> str:
    """调用概览统计（全局单键）。"""
    return f"ob:wm:calls-overview:v{_version()}"


def calls_daily_cache_key(days: int) -> str:
    """按日调用聚合（时间窗参数进键）。"""
    return f"ob:wm:calls-daily:v{_version()}:{days}"


def cached_call(key: str, ttl_seconds: int, builder: Callable[[], Any]) -> Any:
    """cache-aside：开关关闭时完全绕过；回填前校验字节上限。"""
    if not _enabled():
        return builder()
    cached = redis_cache.cache_get(key)
    if cached is not None:
        return cached
    value = builder()
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        if len(payload.encode("utf-8")) <= RESPONSE_MAX_BYTES:
            redis_cache.cache_set(key, value, ttl_seconds)
    except Exception:  # noqa: BLE001 - 序列化/回填失败不影响返回值
        pass
    return value


def invalidate_world_model() -> None:
    redis_cache.cache_bump(_VERSION_KEY)
