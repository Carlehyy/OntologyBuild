"""本体详情页读接口缓存胶水层。

键名空间 ob:ont:*，独立于其他模块的缓存键（任务池 ob:pt:*、资产湖
ob:lake:*、流水线 ob:pl:* 等）。失效策略：Web 进程内的写操作（本体更新/
删除/发布、实例与关系灌数、动作执行与审批决策、哨兵配置变更）自增版本键，
旧键依赖短 TTL 自然过期；executor 进程与外部灌数进程侧的状态推进无法事件
失效，由短 TTL 兜底。

设计契约与 ``app.shared.redis_cache`` 一致：Redis 只是加速器，任何连接/
读写异常都静默降级直查，绝不影响接口可用性与正确性；开关关闭时完全绕过
缓存，等价于现状直查路径。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from app.config import settings
from app.shared import redis_cache

_DETAIL_VERSION_KEY = "ob:ont:detail:ver"
_OVERVIEW_VERSION_KEY = "ob:ont:overview:ver"
_PENDING_VERSION_KEY = "ob:ont:pending:ver"
_INSTANCE_COUNTS_VERSION_KEY = "ob:ont:instance-counts:ver"
_VERSION_TREE_VERSION_KEY = "ob:ont:vtree:ver"

# 实例计数缓存 TTL：外部灌数/executor 进程无法事件失效，短 TTL 兜底；
# 手动"刷新本体清单"可带 fresh 参数绕过缓存强制直查。
INSTANCE_COUNTS_TTL_SECONDS = 60

# 版本树响应回填上限：超限（如海量历史版本）放弃缓存只走直查。
VERSION_TREE_MAX_BYTES = 1_000_000


def _enabled() -> bool:
    return bool(settings.ontology_detail_cache_enabled)


def _version(key: str) -> str:
    if not _enabled():
        return "0"
    return redis_cache.cache_version(key)


def detail_cache_key(ontology_id: str) -> str:
    """本体详情头（名称/领域/当前发布版本指针）。"""
    return f"ob:ont:detail:v{_version(_DETAIL_VERSION_KEY)}:{ontology_id}"


def overview_cache_key(ontology_id: str) -> str:
    """本体总览驾驶舱统计（模型/数据/运行/事实流/健康）。"""
    return f"ob:ont:overview:v{_version(_OVERVIEW_VERSION_KEY)}:{ontology_id}"


def runtime_summary_cache_key(ontology_id: str, start: str, end: str) -> str:
    """运行汇总按日聚合（显式时间窗）。

    与 overview 同一版本键：统计口径同源（当前发布血缘的哨兵/动作日志），
    发布、灌数、审批等写路径失效 overview 时必须同步失效这里。
    """
    return f"ob:ont:runtime-summary:v{_version(_OVERVIEW_VERSION_KEY)}:{ontology_id}:{start}:{end}"


def pending_cache_key(ontology_id: str, release_id: Optional[str]) -> str:
    """待审批/待恢复动作队列；release 维度分键。"""
    scope = release_id or "any"
    return f"ob:ont:pending:v{_version(_PENDING_VERSION_KEY)}:{ontology_id}:{scope}"


def instance_counts_cache_key(ontology_id: str, release_id: Optional[str]) -> str:
    """本体实例计数（发布版 or 工作区实时口径分别缓存）。"""
    scope = release_id or "workspace"
    return f"ob:ont:instance-counts:v{_version(_INSTANCE_COUNTS_VERSION_KEY)}:{ontology_id}:{scope}"


def _vtree_enabled() -> bool:
    return bool(settings.ontology_version_tree_cache_enabled)


def _vtree_version() -> str:
    if not _vtree_enabled():
        return "0"
    return redis_cache.cache_version(_VERSION_TREE_VERSION_KEY)


def version_tree_cache_key(ontology_id: str) -> str:
    """本体版本树（版本行 + 当前发布指针 + 最近试跑状态）。"""
    return f"ob:ont:vtree:v{_vtree_version()}:{ontology_id}"


def vtree_cached_call(key: str, ttl_seconds: int, builder: Callable[[], Any]) -> Any:
    """版本树 cache-aside：开关关闭时完全绕过；回填前校验字节上限。"""
    if not _vtree_enabled():
        return builder()
    cached = redis_cache.cache_get(key)
    if cached is not None:
        return cached
    value = builder()
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        if len(payload.encode("utf-8")) <= VERSION_TREE_MAX_BYTES:
            redis_cache.cache_set(key, value, ttl_seconds)
    except Exception:  # noqa: BLE001 - 序列化/回填失败不影响返回值
        pass
    return value


def cached_call(key: str, ttl_seconds: int, builder: Callable[[], Any]) -> Any:
    """开关关闭时完全绕过缓存，等价于现状直查路径。"""
    if not _enabled():
        return builder()
    return redis_cache.cache_aside(key, ttl_seconds, builder)


def invalidate_detail() -> None:
    redis_cache.cache_bump(_DETAIL_VERSION_KEY)


def invalidate_overview() -> None:
    redis_cache.cache_bump(_OVERVIEW_VERSION_KEY)


def invalidate_pending() -> None:
    redis_cache.cache_bump(_PENDING_VERSION_KEY)


def invalidate_instance_counts() -> None:
    redis_cache.cache_bump(_INSTANCE_COUNTS_VERSION_KEY)


def invalidate_version_tree() -> None:
    redis_cache.cache_bump(_VERSION_TREE_VERSION_KEY)
