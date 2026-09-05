"""Pipeline Task creation, mutation, deletion, and scheduler refresh."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.data_channel.pipeline_tasks import cache as _cache
from app.data_channel.pipeline_tasks.models import PipelineTask
from app.data_channel.pipelines.models import Pipeline


LifecycleDependency = Callable[..., Any]


def _refresh_scheduler(task_id: str) -> None:
    try:
        from app.data_channel.sync_tasks.scheduler import (
            get_sync_scheduler,
        )

        get_sync_scheduler().reload_pipeline_task(task_id)
    except Exception:
        pass


def create_task(
    body: Any,
    db: Session,
    current_user: Any,
    *,
    validate_fn: LifecycleDependency,
    refresh_scheduler_fn: LifecycleDependency,
    with_pipeline_info_fn: LifecycleDependency,
) -> dict:
    _, pipeline_primary_key = validate_fn(db, body)
    task = PipelineTask(
        id=str(uuid.uuid4()),
        name=body.name,
        description=body.description,
        pipeline_id=body.pipeline_id,
        write_mode=body.write_mode,
        primary_key=pipeline_primary_key,
        soft_delete_column=(
            body.soft_delete_column or ""
        ).strip(),
        cursor_column=(body.cursor_column or "").strip(),
        skip_empty=body.skip_empty,
        schedule_type=body.schedule_type,
        cron_expression=body.cron_expression or "",
        interval_seconds=body.interval_seconds or 0,
        enabled=body.enabled,
        status="idle",
        created_by=getattr(current_user, "id", None),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    refresh_scheduler_fn(task.id)
    _cache.invalidate_all()
    return with_pipeline_info_fn(db, [task])[0]


def update_task(
    task_id: str,
    body: Any,
    db: Session,
    *,
    validate_fn: LifecycleDependency,
    refresh_scheduler_fn: LifecycleDependency,
    with_pipeline_info_fn: LifecycleDependency,
) -> dict:
    task = (
        db.query(PipelineTask)
        .filter(PipelineTask.id == task_id)
        .first()
    )
    if not task:
        raise HTTPException(404, "PipelineTask not found")
    _, pipeline_primary_key = validate_fn(
        db,
        body,
        existing=task,
    )
    previous_cursor_column = task.cursor_column or ""
    for field, value in body.model_dump(
        exclude_unset=True
    ).items():
        if field == "primary_key":
            continue
        setattr(task, field, value)
    # 游标列被修改/清空时，旧水位对新列不再可比较，必须归零——下次运行
    # 按全量重建水位，避免沿用旧列的水位静默漏数
    if (task.cursor_column or "") != previous_cursor_column:
        task.last_cursor_value = ""
    # 兼容字段只保留当前发布契约快照，修复历史任务可能存在的自定义值。
    task.primary_key = pipeline_primary_key
    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    refresh_scheduler_fn(task.id)
    _cache.invalidate_all()
    return with_pipeline_info_fn(db, [task])[0]


def delete_task(
    task_id: str,
    db: Session,
    *,
    refresh_scheduler_fn: LifecycleDependency,
) -> dict:
    task = (
        db.query(PipelineTask)
        .filter(PipelineTask.id == task_id)
        .first()
    )
    if not task:
        raise HTTPException(404, "PipelineTask not found")
    db.delete(task)
    db.commit()
    refresh_scheduler_fn(task_id)
    _cache.invalidate_all()
    return {"status": "ok"}


def toggle_task(
    task_id: str,
    enabled: bool,
    db: Session,
    *,
    refresh_scheduler_fn: LifecycleDependency,
) -> dict:
    task = (
        db.query(PipelineTask)
        .filter(PipelineTask.id == task_id)
        .first()
    )
    if not task:
        raise HTTPException(404, "PipelineTask not found")
    if enabled:
        pipeline = (
            db.query(Pipeline)
            .filter(Pipeline.id == task.pipeline_id)
            .first()
        )
        if (
            not pipeline
            or (pipeline.status or "draft") != "published"
            or pipeline.enabled is False
        ):
            raise HTTPException(
                409,
                "关联流水线未发布或已停用，不能启用该调度任务",
            )
    task.enabled = enabled
    task.updated_at = datetime.utcnow()
    db.commit()
    refresh_scheduler_fn(task.id)
    _cache.invalidate_all()
    return task.to_dict()
