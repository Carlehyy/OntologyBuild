"""流水线读接口的 Redis 缓存胶水层（fail-open）。

- 运行列表/详情：2s 级 TTL，运行状态轮询最多看到 2 秒前的结果；
- dry-run 暂存结果：解析后的 payload 缓存（对象存储仍是权威存储），
  命中可免去每次分页的全量下载解析；超大小上限的暂存不缓存。

Redis 不可用时全部走原路径（S3/数据库），主流程不受影响。
"""
from __future__ import annotations

import json
from typing import Any, Callable

from app.config import settings
from app.shared import redis_cache


def _enabled() -> bool:
    # 测试环境关闭：保证测试不依赖环境里是否存在 Redis，结果确定。
    if settings.environment.strip().lower() == "test":
        return False
    return bool(settings.pipeline_read_cache_enabled)


def cached_call(key: str, ttl_seconds: int, builder: Callable[[], Any]) -> Any:
    if not _enabled():
        return builder()
    return redis_cache.cache_aside(key, ttl_seconds, builder)


def runs_key(pipeline_id: str, limit: int) -> str:
    return f"ob:pl:runs:{pipeline_id}:{limit}"


def run_key(run_id: str) -> str:
    return f"ob:pl:run:{run_id}"


def dryrun_key(pipeline_id: str, dry_run_id: str) -> str:
    return f"ob:pl:dryrun:{pipeline_id}:{dry_run_id}"


def cache_dryrun_payload(
    key: str, payload: dict, max_bytes: int | None = None
) -> None:
    """暂存 payload 尽力回填；超过大小上限或 Redis 不可用时静默跳过。"""
    if not _enabled():
        return
    cap = (
        max_bytes
        if max_bytes is not None
        else settings.pipeline_dryrun_cache_max_bytes
    )
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    if len(raw.encode("utf-8")) > cap:
        return
    redis_cache.cache_set(key, payload, settings.pipeline_dryrun_cache_ttl_seconds)


def get_dryrun_payload(key: str) -> Any:
    if not _enabled():
        return None
    return redis_cache.cache_get(key)


def invalidate_pipeline_dryruns(pipeline_id: str) -> None:
    """重新试运行时清理旧暂存缓存，与对象存储的旧对象删除语义对齐。

    best-effort：Redis 不可用或删除失败时，旧键依赖 TTL 自然过期。
    """
    if not _enabled():
        return
    client = redis_cache.get_client()
    if client is None:
        return
    try:
        prefix = f"ob:pl:dryrun:{pipeline_id}:"
        for key in client.scan_iter(match=f"{prefix}*", count=100):
            client.delete(key)
    except Exception:  # noqa: BLE001 - 清理失败由 TTL 兜底
        pass
