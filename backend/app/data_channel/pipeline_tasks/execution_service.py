"""Manual execution entry point for Pipeline Tasks."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.data_channel.pipeline_tasks.models import PipelineTask


def trigger_task(
    task_id: str,
    background: Any,
    sync: bool,
    db: Session,
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
    from app.data_channel.pipeline_tasks.engine import (
        execute_pipeline_task,
    )

    if sync:
        return execute_pipeline_task(
            task_id,
            trigger_type="manual",
        )
    background.add_task(
        execute_pipeline_task,
        task_id,
        "manual",
    )
    return {"status": "triggered", "task_id": task_id}
