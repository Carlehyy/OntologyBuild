"""记忆宫殿图谱抽取的 NATS 消息处理器（executor 进程内运行）。

与反思 handler 同一模式；两点差异：

1. 抽取是分钟级长任务，handler 内用进程级单飞闸（threading.Semaphore(1)）
   把本类任务的并发压到 1：executor 的共享并发信号量（默认 2）至少保留
   一个名额给 reflect.* 等短任务，杜绝建图饿死反思。
2. 消息等待期间由 executor 的 in_progress 周期续约兜底，单飞闸排队不会
   触发 ack_wait 重投；业务异常在 handler 内消化（build 行自身记 error），
   不向外抛——逃到 executor 的异常会被 nak 重投，而抽取失败重投无意义。
"""
from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger(__name__)

# 进程级单飞闸：同 executor 进程内至多 1 个抽取在途（并发隔离纪律）
_PALACE_SLOT = threading.Semaphore(1)


async def run_palace_extract_message(payload: dict) -> None:
    """super_assistant.palace.extract：单文件的图谱抽取。"""
    owner_id = str(payload["owner_id"])
    file_id = str(payload["file_id"])

    from app.database import SessionLocal
    from app.super_assistant import palace_service

    def _run() -> None:
        with _PALACE_SLOT:
            db = SessionLocal()
            try:
                palace_service.run_build(db, owner_id, file_id)
                logger.info("记忆宫殿图谱抽取完成（file=%s）", file_id)
            except Exception:
                logger.exception("记忆宫殿图谱抽取执行失败（file=%s）", file_id)
            finally:
                db.close()

    await asyncio.to_thread(_run)
