"""Pipeline Task run-history filtering, projection, and audit queries."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.data_channel.pipeline_tasks.contracts import (
    HistoryStatus,
    HistoryTriggerType,
)
from app.data_channel.pipeline_tasks.models import PipelineTask
from app.data_channel.pipeline_tasks.query_service import (
    _as_utc,
    _utc_iso,
)
from app.models.v2.pipeline import Pipeline, PipelineRun


HistoryDependency = Callable[..., Any]


def _validate_history_query(
    page: int,
    page_size: int,
    created_from: Optional[datetime],
    created_to: Optional[datetime],
) -> None:
    if page < 1:
        raise HTTPException(400, "page 必须大于等于 1")
    if page_size < 1 or page_size > 100:
        raise HTTPException(400, "page_size 必须在 1 到 100 之间")
    if (
        created_from
        and created_to
        and _as_utc(created_from) > _as_utc(created_to)
    ):
        raise HTTPException(400, "开始时间不能晚于结束时间")


def _apply_history_filters(
    query,
    status: Optional[HistoryStatus],
    trigger_type: Optional[HistoryTriggerType],
    created_from: Optional[datetime],
    created_to: Optional[datetime],
):
    if status:
        query = query.filter(PipelineRun.status == status)
    if trigger_type:
        trigger_expression = (
            PipelineRun.stats["trigger_type"].as_string()
        )
        if trigger_type == "manual":
            query = query.filter(
                or_(
                    PipelineRun.stats.is_(None),
                    trigger_expression.is_(None),
                    trigger_expression == "manual",
                )
            )
        else:
            query = query.filter(
                trigger_expression == trigger_type
            )
    if created_from:
        query = query.filter(
            PipelineRun.created_at
            >= _as_utc(created_from).replace(tzinfo=None)
        )
    if created_to:
        query = query.filter(
            PipelineRun.created_at
            <= _as_utc(created_to).replace(tzinfo=None)
        )
    return query


def _history_item(run: PipelineRun) -> dict:
    stats = run.stats or {}
    return {
        "id": run.id,
        "status": run.status,
        "trigger_type": stats.get("trigger_type", "manual"),
        "created_at": _utc_iso(run.created_at),
        "started_at": _utc_iso(run.started_at),
        "finished_at": _utc_iso(run.finished_at),
        "rows_in": stats.get("rows_in", 0),
        "rows_out": stats.get("rows_out", 0),
        "lake_rows": stats.get("lake_rows"),
        "write_mode": stats.get("write_mode"),
        "skipped_outputs": stats.get("skipped_outputs"),
        "curated_dataset_ids": stats.get(
            "curated_dataset_ids",
            [],
        ),
        "lake_impact": stats.get("lake_impact"),
        "config_snapshot": stats.get("config_snapshot"),
        "error_message": run.error_log or "",
    }


def list_all_histories(
    search: Optional[str],
    pipeline_id: Optional[str],
    page: int,
    page_size: int,
    status: Optional[HistoryStatus],
    trigger_type: Optional[HistoryTriggerType],
    created_from: Optional[datetime],
    created_to: Optional[datetime],
    db: Session,
    *,
    validate_history_query_fn: HistoryDependency,
    apply_history_filters_fn: HistoryDependency,
    history_item_fn: HistoryDependency,
) -> dict:
    validate_history_query_fn(
        page,
        page_size,
        created_from,
        created_to,
    )
    query = (
        db.query(PipelineRun, PipelineTask, Pipeline)
        .join(PipelineTask, PipelineTask.id == PipelineRun.task_id)
        .outerjoin(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
    )
    keyword = (search or "").strip()
    if keyword:
        query = query.filter(
            or_(
                PipelineTask.name.ilike(f"%{keyword}%"),
                Pipeline.name.ilike(f"%{keyword}%"),
            )
        )
    if pipeline_id:
        query = query.filter(
            PipelineRun.pipeline_id == pipeline_id
        )
    query = apply_history_filters_fn(
        query,
        status,
        trigger_type,
        created_from,
        created_to,
    )
    total = query.count()
    rows = (
        query.order_by(
            PipelineRun.created_at.desc(),
            PipelineRun.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = []
    for run, task, pipeline in rows:
        item = history_item_fn(run)
        item.update(
            {
                "task_id": task.id,
                "task_name": task.name,
                "pipeline_id": run.pipeline_id,
                "pipeline_name": (
                    pipeline.name
                    if pipeline
                    else "(流水线已删除)"
                ),
            }
        )
        items.append(item)
    return {
        "total": total,
        "items": items,
        "page": page,
        "page_size": page_size,
    }


def list_histories(
    task_id: str,
    page: int,
    page_size: int,
    status: Optional[HistoryStatus],
    trigger_type: Optional[HistoryTriggerType],
    created_from: Optional[datetime],
    created_to: Optional[datetime],
    db: Session,
    *,
    validate_history_query_fn: HistoryDependency,
    apply_history_filters_fn: HistoryDependency,
    history_item_fn: HistoryDependency,
) -> dict:
    validate_history_query_fn(
        page,
        page_size,
        created_from,
        created_to,
    )
    task = (
        db.query(PipelineTask)
        .filter(PipelineTask.id == task_id)
        .first()
    )
    if not task:
        raise HTTPException(404, "PipelineTask not found")
    query = apply_history_filters_fn(
        db.query(PipelineRun).filter(
            PipelineRun.task_id == task_id
        ),
        status,
        trigger_type,
        created_from,
        created_to,
    )
    total = query.count()
    runs = (
        query.order_by(
            PipelineRun.created_at.desc(),
            PipelineRun.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [history_item_fn(run) for run in runs]
    return {
        "total": total,
        "items": items,
        "page": page,
        "page_size": page_size,
    }


def run_audit(
    task_id: str,
    run_id: str,
    db: Session,
) -> dict:
    run = (
        db.query(PipelineRun)
        .filter(
            PipelineRun.id == run_id,
            PipelineRun.task_id == task_id,
        )
        .first()
    )
    if not run:
        raise HTTPException(404, "执行记录不存在")
    stats = run.stats or {}
    pipeline = (
        db.query(Pipeline)
        .filter(Pipeline.id == run.pipeline_id)
        .first()
    )

    from app.models.v2.dataset import Dataset

    outputs = []
    for output in (
        (stats.get("meta", {}) or {}).get("outputs", []) or []
    ):
        curated_dataset_id = output.get("curated_dataset_id")
        curated_dataset_name = output.get(
            "curated_dataset_name"
        )
        if not curated_dataset_name and curated_dataset_id:
            dataset = (
                db.query(Dataset)
                .filter(Dataset.id == curated_dataset_id)
                .first()
            )
            curated_dataset_name = (
                dataset.name if dataset else None
            )
        outputs.append(
            {
                "curated_dataset_id": curated_dataset_id,
                "curated_dataset_name": curated_dataset_name,
                "version_no": output.get("version_no"),
                "dataset_version_id": output.get(
                    "dataset_version_id"
                ),
                "table_name": output.get("table_name"),
                "rows_out": output.get("rows_out"),
                "lake_rows": output.get("lake_rows"),
                "primary_key": output.get("primary_key"),
                "output_columns": output.get(
                    "output_columns"
                )
                or [],
                "output_sample": output.get("output_sample")
                or [],
                "lake_impact": output.get("lake_impact"),
                "skipped": output.get("skipped"),
                "gate_warnings": output.get("gate_warnings"),
            }
        )

    return {
        "id": run.id,
        "task_id": task_id,
        "status": run.status,
        "trigger_type": stats.get("trigger_type", "manual"),
        "started_at": (
            run.started_at.isoformat() if run.started_at else None
        ),
        "finished_at": (
            run.finished_at.isoformat()
            if run.finished_at
            else None
        ),
        "created_at": (
            run.created_at.isoformat() if run.created_at else None
        ),
        "rows_in": stats.get("rows_in", 0),
        "rows_out": stats.get("rows_out", 0),
        "lake_rows": stats.get("lake_rows"),
        "write_mode": stats.get("write_mode"),
        "lake_impact": stats.get("lake_impact"),
        "config_snapshot": stats.get("config_snapshot"),
        "pipeline": {
            "id": run.pipeline_id,
            "name": (
                pipeline.name if pipeline else "(已删除)"
            ),
            "version": pipeline.version if pipeline else None,
            "status": (
                pipeline.status if pipeline else "deleted"
            ),
            "domain": pipeline.domain if pipeline else None,
        },
        "outputs": outputs,
        "error_message": run.error_log or "",
    }
