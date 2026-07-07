"""流水线调度任务路由：CRUD、手动触发、执行历史、统计"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
import uuid

from app.deps import get_current_user, get_db
from app.data_channel.pipeline_tasks.models import PipelineTask
from app.models.v2.pipeline import Pipeline, PipelineRun

router = APIRouter(dependencies=[Depends(get_current_user)])

WRITE_MODES = ("overwrite", "append", "upsert", "append_dedup")


class PipelineTaskCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = ""
    pipeline_id: str
    write_mode: Literal["overwrite", "append", "upsert", "append_dedup"] = "overwrite"
    primary_key: Optional[str] = ""
    soft_delete_column: Optional[str] = ""
    skip_empty: bool = True
    schedule_type: Literal["MANUAL", "CRON", "INTERVAL"] = "MANUAL"
    cron_expression: Optional[str] = ""
    interval_seconds: Optional[int] = 0
    enabled: bool = True


class PipelineTaskUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    pipeline_id: Optional[str] = None
    write_mode: Optional[Literal["overwrite", "append", "upsert", "append_dedup"]] = None
    primary_key: Optional[str] = None
    soft_delete_column: Optional[str] = None
    skip_empty: Optional[bool] = None
    schedule_type: Optional[Literal["MANUAL", "CRON", "INTERVAL"]] = None
    cron_expression: Optional[str] = None
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None


def _validate(db: Session, body, existing: PipelineTask | None = None) -> None:
    def g(k):
        v = getattr(body, k, None)
        if v is None and existing is not None:
            return getattr(existing, k, None)
        return v

    pipeline_id = g("pipeline_id")
    if not pipeline_id:
        raise HTTPException(400, "必须选择要调度的流水线")
    pipe = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipe:
        raise HTTPException(400, "所选流水线不存在")
    if (pipe.status or "draft") != "published":
        raise HTTPException(400, f"流水线「{pipe.name}」尚未发布，任务只能调度已发布的流水线。请先在流水线画布中完成发布。")

    if g("write_mode") == "upsert" and not (g("primary_key") or "").strip():
        raise HTTPException(400, "「主键合并」入库方式必须指定主键列")

    schedule_type = g("schedule_type")
    if schedule_type == "CRON":
        expr = (g("cron_expression") or "").strip()
        if not expr:
            raise HTTPException(400, "CRON 调度必须填写 cron 表达式")
        if len(expr.split()) != 5:
            raise HTTPException(400, "cron 表达式须为 5 段格式：分 时 日 月 周")
    elif schedule_type == "INTERVAL":
        iv = g("interval_seconds")
        if not iv or iv < 10:
            raise HTTPException(400, "固定间隔调度的间隔必须 ≥ 10 秒")


def _refresh_scheduler(task_id: str) -> None:
    try:
        from app.data_channel.sync_tasks.scheduler import get_sync_scheduler
        get_sync_scheduler().reload_pipeline_task(task_id)
    except Exception:
        pass


def _with_pipeline_info(db: Session, tasks: list[PipelineTask]) -> list[dict]:
    pipe_map = {p.id: p for p in db.query(Pipeline).all()}
    items = []
    for t in tasks:
        d = t.to_dict()
        pipe = pipe_map.get(t.pipeline_id)
        d["pipeline_name"] = pipe.name if pipe else "(已删除)"
        d["pipeline_status"] = (pipe.status or "draft") if pipe else "deleted"
        d["pipeline_version"] = (pipe.version or 1) if pipe else None
        items.append(d)
    return items


# ========== 固定路径（必须放在 /{task_id} 之前） ==========

@router.get("/stats")
def stats_overview(db: Session = Depends(get_db)):
    total = db.query(PipelineTask).count()
    running = db.query(PipelineTask).filter(PipelineTask.status == "running").count()
    enabled = db.query(PipelineTask).filter(PipelineTask.enabled.is_(True)).count()
    failed = db.query(PipelineTask).filter(PipelineTask.status == "failed").count()

    today_start = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    today_runs = db.query(PipelineRun).filter(
        PipelineRun.task_id.isnot(None),
        PipelineRun.created_at >= today_start,
    ).count()
    return {
        "total": total,
        "running": running,
        "enabled": enabled,
        "failed": failed,
        "today_runs": today_runs,
    }


# ========== CRUD ==========

@router.post("", status_code=201)
def create_task(body: PipelineTaskCreate, db: Session = Depends(get_db)):
    _validate(db, body)
    task = PipelineTask(
        id=str(uuid.uuid4()),
        name=body.name,
        description=body.description or "",
        pipeline_id=body.pipeline_id,
        write_mode=body.write_mode,
        primary_key=(body.primary_key or "").strip(),
        soft_delete_column=(body.soft_delete_column or "").strip(),
        skip_empty=body.skip_empty,
        schedule_type=body.schedule_type,
        cron_expression=body.cron_expression or "",
        interval_seconds=body.interval_seconds or 0,
        enabled=body.enabled,
        status="idle",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    _refresh_scheduler(task.id)
    return _with_pipeline_info(db, [task])[0]


@router.get("")
def list_tasks(
    search: Optional[str] = None,
    status: Optional[str] = None,
    enabled: Optional[bool] = None,
    pipeline_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(PipelineTask)
    if search:
        q = q.filter(PipelineTask.name.ilike(f"%{search}%"))
    if status:
        q = q.filter(PipelineTask.status == status)
    if enabled is not None:
        q = q.filter(PipelineTask.enabled.is_(enabled))
    if pipeline_id:
        q = q.filter(PipelineTask.pipeline_id == pipeline_id)
    total = q.count()
    tasks = q.order_by(PipelineTask.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "items": _with_pipeline_info(db, tasks), "page": page, "page_size": page_size}


@router.get("/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "PipelineTask not found")
    return _with_pipeline_info(db, [task])[0]


@router.put("/{task_id}")
def update_task(task_id: str, body: PipelineTaskUpdate, db: Session = Depends(get_db)):
    task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "PipelineTask not found")
    _validate(db, body, existing=task)
    for field, val in body.model_dump(exclude_unset=True).items():
        setattr(task, field, val)
    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    _refresh_scheduler(task.id)
    return _with_pipeline_info(db, [task])[0]


@router.delete("/{task_id}")
def delete_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "PipelineTask not found")
    db.delete(task)
    db.commit()
    _refresh_scheduler(task_id)
    return {"status": "ok"}


@router.post("/{task_id}/toggle")
def toggle_task(task_id: str, enabled: bool, db: Session = Depends(get_db)):
    task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "PipelineTask not found")
    task.enabled = enabled
    task.updated_at = datetime.utcnow()
    db.commit()
    _refresh_scheduler(task.id)
    return task.to_dict()


@router.post("/{task_id}/trigger")
def trigger_task(
    task_id: str,
    background: BackgroundTasks,
    sync: bool = False,
    db: Session = Depends(get_db),
):
    task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "PipelineTask not found")
    if task.status == "running":
        raise HTTPException(409, "任务正在执行中，请稍后再试")
    from app.data_channel.pipeline_tasks.engine import execute_pipeline_task

    if sync:
        return execute_pipeline_task(task_id, trigger_type="manual")
    background.add_task(execute_pipeline_task, task_id, "manual")
    return {"status": "triggered", "task_id": task_id}


@router.get("/{task_id}/histories")
def list_histories(
    task_id: str,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "PipelineTask not found")
    q = db.query(PipelineRun).filter(PipelineRun.task_id == task_id)
    total = q.count()
    runs = q.order_by(PipelineRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for r in runs:
        stats = r.stats or {}
        items.append({
            "id": r.id,
            "status": r.status,
            "trigger_type": stats.get("trigger_type", "manual"),
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "rows_in": stats.get("rows_in", 0),
            "rows_out": stats.get("rows_out", 0),
            "lake_rows": stats.get("lake_rows"),
            "write_mode": stats.get("write_mode"),
            "skipped_outputs": stats.get("skipped_outputs"),
            "curated_dataset_ids": stats.get("curated_dataset_ids", []),
            "error_message": r.error_log or "",
        })
    return {"total": total, "items": items, "page": page, "page_size": page_size}
