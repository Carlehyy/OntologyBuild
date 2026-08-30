"""值守定时器 — APScheduler 进程内定时 + NATS JetStream 派发。

平台新任务模式（Celery 已列入退役计划，见 AGENTS.md）：定时半边由
APScheduler 在 API 进程内驱动（与数据同步任务池同模式），执行半边
经 dispatch_task 投递给 nats_executor 消费进程——LLM 重活不占 Web
线程。每 5 分钟扫描一次到期配置；派发成功即标记 last_dispatched_at，
进程重启也不会重复触发同一时段。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
SCAN_INTERVAL_MINUTES = 5
_JOB_ID = "assistant-eval-autopilot-dispatch"


def _dispatch_due_cycles() -> None:
    from zoneinfo import ZoneInfo

    from app.assistant_evaluation.autopilot_service import is_due
    from app.assistant_evaluation.models import AssistantEvalAutopilotConfig
    from app.shared.database import SessionLocal

    db = SessionLocal()
    try:
        now_local = datetime.now(ZoneInfo("Asia/Shanghai"))
        configs = (
            db.query(AssistantEvalAutopilotConfig)
            .filter(AssistantEvalAutopilotConfig.enabled.is_(True),
                    AssistantEvalAutopilotConfig.suspended.is_(False))
            .all()
        )
        for config in configs:
            if not is_due(config, now_local):
                continue
            try:
                from app.data_channel.pipeline_tasks.dispatch import (
                    dispatch_assistant_eval_autopilot,
                )

                dispatch_assistant_eval_autopilot(config.id)
                config.last_dispatched_at = datetime.now(timezone.utc)
                db.commit()
                logger.info("值守循环已派发：config=%s ontology=%s run_at=%s",
                            config.id[:8], config.ontology_id[:8], config.run_at)
            except Exception:  # noqa: BLE001 — 单个配置派发失败不影响其余
                db.rollback()
                logger.exception("值守循环派发失败：config=%s", config.id)
    finally:
        db.close()


def start() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _scheduler.add_job(
        _dispatch_due_cycles, "interval", minutes=SCAN_INTERVAL_MINUTES,
        id=_JOB_ID, max_instances=1, coalesce=True)
    _scheduler.start()
    logger.info("助手评估值守定时器已启动（每 %s 分钟扫描）", SCAN_INTERVAL_MINUTES)


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
