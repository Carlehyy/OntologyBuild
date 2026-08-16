"""Manual execution entry point for Pipeline Tasks."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.data_channel.pipeline_tasks.models import PipelineTask

logger = logging.getLogger(__name__)


def trigger_task(
    task_id: str,
    background: Any,
    sync: bool,
    db: Session,
    full_refresh: bool = False,
) -> dict:
    task = (
        db.query(PipelineTask)
        .filter(PipelineTask.id == task_id)
        .first()
    )
    if not task:
        raise HTTPException(404, "PipelineTask not found")
    if task.status == "running":
        # 这里只做快速反馈；真正的并发边界在执行引擎的数据库原子租约。
        # 过期租约允许引擎恢复，不能被页面层的陈旧状态永久拦住。
        if (
            task.lease_expires_at is None
            or task.lease_expires_at > datetime.utcnow()
        ):
            raise HTTPException(409, "任务正在执行中，请稍后再试")
    if sync:
        # sync=true 是产品契约：前端阻塞等待本次执行结果，保持原地内联执行
        from app.data_channel.pipeline_tasks.engine import (
            execute_pipeline_task,
        )
        return execute_pipeline_task(
            task_id,
            trigger_type="manual",
            full_refresh=full_refresh,
        )

    # sync=false 经 NATS 派发给独立 executor 进程执行，不再占用 Web 进程
    from app.data_channel.pipeline_tasks.dispatch import dispatch_pipeline_task
    try:
        dispatch_pipeline_task(task_id, "manual", full_refresh=full_refresh)
    except Exception as exc:  # noqa: BLE001 - 任何通道故障对用户都是 503
        logger.error("PipelineTask %s 派发失败: %s", task_id, exc)
        raise HTTPException(503, "任务派发失败：消息通道不可用，请稍后重试")
    return {"status": "triggered", "task_id": task_id}
