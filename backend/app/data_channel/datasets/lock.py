"""数据集写锁：串行化「读全量→内存合并→写新版」的读改写临界区。

任务调度与手动运行可能并发落同一个 curated 数据集，双方各自基于旧版本
合并后先提交的增量会被后提交者静默覆盖——必须在整个合并期间持锁。

锁存在数据所在的同一个数据库里（v2_dataset_write_locks，行即锁）：
- 获取 = INSERT，主键冲突说明有人持有，轮询等待
- 持有者崩溃不释放 → acquired_at 超过 stale_after 后可被接管（CAS 防双抢）
- 释放 = 按 (lock_key, owner) 删除，只释放自己的锁
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app.data_channel.datasets.models import DatasetWriteLock

logger = logging.getLogger(__name__)


class DatasetLockTimeout(RuntimeError):
    """等锁超时。抛给运行层让本次运行失败，好过静默丢增量。"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime) -> datetime:
    # SQLite 存不了时区，读回是 naive——按 UTC 补齐再比较
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _try_acquire(session_factory, lock_key: str, owner: str, stale_after: float) -> bool:
    session = session_factory()
    try:
        session.add(DatasetWriteLock(lock_key=lock_key, owner=owner, acquired_at=_utc_now()))
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        holder = session.query(DatasetWriteLock).filter(
            DatasetWriteLock.lock_key == lock_key).first()
        if holder is None:
            return False  # 持有者刚释放，下一轮再抢
        age = (_utc_now() - _as_aware(holder.acquired_at)).total_seconds()
        if age < stale_after:
            return False
        # 接管僵尸锁：带旧 owner 条件的 UPDATE 是 CAS，两个接管者只有一个成功
        taken = session.query(DatasetWriteLock).filter(
            DatasetWriteLock.lock_key == lock_key,
            DatasetWriteLock.owner == holder.owner,
        ).update({"owner": owner, "acquired_at": _utc_now()})
        session.commit()
        if taken:
            logger.warning(f"接管僵尸数据集写锁 {lock_key}（原持有者 {holder.owner} 已持锁 {age:.0f}s）")
        return bool(taken)
    except OperationalError as e:
        session.rollback()
        # SQLite 并发写忙碌属于可重试；表不存在等结构性错误必须立刻暴露
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            return False
        raise
    finally:
        session.close()


def _release(session_factory, lock_key: str, owner: str) -> None:
    session = session_factory()
    try:
        session.query(DatasetWriteLock).filter(
            DatasetWriteLock.lock_key == lock_key,
            DatasetWriteLock.owner == owner,
        ).delete()
        session.commit()
    except Exception:
        # 释放失败不掩盖业务结果；残留锁最终会因 stale_after 被接管
        logger.warning(f"释放数据集写锁 {lock_key} 失败，将由超时接管兜底", exc_info=True)
        session.rollback()
    finally:
        session.close()


@contextmanager
def dataset_write_lock(lock_key: str, *, bind,
                       wait_timeout: float = 300.0,
                       stale_after: float = 1800.0,
                       poll_interval: float = 0.5):
    """持有 lock_key 对应的数据集写锁执行代码块。

    bind: 数据所在库的 Engine（锁必须与被保护的数据同库，跨库锁不住）。
    wait_timeout: 等锁上限，超时抛 DatasetLockTimeout。
    stale_after: 持锁超过该秒数视为持有者已崩溃，可被接管。
    """
    session_factory = sessionmaker(bind=bind)
    owner = uuid.uuid4().hex
    deadline = time.monotonic() + wait_timeout
    while not _try_acquire(session_factory, lock_key, owner, stale_after):
        if time.monotonic() >= deadline:
            raise DatasetLockTimeout(
                f"等待数据集写锁超时（{wait_timeout:.0f}s）：{lock_key}。"
                f"可能有另一次运行正在对同一数据集入湖，请稍后重试")
        time.sleep(poll_interval)
    try:
        yield
    finally:
        _release(session_factory, lock_key, owner)
