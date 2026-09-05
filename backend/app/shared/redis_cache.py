"""Redis 只读加速缓存（fail-open）。

设计约束（产品契约）：
- Redis 只是加速器，不是主流程的一部分：任何连接/读写异常都静默降级，
  调用方始终能走原有直查路径，Redis 故障绝不影响接口可用性与正确性。
- 只缓存 JSON 可序列化的只读结果；写操作继续以数据库为唯一事实源。
- 键默认落在 db 1；本模块自含连接管理，不依赖任何任务系统。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

from redis import Redis

from app.config import settings

logger = logging.getLogger(__name__)

# 连接失败后的退避窗口：Redis 宕机期间不重复支付连接超时成本。
_CLIENT_RETRY_BACKOFF_SECONDS = 30.0

_client: Optional[Redis] = None
_client_failed_at: float = 0.0


def _cache_db_url() -> str:
    """缓存统一落 db 1；兼容 rediss 与无 db 后缀的 REDIS_URL。"""
    url = settings.redis_url or ""
    if not url:
        return ""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    path_sep = rest.rfind("/")
    if path_sep > 0 and rest[path_sep + 1 :].isdigit():
        return f"{scheme}://{rest[:path_sep]}/1"
    return f"{url}/1"


def _mark_failed() -> None:
    global _client, _client_failed_at
    _client = None
    _client_failed_at = time.monotonic() + _CLIENT_RETRY_BACKOFF_SECONDS


def get_client() -> Optional[Redis]:
    """惰性获取缓存客户端；不可用返回 None（fail-open）。

    建连不做 ping 验证：首次命令失败会走异常路径并进入退避。
    """
    global _client, _client_failed_at
    if _client is not None:
        return _client
    if _client_failed_at and time.monotonic() < _client_failed_at:
        return None
    try:
        _client = Redis.from_url(
            _cache_db_url(),
            socket_timeout=0.5,
            socket_connect_timeout=0.5,
            decode_responses=True,
        )
    except Exception as exc:  # noqa: BLE001 - 任何初始化失败都降级直查
        logger.warning("Redis 缓存客户端初始化失败（降级直查）: %s", exc)
        _mark_failed()
    return _client


def cache_get(key: str) -> Optional[Any]:
    client = get_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        return json.loads(raw) if raw is not None else None
    except Exception as exc:  # noqa: BLE001 - 读取失败视同未命中
        logger.warning("Redis 缓存读取失败（降级直查）: %s", exc)
        _mark_failed()
        return None


def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    client = get_client()
    if client is None:
        return
    try:
        client.set(
            key,
            json.dumps(value, ensure_ascii=False, default=str),
            ex=ttl_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - 回填失败不影响返回
        logger.warning("Redis 缓存回填失败（忽略）: %s", exc)
        _mark_failed()


def cache_bump(version_key: str) -> None:
    """版本键自增：旧键依赖 TTL 自然过期，不做主动清扫（best-effort）。"""
    client = get_client()
    if client is None:
        return
    try:
        client.incr(version_key)
    except Exception as exc:  # noqa: BLE001 - 失效失败由短 TTL 兜底
        logger.warning("Redis 缓存失效失败（由 TTL 兜底）: %s", exc)
        _mark_failed()


def cache_version(version_key: str) -> str:
    client = get_client()
    if client is None:
        return "0"
    try:
        value = client.get(version_key)
        return value if value else "0"
    except Exception as exc:  # noqa: BLE001 - 版本读取失败按 0 处理
        logger.warning("Redis 缓存版本读取失败（按 0 处理）: %s", exc)
        _mark_failed()
        return "0"


def cache_aside(key: str, ttl_seconds: int, builder: Callable[[], Any]) -> Any:
    """cache-aside：命中返回缓存；未命中执行 builder 并尽力回填。

    builder 抛出的异常（含 HTTPException）原样上抛且不缓存——
    缓存层不吞业务错误。
    """
    cached = cache_get(key)
    if cached is not None:
        return cached
    value = builder()
    cache_set(key, value, ttl_seconds)
    return value
