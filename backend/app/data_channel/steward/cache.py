"""数据管家读接口的 Redis 缓存胶水层（fail-open）。

只缓存远端 n8n 的返回（连通性探活、工作流列表、单个工作流快照），本地
数据库事实仍实时查询。TTL 秒级：启停/发布后的 active 状态最多延迟一个
TTL 窗口，写接口自身的响应永远实时。Redis 不可用时直接打远端 n8n，
主流程不受影响。
"""
from __future__ import annotations

from typing import Any, Callable

from app.config import settings
from app.shared import redis_cache


def _enabled() -> bool:
    # 测试环境关闭：保证测试不依赖环境里是否存在 Redis，结果确定。
    if settings.environment.strip().lower() == "test":
        return False
    return bool(settings.pipeline_read_cache_enabled)


def cached_call(key: str, builder: Callable[[], Any]) -> Any:
    if not _enabled():
        return builder()
    return redis_cache.cache_aside(
        key, settings.steward_n8n_cache_ttl_seconds, builder
    )


def n8n_ping_key() -> str:
    return "ob:n8n:ping"


def n8n_workflows_key() -> str:
    return "ob:n8n:workflows"


def n8n_workflow_key(workflow_id: str) -> str:
    return f"ob:n8n:workflow:{workflow_id}"


def cached_n8n_probe(service_module, db) -> dict:
    """远端连通性探活：命中返回缓存；未命中执行探活并尽力回填。

    页面打开即探活会形成对 n8n 的探测风暴，秒级缓存同时保护 n8n。
    """

    def _probe() -> dict:
        try:
            service_module.get_n8n_client(db).test_connection()
            return {"reachable": True, "error": None}
        except Exception as exc:  # noqa: BLE001
            return {"reachable": False, "error": str(exc)[:300]}

    return cached_call(n8n_ping_key(), _probe)


def cached_n8n_workflows(fetch_fn: Callable[[], Any]) -> Any:
    """工作流列表：命中返回缓存；未命中执行 fetch_fn 并尽力回填。"""
    return cached_call(n8n_workflows_key(), fetch_fn)


def cached_n8n_workflow(workflow_id: str, fetch_fn: Callable[[], Any]) -> Any:
    """单个工作流快照：命中返回缓存；未命中执行 fetch_fn 并尽力回填。"""
    return cached_call(n8n_workflow_key(workflow_id), fetch_fn)
