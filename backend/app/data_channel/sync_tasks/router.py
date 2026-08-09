"""V2 数据同步任务（DataSyncTask）路由：CRUD、手动触发、历史记录"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
import uuid

from app.deps import get_current_user, get_db
from app.models.v2.sync_task import DataSyncTask, DataSyncHistory, SyncMode, ScheduleType, SyncStatus, TriggerType, HistoryStatus
from app.models.v2.connection import Connection
from app.models.v2.pipeline import Pipeline

router = APIRouter(dependencies=[Depends(get_current_user)])

_RETIRED_DETAIL = (
    "旧版 DataSyncTask 已停用。请先发布并启用 n8n 流水线，"
    "再到数据任务池创建 PipelineTask；存量任务仅保留只读审计。"
)


def _reject_retired_write() -> None:
    raise HTTPException(status_code=410, detail=_RETIRED_DETAIL)


class SyncTaskCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = ""
    connection_id: str
    source_table: Optional[str] = ""
    source_query: Optional[str] = ""
    sync_mode: Literal["SNAPSHOT", "APPEND"] = "SNAPSHOT"
    primary_key: Optional[str] = ""
    watermark_column: Optional[str] = ""
    is_deleted_column: Optional[str] = ""
    schedule_type: Literal["MANUAL", "CRON", "INTERVAL"] = "MANUAL"
    cron_expression: Optional[str] = ""
    interval_seconds: Optional[int] = 0
    enabled: bool = True
    trigger_pipeline_id: Optional[str] = ""


class SyncTaskUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    source_table: Optional[str] = None
    source_query: Optional[str] = None
    sync_mode: Optional[Literal["SNAPSHOT", "APPEND"]] = None
    primary_key: Optional[str] = None
    watermark_column: Optional[str] = None
    is_deleted_column: Optional[str] = None
    schedule_type: Optional[Literal["MANUAL", "CRON", "INTERVAL"]] = None
    cron_expression: Optional[str] = None
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None
    trigger_pipeline_id: Optional[str] = None


def _validate_task(db: Session, body, is_create: bool) -> None:
    def g(k):
        return getattr(body, k, None)

    if is_create:
        conn_id = g("connection_id")
        conn = db.query(Connection).filter(Connection.id == conn_id).first()
        if not conn:
            raise HTTPException(400, f"Connection {conn_id} 不存在")

    pipeline_id = g("trigger_pipeline_id")
    if pipeline_id:
        pipe = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pipe:
            raise HTTPException(400, f"Pipeline {pipeline_id} 不存在")
        if (pipe.status or "draft") != "published":
            raise HTTPException(
                400,
                f"流水线「{pipe.name}」尚未发布，任务只能触发已发布的流水线。请先在编辑向导中完成发布。",
            )

    sync_mode = g("sync_mode")
    if sync_mode == "APPEND":
        watermark = g("watermark_column")
        if is_create and not watermark:
            raise HTTPException(400, "APPEND 模式必须指定 watermark_column")

    schedule_type = g("schedule_type")
    if schedule_type == "CRON":
        if is_create and not g("cron_expression"):
            raise HTTPException(400, "CRON 调度必须填写 cron_expression")
    elif schedule_type == "INTERVAL":
        iv = g("interval_seconds")
        if is_create and (not iv or iv < 10):
            raise HTTPException(400, "INTERVAL 调度 interval_seconds 必须 ≥ 10 秒")


def _refresh_scheduler(task_id: str) -> None:
    try:
        from app.services.v2.sync_scheduler import get_sync_scheduler
        get_sync_scheduler().reload_task(task_id)
    except Exception:
        pass


# ========== 固定路径（必须放在 /{task_id} 之前） ==========

@router.get("/stats")
def stats_overview(db: Session = Depends(get_db)):
    total = db.query(DataSyncTask).count()
    running = db.query(DataSyncTask).filter(DataSyncTask.status == "running").count()
    enabled = db.query(DataSyncTask).filter(DataSyncTask.enabled.is_(True)).count()
    failed = db.query(DataSyncTask).filter(DataSyncTask.status == "failed").count()

    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())
    today_runs = db.query(DataSyncHistory).filter(DataSyncHistory.started_at >= today_start).count()
    success_runs = db.query(DataSyncHistory).filter(
        DataSyncHistory.started_at >= today_start,
        DataSyncHistory.status == HistoryStatus.SUCCESS.value,
    ).count()
    success_rate = round(success_runs / today_runs * 100, 1) if today_runs > 0 else 100.0

    mode_dist: dict = {}
    for t in db.query(DataSyncTask).all():
        mode_dist[t.sync_mode] = mode_dist.get(t.sync_mode, 0) + 1

    return {
        "total": total,
        "running": running,
        "enabled": enabled,
        "failed": failed,
        "today_runs": today_runs,
        "success_rate": success_rate,
        "mode_distribution": mode_dist,
    }


@router.get("/scheduler/status")
def scheduler_status():
    try:
        from app.services.v2.sync_scheduler import get_sync_scheduler
        sched = get_sync_scheduler()
        jobs = sched.scheduler.get_jobs() if sched.started else []
        return {
            "started": sched.started,
            "jobs_count": len(jobs),
            "jobs": [
                {"task_id": j.args[0] if j.args else j.id, "next_run": j.next_run_time.isoformat() if j.next_run_time else None}
                for j in jobs
            ],
        }
    except Exception as e:
        return {
            "started": False,
            "jobs_count": 0,
            "jobs": [],
            "error": str(e),
        }


@router.get("/sources/{conn_id}/tables")
def list_source_tables(conn_id: str, db: Session = Depends(get_db)):
    import json
    from app.services import encryption_service
    from app.services.connection.registry import get_connector

    conn = db.query(Connection).filter(Connection.id == conn_id).first()
    if not conn:
        raise HTTPException(404, "Connection not found")
    raw = (conn.config or {}).get("_encrypted", "")
    config = json.loads(encryption_service.decrypt(raw)) if raw else (conn.config or {})
    try:
        connector = get_connector(conn.kind, config)
        tables = connector.list_resources()
        sample: list = []
        columns: list = []
        if tables:
            try:
                sample = connector.pull_sample(tables[0], limit=5)
                columns = list(sample[0].keys()) if sample else []
            except Exception:
                sample = []
                columns = []
        return {"tables": tables, "sample": sample, "columns": columns}
    except Exception as e:
        raise HTTPException(400, f"获取表列表失败: {e}")


@router.get("/sources/{conn_id}/tables/{table}/sample")
def preview_source_table(conn_id: str, table: str, db: Session = Depends(get_db)):
    import json
    from app.services import encryption_service
    from app.services.connection.registry import get_connector

    conn = db.query(Connection).filter(Connection.id == conn_id).first()
    if not conn:
        raise HTTPException(404, "Connection not found")
    raw = (conn.config or {}).get("_encrypted", "")
    config = json.loads(encryption_service.decrypt(raw)) if raw else (conn.config or {})
    try:
        connector = get_connector(conn.kind, config)
        sample = connector.pull_sample(table, limit=20)
        columns = list(sample[0].keys()) if sample else []
        return {"table": table, "columns": columns, "sample": sample}
    except Exception as e:
        raise HTTPException(400, f"预览表失败: {e}")


# ========== CRUD ==========

@router.post("", status_code=201)
def create_task(body: SyncTaskCreate, db: Session = Depends(get_db)):
    _reject_retired_write()
    _validate_task(db, body, is_create=True)
    task = DataSyncTask(
        id=str(uuid.uuid4()),
        name=body.name,
        description=body.description or "",
        connection_id=body.connection_id,
        source_table=body.source_table or "",
        source_query=body.source_query or "",
        sync_mode=body.sync_mode,
        primary_key=body.primary_key or "",
        watermark_column=body.watermark_column or "",
        is_deleted_column=body.is_deleted_column or "",
        schedule_type=body.schedule_type,
        cron_expression=body.cron_expression or "",
        interval_seconds=body.interval_seconds or 0,
        enabled=body.enabled,
        trigger_pipeline_id=body.trigger_pipeline_id or None,
        status=SyncStatus.IDLE.value,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    _refresh_scheduler(task.id)
    return task.to_dict()


@router.get("")
def list_tasks(
    connection_id: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    enabled: Optional[bool] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(DataSyncTask)
    if connection_id:
        q = q.filter(DataSyncTask.connection_id == connection_id)
    if status:
        q = q.filter(DataSyncTask.status == status)
    if search:
        q = q.filter(DataSyncTask.name.ilike(f"%{search}%"))
    if enabled is not None:
        q = q.filter(DataSyncTask.enabled.is_(enabled))
    total = q.count()
    tasks = q.order_by(DataSyncTask.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    conn_map = {c.id: c.name for c in db.query(Connection).all()}
    pipe_map = {p.id: p.name for p in db.query(Pipeline).all()}
    items = []
    for t in tasks:
        d = t.to_dict()
        d["connection_name"] = conn_map.get(t.connection_id, "")
        d["trigger_pipeline_name"] = pipe_map.get(t.trigger_pipeline_id or "", "")
        items.append(d)
    return {"total": total, "items": items, "page": page, "page_size": page_size}


@router.get("/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(DataSyncTask).filter(DataSyncTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "SyncTask not found")
    d = task.to_dict()
    conn = db.query(Connection).filter(Connection.id == task.connection_id).first()
    d["connection_name"] = conn.name if conn else ""
    if task.trigger_pipeline_id:
        pipe = db.query(Pipeline).filter(Pipeline.id == task.trigger_pipeline_id).first()
        d["trigger_pipeline_name"] = pipe.name if pipe else ""
    return d


@router.put("/{task_id}")
def update_task(task_id: str, body: SyncTaskUpdate, db: Session = Depends(get_db)):
    _reject_retired_write()
    task = db.query(DataSyncTask).filter(DataSyncTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "SyncTask not found")
    _validate_task(db, body, is_create=False)
    for field, val in body.model_dump(exclude_unset=True).items():
        setattr(task, field, val)
    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    _refresh_scheduler(task.id)
    return task.to_dict()


@router.delete("/{task_id}")
def delete_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(DataSyncTask).filter(DataSyncTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "SyncTask not found")
    db.delete(task)
    db.commit()
    _refresh_scheduler(task_id)
    return {"status": "ok"}


@router.post("/{task_id}/toggle")
def toggle_task(task_id: str, enabled: bool, db: Session = Depends(get_db)):
    if enabled:
        _reject_retired_write()
    task = db.query(DataSyncTask).filter(DataSyncTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "SyncTask not found")
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
    _reject_retired_write()
    task = db.query(DataSyncTask).filter(DataSyncTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "SyncTask not found")
    if task.status == SyncStatus.RUNNING.value:
        raise HTTPException(409, "任务正在执行中，请稍后再试")
    from app.services.v2.sync_engine import execute_sync_task

    if sync:
        result = execute_sync_task(task_id, trigger_type=TriggerType.MANUAL.value)
        return result
    background.add_task(execute_sync_task, task_id, TriggerType.MANUAL.value)
    return {"status": "triggered", "task_id": task_id}


@router.get("/{task_id}/histories")
def list_histories(
    task_id: str,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    task = db.query(DataSyncTask).filter(DataSyncTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "SyncTask not found")
    total = db.query(DataSyncHistory).filter(DataSyncHistory.task_id == task_id).count()
    histories = (
        db.query(DataSyncHistory)
        .filter(DataSyncHistory.task_id == task_id)
        .order_by(DataSyncHistory.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"total": total, "items": [h.to_dict() for h in histories], "page": page, "page_size": page_size}
