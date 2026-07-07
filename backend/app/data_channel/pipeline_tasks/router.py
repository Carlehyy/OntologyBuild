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


def _live_next_run_map(task_ids: list[str]) -> dict[str, str]:
    """优先取调度器里 Job 的真实 next_run_time（最准，反映 misfire/coalesce 等）。"""
    out: dict[str, str] = {}
    try:
        from app.data_channel.sync_tasks.scheduler import get_sync_scheduler
        sched = get_sync_scheduler()
        if not sched.started:
            return out
        for tid in task_ids:
            job = sched.scheduler.get_job(f"pipe_task:{tid}")
            if job and job.next_run_time:
                out[tid] = job.next_run_time.isoformat()
    except Exception:
        pass
    return out


def _computed_next_run(task) -> str | None:
    """调度器未起（如预览环境）时按配置推算下一次执行时间。"""
    if not task.enabled:
        return None
    try:
        import pytz
        from datetime import timedelta
        tz = pytz.timezone("Asia/Shanghai")
        now = datetime.now(tz)
        if task.schedule_type == "CRON" and (task.cron_expression or "").strip():
            parts = task.cron_expression.strip().split()
            if len(parts) != 5:
                return None
            from apscheduler.triggers.cron import CronTrigger
            trig = CronTrigger(minute=parts[0], hour=parts[1], day=parts[2],
                               month=parts[3], day_of_week=parts[4], timezone=tz)
            nxt = trig.get_next_fire_time(None, now)
            return nxt.isoformat() if nxt else None
        if task.schedule_type == "INTERVAL" and (task.interval_seconds or 0) > 0:
            base = task.last_run_at or datetime.utcnow()
            if base.tzinfo is None:
                base = pytz.utc.localize(base)
            return (base + timedelta(seconds=task.interval_seconds)).astimezone(tz).isoformat()
    except Exception:
        return None
    return None


def _with_pipeline_info(db: Session, tasks: list[PipelineTask]) -> list[dict]:
    pipe_map = {p.id: p for p in db.query(Pipeline).all()}
    live = _live_next_run_map([t.id for t in tasks])
    items = []
    for t in tasks:
        d = t.to_dict()
        pipe = pipe_map.get(t.pipeline_id)
        d["pipeline_name"] = pipe.name if pipe else "(已删除)"
        d["pipeline_status"] = (pipe.status or "draft") if pipe else "deleted"
        d["pipeline_version"] = (pipe.version or 1) if pipe else None
        d["next_run_at"] = live.get(t.id) or _computed_next_run(t)
        items.append(d)
    return items


# ========== 固定路径（必须放在 /{task_id} 之前） ==========

def _curated_columns(db: Session, ds, schema: dict) -> list[dict]:
    """成品数据集的列清单（供主键多选/预览表头）。

    优先读闸门维护的 schema_json.columns_typed → columns；都没有再回退到实读预览。
    """
    typed = schema.get("columns_typed")
    if isinstance(typed, list) and typed:
        return [{"name": c.get("name"), "type": c.get("type") or "string"}
                for c in typed if isinstance(c, dict) and c.get("name")]
    cols = schema.get("columns")
    if isinstance(cols, list) and cols:
        return [{"name": c, "type": "string"} for c in cols if c]
    # 回退：实读最新版本前若干行推断列（老数据集没有 schema_json 时）
    from app.services.v2.dataset_service import DatasetService
    rows = DatasetService(db).preview(ds.id, None, limit=20)
    names: list[str] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k in r.keys():
            if k != "content" and k not in seen:
                seen.add(k)
                names.append(str(k))
    return [{"name": n, "type": "string"} for n in names]


@router.get("/selectable-pipelines")
def selectable_pipelines(db: Session = Depends(get_db)):
    """新建任务「选择流水线」阶段的候选：仅已发布 **且已执行产出过数据** 的流水线。

    每条流水线附带其成品数据集（id/名称/行数/已声明主键/列清单），
    让前端一次拿齐：可预览的数据、可勾选的主键范围、湖中已锁定的主键契约。
    """
    from app.models.v2.dataset import Dataset, DatasetVersion

    pipes = db.query(Pipeline).filter(Pipeline.status == "published").all()
    # 一次取全部成品数据集的最新版本，避免逐个查询
    items: list[dict] = []
    for p in pipes:
        curated_ids = [cid for cid in (p.target_curated_ids or []) if cid]
        if not curated_ids:
            continue
        curated: list[dict] = []
        total_rows = 0
        for cid in curated_ids:
            ds = db.query(Dataset).filter(Dataset.id == cid).first()
            if not ds:
                continue
            latest = db.query(DatasetVersion).filter(
                DatasetVersion.dataset_id == cid
            ).order_by(DatasetVersion.version_no.desc()).first()
            has_data = bool(latest and (latest.storage_uri or (latest.rowcount or 0) > 0))
            if not has_data:
                continue
            schema = dict(ds.schema_json or {})
            rowcount = (latest.rowcount or 0)
            curated.append({
                "id": ds.id,
                "name": ds.name,
                "rowcount": rowcount,
                "version_no": latest.version_no,
                "primary_key": schema.get("primary_key") or "",
                "columns": _curated_columns(db, ds, schema),
            })
            total_rows += rowcount
        # 「执行并产出过数据」：至少一个成品数据集有数据
        if not curated:
            continue
        items.append({
            "id": p.id,
            "name": p.name,
            "version": p.version,
            "domain": p.domain,
            "status": p.status,
            "total_rows": total_rows,
            "curated_datasets": curated,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        })
    items.sort(key=lambda x: x["updated_at"] or "", reverse=True)
    return {"items": items, "total": len(items)}


