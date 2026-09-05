"""本体网络读接口缓存胶水层。

键名空间 ob:ont:network:*。/overview 与 /graph 是跨本体全局聚合读：
节点与统计来自发布快照（不可变）和项目身份，实例计数在服务层已有
短 TTL 缓存，响应级缓存只做同口径加速。失效面与详情缓存一致：项目
创建/导入/更新/删除与发布指针变更（发布/回滚/激活）bump 版本键换键，
后台灌数导致的实例计数漂移由短 TTL 兜底；手动 fresh 参数完全绕过。

设计契约与 ``app.shared.redis_cache`` 一致：Redis 只是加速器，任何连接/
读写异常都静默降级直查，绝不影响接口可用性与正确性；开关关闭时完全
绕过缓存，等价于现状直查路径。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from app.config import settings
from app.shared import redis_cache

_VERSION_KEY = "ob:ont:network:ver"

# 响应回填上限：超限（如选满 12 个大本体）放弃缓存只走直查，
# 防止单键占用过多 Redis 内存。
RESPONSE_MAX_BYTES = 1_000_000


def _enabled() -> bool:
    return bool(settings.ontology_network_cache_enabled)


def _version() -> str:
    if not _enabled():
        return "0"
    return redis_cache.cache_version(_VERSION_KEY)


def overview_cache_key() -> str:
    """本体网络总览（全局单键，无参数维度）。"""
    return f"ob:ont:network:v{_version()}:overview"


def graph_cache_key(
    ontology_ids: str,
    level: int,
    query: str | None,
    limit_per_type: int,
    bridge_same_name: bool,
) -> str:
    """跨本体全局图；本体集合（去重保序）与图参数进哈希作用域。"""
    ids = ",".join(
        dict.fromkeys(item.strip() for item in ontology_ids.split(",") if item.strip())
    )
    scope = hashlib.md5(
        "|".join(
            [
                ids,
                str(level),
                (query or "").strip(),
                str(int(limit_per_type)),
                str(bool(bridge_same_name)),
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"ob:ont:network:v{_version()}:graph:{scope}"


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


def invalidate_network() -> None:
    redis_cache.cache_bump(_VERSION_KEY)
