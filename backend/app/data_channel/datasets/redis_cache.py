"""数据资产湖 Redis 读缓存（cache-aside，失败即降级）。

资产湖（/data/structured，/api/v2/datasets）的高频读路径——总览列表、
数据预览、字段契约、统计信息——叠加一层 Redis 读缓存，把重复的数据库
查询与文件解析挡在缓存之外。

主流程安全约束（本模块全部对外函数保证）：

- **永不抛出**：连接失败 / 超时 / 反序列化失败一律吞掉，调用方拿不到缓存
  就走原读取路径，行为与未启用缓存完全一致；
- **熔断冷却**：Redis 连续失败后短暂停用缓存（默认 10 秒），避免 Redis
  抖动时给每个请求追加 socket 超时等待；
- **键空间即失效**：数据集版本内容不可变，预览/契约/统计的缓存键携带
  version id，内容变化必然产生新键，历史键靠 TTL 自然回收；总览列表是
  聚合视图，用短 TTL + 写路径尽力失效兜底。

键命名空间统一为 ``lake:cache:*``，便于运维侧统一观察与清理。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

from app.config import settings

logger = logging.getLogger(__name__)

#: 总览列表是聚合视图，短 TTL 保证新建/删除/上传后 10 秒内自然校正。
OVERVIEW_TTL_SECONDS: int = 10
#: 版本内容不可变：键携带 version id，TTL 只负责回收内存，不负责正确性。
VERSION_TTL_SECONDS: int = 1800
#: 单条缓存值的字节上限；超限不缓存，避免把大文件预览塞进 Redis。
MAX_PAYLOAD_BYTES: int = 512 * 1024
#: SCAN 失效的轮数上限，把单次失效耗时封顶（每轮 200 个键）。
MAX_SCAN_ROUNDS: int = 100

# Redis 抖动时的停用窗口：此窗口内所有缓存操作直接短路为「未命中」。
COOLDOWN_SECONDS: float = 10.0

_PREFIX: str = "lake:cache:"

_client: Any = None
_client_lock = threading.Lock()
_last_failure_mono: float = 0.0


def _json_default(value: Any) -> str:
    """序列化兜底：非原生 JSON 值（如 datetime）降级为字符串而非报错。"""
    return str(value)


def _client_or_none() -> Any:
    """惰性建连；建连失败或处于冷却期返回 None（缓存整体降级）。"""
    global _client, _last_failure_mono
    with _client_lock:
        if _client is not None:
            return _client
        now = time.monotonic()
        if now - _last_failure_mono < COOLDOWN_SECONDS:
            return None
        try:
            import redis

            _client = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.3,
                socket_timeout=0.5,
                max_connections=8,
                health_check_interval=30,
            )
        except Exception:  # noqa: BLE001 — 缓存不可用不影响主流程
            logger.debug("数据资产湖缓存：Redis 客户端创建失败，本次降级", exc_info=True)
            _last_failure_mono = now
            return None
        return _client


def _note_failure() -> None:
    """操作失败：丢弃客户端并进入冷却期。"""
    global _client, _last_failure_mono
    with _client_lock:
        client, _client = _client, None
        _last_failure_mono = time.monotonic()
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def get_json(key: str) -> Any:
    """读取并解析缓存值；未命中/损坏/Redis 不可用统一返回 None。"""
    client = _client_or_none()
    if client is None:
        return None
    try:
        raw = client.get(key)
    except Exception:  # noqa: BLE001
        _note_failure()
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001 — 脏数据按未命中处理
        logger.debug("数据资产湖缓存：键 %s 解析失败，按未命中处理", key)
        return None


def set_json(key: str, value: Any, ttl_seconds: int) -> bool:
    """写缓存；失败/超限静默返回 False，绝不阻断主流程。"""
    client = _client_or_none()
    if client is None:
        return False
    try:
        payload = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), default=_json_default
        )
    except Exception:  # noqa: BLE001 — 序列化失败跳过缓存
        return False
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return False
    try:
        client.set(key, payload, ex=int(ttl_seconds))
        return True
    except Exception:  # noqa: BLE001
        _note_failure()
        return False


def delete_prefix(prefix: str) -> int:
    """SCAN 删除匹配前缀的键（尽力而为），返回删除个数。"""
    client = _client_or_none()
    if client is None:
        return 0
    deleted = 0
    try:
        cursor = 0
        for _ in range(MAX_SCAN_ROUNDS):
            cursor, keys = client.scan(cursor=cursor, match=f"{prefix}*", count=200)
            if keys:
                client.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
    except Exception:  # noqa: BLE001
        _note_failure()
    return deleted


def cache_aside(key: str, ttl_seconds: int, loader: Callable[[], Any]) -> Any:
    """命中即返回缓存；未命中回源并把结果尽力写回。

    任何 Redis 异常都不改变返回值语义：调用方始终拿到 loader 的等价结果。
    """
    cached = get_json(key)
    if cached is not None:
        return cached
    value = loader()
    set_json(key, value, ttl_seconds)
    return value


def invalidate_overview() -> int:
    """写路径调用：尽力失效总览列表缓存。"""
    return delete_prefix(f"{_PREFIX}overview:")
