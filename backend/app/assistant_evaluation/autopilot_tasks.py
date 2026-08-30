"""assistant_evaluation.autopilot.cycle — 候守循环的 NATS 消费入口。

与超级助手反思链路同构：异步壳 + asyncio.to_thread 执行同步服务；
业务异常在服务层内消化为 cycle error 状态（含熔断计数），不向消费
框架抛出导致消息反复重投。
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_autopilot_cycle_message(payload: dict) -> None:
    config_id = str(payload.get("config_id") or "").strip()
    if not config_id:
        logger.warning("值守循环消息缺少 config_id，已忽略")
        return
    from app.assistant_evaluation import autopilot_service

    result = await asyncio.to_thread(autopilot_service.run_cycle, config_id)
    logger.info("值守循环完成：config=%s status=%s",
                config_id[:8], (result or {}).get("status"))