@router.get("/stats")
def stats_overview(db: Session = Depends(get_db)):
    total = db.query(PipelineTask).count()
    running = db.query(PipelineTask).filter(PipelineTask.status == "running").count()
    enabled = db.query(PipelineTask).filter(PipelineTask.enabled.is_(True)).count()
    failed = db.query(PipelineTask).filter(PipelineTask.status == "failed").count()

    # 执行次数/异常数：以任务触发的 PipelineRun 为口径（今日 + 累计）
    task_runs = db.query(PipelineRun).filter(PipelineRun.task_id.isnot(None))
    today_start = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    today_runs = task_runs.filter(PipelineRun.created_at >= today_start).count()
    today_errors = task_runs.filter(
        PipelineRun.created_at >= today_start, PipelineRun.status == "failed").count()
    total_runs = task_runs.count()
    total_errors = task_runs.filter(PipelineRun.status == "failed").count()
    return {
        "total": total,
        "running": running,
        "enabled": enabled,
        "failed": failed,
        "today_runs": today_runs,
        "today_errors": today_errors,
        "total_runs": total_runs,
        "total_errors": total_errors,
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
            # 审计速览：资产湖影响计数 + 执行时配置快照（明细走 /audit）
            "lake_impact": stats.get("lake_impact"),
            "config_snapshot": stats.get("config_snapshot"),
            "error_message": r.error_log or "",
        })
    return {"total": total, "items": items, "page": page, "page_size": page_size}


@router.get("/{task_id}/runs/{run_id}/audit")
def run_audit(task_id: str, run_id: str, db: Session = Depends(get_db)):
    """单次执行的完整审计明细：配置快照、流水线输出样本、资产湖行级影响。

    支撑「逐个追溯审计」：调用哪条流水线、输出是什么、当时配置是什么、
    最终对资产湖新增/更新/删除了哪些行。
    """
    run = db.query(PipelineRun).filter(
        PipelineRun.id == run_id, PipelineRun.task_id == task_id).first()
    if not run:
        raise HTTPException(404, "执行记录不存在")
    stats = run.stats or {}
    pipe = db.query(Pipeline).filter(Pipeline.id == run.pipeline_id).first()

    from app.models.v2.dataset import Dataset
    outputs = []
    for o in (stats.get("meta", {}) or {}).get("outputs", []) or []:
        cid = o.get("curated_dataset_id")
        name = o.get("curated_dataset_name")
        if not name and cid:
            ds = db.query(Dataset).filter(Dataset.id == cid).first()
            name = ds.name if ds else None
        outputs.append({
            "curated_dataset_id": cid,
            "curated_dataset_name": name,
            "version_no": o.get("version_no"),
            "dataset_version_id": o.get("dataset_version_id"),
            "table_name": o.get("table_name"),
            "rows_out": o.get("rows_out"),
            "lake_rows": o.get("lake_rows"),
            "primary_key": o.get("primary_key"),
            "output_columns": o.get("output_columns") or [],
            "output_sample": o.get("output_sample") or [],
            "lake_impact": o.get("lake_impact"),
            "skipped": o.get("skipped"),
            "gate_warnings": o.get("gate_warnings"),
        })

    return {
        "id": run.id,
        "task_id": task_id,
        "status": run.status,
        "trigger_type": stats.get("trigger_type", "manual"),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "rows_in": stats.get("rows_in", 0),
        "rows_out": stats.get("rows_out", 0),
        "lake_rows": stats.get("lake_rows"),
        "write_mode": stats.get("write_mode"),
        "lake_impact": stats.get("lake_impact"),
        "config_snapshot": stats.get("config_snapshot"),
        "pipeline": {
            "id": run.pipeline_id,
            "name": pipe.name if pipe else "(已删除)",
            "version": (pipe.version if pipe else None),
            "status": (pipe.status if pipe else "deleted"),
            "domain": (pipe.domain if pipe else None),
        },
        "outputs": outputs,
        "error_message": run.error_log or "",
    }
