"""流水线调度任务路由：CRUD、手动触发、执行历史、统计"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import uuid

from app.deps import get_current_user, get_db
from app.data_channel.pipeline_tasks.models import PipelineTask
from app.models.v2.pipeline import Pipeline, PipelineRun

router = APIRouter(dependencies=[Depends(get_current_user)])

WRITE_MODES = ("overwrite", "append", "upsert", "append_dedup")
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
HistoryStatus = Literal["pending", "running", "success", "failed", "cancelled"]
HistoryTriggerType = Literal["manual", "scheduled"]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """数据库裸时间统一按 UTC 解释；带时区值统一转换为 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _shanghai_day_start_utc(local_day) -> datetime:
    """上海自然日零点转换为数据库使用的 UTC naive 边界。"""
    local_start = datetime.combine(local_day, datetime.min.time(), tzinfo=SHANGHAI_TZ)
    return local_start.astimezone(timezone.utc).replace(tzinfo=None)


def _shanghai_date(value: datetime):
    return _as_utc(value).astimezone(SHANGHAI_TZ).date()


class PipelineTaskCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=1000)
    pipeline_id: str
    write_mode: Literal["overwrite", "append", "upsert", "append_dedup"] = "overwrite"
    # deprecated：只为兼容旧客户端；服务端只接受与流水线发布契约一致的值，
    # 最终始终从流水线契约派生。
    primary_key: Optional[str] = ""
    soft_delete_column: Optional[str] = ""
    skip_empty: bool = True
    schedule_type: Literal["MANUAL", "CRON", "INTERVAL"] = "MANUAL"
    cron_expression: Optional[str] = ""
    interval_seconds: Optional[int] = 0
    enabled: bool = True

    @field_validator("name", "description")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


class PipelineTaskUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, min_length=1, max_length=1000)
    pipeline_id: Optional[str] = None
    write_mode: Optional[Literal["overwrite", "append", "upsert", "append_dedup"]] = None
    primary_key: Optional[str] = None
    soft_delete_column: Optional[str] = None
    skip_empty: Optional[bool] = None
    schedule_type: Optional[Literal["MANUAL", "CRON", "INTERVAL"]] = None
    cron_expression: Optional[str] = None
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None

    @field_validator("name", "description")
    @classmethod
    def required_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


def _validate(db: Session, body, existing: PipelineTask | None = None) -> tuple[Pipeline, str]:
    from app.data_channel.datasets.lake_gate import contract_pk, normalize_definitions, split_pk

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
        raise HTTPException(400, f"流水线「{pipe.name}」尚未发布，任务只能调度已发布的流水线。请先在流水线编辑向导中完成发布。")
    # 启用校验只在新建/换绑流水线时做：已有任务的流水线被临时停用时，
    # 改名等编辑不应被拦（执行时引擎自会跳过停用的流水线）
    picking_pipeline = existing is None or pipeline_id != existing.pipeline_id
    if picking_pipeline and pipe.enabled is False:
        raise HTTPException(400, f"流水线「{pipe.name}」未启用，任务只能挂接已启用的流水线。请先在流水线列表打开启用开关。")

    # 主键只有一个权威源：流水线发布契约。任务请求里的 primary_key 仅用于
    # 兼容旧客户端，允许留空或传入同值，绝不允许自行补充/改写。
    pipe_pk = contract_pk(pipe.column_definitions)
    requested_pk = (getattr(body, "primary_key", None) or "").strip()
    if requested_pk and split_pk(requested_pk) != split_pk(pipe_pk):
        raise HTTPException(
            400,
            "数据任务不再定义主键；主键必须来自已发布流水线的数据契约"
            + (f"（当前契约：{pipe_pk}）" if pipe_pk else "（当前流水线未声明主键）"),
        )
    if g("write_mode") == "upsert" and not pipe_pk:
        raise HTTPException(400, "「主键合并」要求流水线在发布契约中声明主键；请返回流水线补齐契约后重新发布")

    soft_delete = (g("soft_delete_column") or "").strip()
    contract_columns = {d["field_key"] for d in normalize_definitions(pipe.column_definitions)}
    if soft_delete and soft_delete not in contract_columns:
        raise HTTPException(400, f"软删除列「{soft_delete}」不在流水线发布契约中")

    schedule_type = g("schedule_type")
    if schedule_type == "CRON":
        expr = (g("cron_expression") or "").strip()
        if not expr:
            raise HTTPException(400, "CRON 调度必须填写 cron 表达式")
        if len(expr.split()) != 5:
            raise HTTPException(400, "cron 表达式须为 5 段格式：分 时 日 月 周")
        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(expr)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"cron 表达式无效：{exc}") from exc
    elif schedule_type == "INTERVAL":
        iv = g("interval_seconds")
        if not iv or iv < 10:
            raise HTTPException(400, "固定间隔调度的间隔必须 ≥ 10 秒")
    return pipe, pipe_pk


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
    """调度器未起（或未注册 Job）时按配置推算下一次执行时间，作为兜底。

    INTERVAL 走纯标准库最稳；CRON 用 APScheduler 触发器（pytz→zoneinfo 兜底）。
    MANUAL 或未启用返回 None（前端相应显示「手动触发 / 已停用」）。
    """
    if not task.enabled:
        return None
    from datetime import timedelta, timezone as _tz

    # INTERVAL：last_run + 间隔；从未运行则以当前时间起算
    if task.schedule_type == "INTERVAL" and (task.interval_seconds or 0) > 0:
        base = task.last_run_at or datetime.utcnow()
        if base.tzinfo is None:
            base = base.replace(tzinfo=_tz.utc)
        return (base + timedelta(seconds=task.interval_seconds)).isoformat()

    # CRON：标准 5 段表达式，用触发器推算下次触发点
    if task.schedule_type == "CRON" and (task.cron_expression or "").strip():
        parts = task.cron_expression.strip().split()
        if len(parts) != 5:
            return None
        try:
            try:
                import pytz
                tz = pytz.timezone("Asia/Shanghai")
            except Exception:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo("Asia/Shanghai")
            from apscheduler.triggers.cron import CronTrigger
            trig = CronTrigger(minute=parts[0], hour=parts[1], day=parts[2],
                               month=parts[3], day_of_week=parts[4], timezone=tz)
            nxt = trig.get_next_fire_time(None, datetime.now(tz))
            return nxt.isoformat() if nxt else None
        except Exception:
            return None
    return None


def _last_impact_map(db: Session, task_ids: list[str]) -> dict[str, dict]:
    """每个任务最近一次执行对资产湖的影响（新增/更新/删除）— 列表页「执行结果」列。

    仅取每个任务最新的一条 run（按 max(created_at)），避免加载全部历史 stats。
    """
    if not task_ids:
        return {}
    from sqlalchemy import func
    out: dict[str, dict] = {}
    try:
        sub = (db.query(PipelineRun.task_id, func.max(PipelineRun.created_at).label("mx"))
               .filter(PipelineRun.task_id.in_(task_ids))
               .group_by(PipelineRun.task_id).subquery())
        latest = (db.query(PipelineRun)
                  .join(sub, (PipelineRun.task_id == sub.c.task_id)
                        & (PipelineRun.created_at == sub.c.mx)).all())
        for r in latest:
            imp = (r.stats or {}).get("lake_impact")
            if r.task_id not in out and imp:
                out[r.task_id] = imp
    except Exception:
        pass
    return out


def _with_pipeline_info(db: Session, tasks: list[PipelineTask]) -> list[dict]:
    pipe_map = {p.id: p for p in db.query(Pipeline).all()}
    task_ids = [t.id for t in tasks]
    live = _live_next_run_map(task_ids)
    impacts = _last_impact_map(db, task_ids)
    items = []
    for t in tasks:
        d = t.to_dict()
        pipe = pipe_map.get(t.pipeline_id)
        d["pipeline_name"] = pipe.name if pipe else "(已删除)"
        d["pipeline_status"] = (pipe.status or "draft") if pipe else "deleted"
        d["pipeline_enabled"] = bool(pipe.enabled) if pipe else False
        d["pipeline_version"] = (pipe.version or 1) if pipe else None
        d["next_run_at"] = live.get(t.id) or _computed_next_run(t)
        d["last_impact"] = impacts.get(t.id)
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
    """新建任务「选择流水线」阶段的候选：**已发布且已启用** 的流水线。

    每条流水线附带两份信息，前端一次拿齐：
      - contract：流水线字段契约（发布封版）——列清单与主键，任务侧只读消费
      - curated_datasets：已产出的成品数据集（id/名称/行数/湖中主键/列清单）
    有契约的流水线即使还没产出过数据也可选（首次入湖正是任务的职责）；
    没有契约的旧流水线仍要求已产出数据，否则无从得知列与主键范围。
    """
    from app.models.v2.dataset import Dataset, DatasetVersion
    from app.data_channel.datasets.lake_gate import contract_pk, normalize_definitions

    pipes = db.query(Pipeline).filter(
        Pipeline.status == "published",
        Pipeline.enabled.isnot(False),  # NULL（老数据）视为启用
    ).all()
    items: list[dict] = []
    for p in pipes:
        curated: list[dict] = []
        total_rows = 0
        for cid in [c for c in (p.target_curated_ids or []) if c]:
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

        defs = normalize_definitions(p.column_definitions)
        contract = {
            "primary_key": contract_pk(p.column_definitions),
            "columns": [{"name": d["field_key"], "type": d["field_type"],
                         "field_name": d["field_name"],
                         "is_primary_key": d["is_primary_key"],
                         "nullable": d["nullable"]} for d in defs],
        } if defs else None

        # 既无契约也无已产出数据：无从配置入库方式，不进候选
        if not curated and not contract:
            continue
        items.append({
            "id": p.id,
            "name": p.name,
            "version": p.version,
            "domain": p.domain,
            "status": p.status,
            "total_rows": total_rows,
            "contract": contract,
            "curated_datasets": curated,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        })
    items.sort(key=lambda x: x["updated_at"] or "", reverse=True)
    return {"items": items, "total": len(items)}


@router.get("/stats")
def stats_overview(db: Session = Depends(get_db)):
    total = db.query(PipelineTask).count()
    running = db.query(PipelineTask).filter(PipelineTask.status == "running").count()
    success = db.query(PipelineTask).filter(PipelineTask.status == "success").count()
    idle = db.query(PipelineTask).filter(PipelineTask.status == "idle").count()
    enabled = db.query(PipelineTask).filter(PipelineTask.enabled.is_(True)).count()
    failed = db.query(PipelineTask).filter(PipelineTask.status == "failed").count()

    # 执行次数/异常数：以任务触发的 PipelineRun 为口径（今日 + 累计）
    task_runs = db.query(PipelineRun).filter(PipelineRun.task_id.isnot(None))
    local_today = _now_utc().astimezone(SHANGHAI_TZ).date()
    today_start = _shanghai_day_start_utc(local_today)
    today_runs = task_runs.filter(PipelineRun.created_at >= today_start).count()
    today_errors = task_runs.filter(
        PipelineRun.created_at >= today_start, PipelineRun.status == "failed").count()
    total_runs = task_runs.count()
    total_errors = task_runs.filter(PipelineRun.status == "failed").count()
    # 最近 7 个自然日的真实运行次数。前端不得用随机/合成曲线冒充观测数据。
    first_day = local_today - timedelta(days=6)
    trend = {
        (first_day + timedelta(days=i)).isoformat(): {"runs": 0, "errors": 0}
        for i in range(7)
    }
    recent = task_runs.filter(
        PipelineRun.created_at >= _shanghai_day_start_utc(first_day)
    ).with_entities(PipelineRun.created_at, PipelineRun.status).all()
    for created_at, run_status in recent:
        if not created_at:
            continue
        key = _shanghai_date(created_at).isoformat()
        if key not in trend:
            continue
        trend[key]["runs"] += 1
        if run_status == "failed":
            trend[key]["errors"] += 1

    # 右侧执行动态：按执行创建时间倒序展示最新 30 条真实记录，口径与统计一致。
    recent_runs = (
        db.query(PipelineRun, PipelineTask, Pipeline)
        .join(PipelineTask, PipelineTask.id == PipelineRun.task_id)
        .join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
        .order_by(PipelineRun.created_at.desc())
        .limit(30)
        .all()
    )

    return {
        "total": total,
        "running": running,
        "success": success,
        "idle": idle,
        "enabled": enabled,
        "failed": failed,
        "today_runs": today_runs,
        "today_errors": today_errors,
        "total_runs": total_runs,
        "total_errors": total_errors,
        "trend_7d": [
            {"date": day, "runs": counts["runs"], "errors": counts["errors"]}
            for day, counts in trend.items()
        ],
        "recent_runs": [
            {
                "id": run.id,
                "task_id": task.id,
                "task_name": task.name,
                "pipeline_name": pipeline.name,
                "status": run.status,
                "trigger_type": (run.stats or {}).get("trigger_type", "manual"),
                "started_at": _utc_iso(run.started_at or run.created_at),
                "finished_at": _utc_iso(run.finished_at),
                "rows_out": (run.stats or {}).get("rows_out", 0),
                "lake_impact": (run.stats or {}).get("lake_impact"),
                "error_message": run.error_log or "",
            }
            for run, task, pipeline in recent_runs
        ],
    }


# ========== CRUD ==========

@router.post("", status_code=201)
def create_task(
    body: PipelineTaskCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _, pipe_pk = _validate(db, body)
    task = PipelineTask(
        id=str(uuid.uuid4()),
        name=body.name,
        description=body.description,
        pipeline_id=body.pipeline_id,
        write_mode=body.write_mode,
        primary_key=pipe_pk,
        soft_delete_column=(body.soft_delete_column or "").strip(),
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
        q = q.join(Pipeline, Pipeline.id == PipelineTask.pipeline_id).filter(or_(
            PipelineTask.name.ilike(f"%{search}%"),
            Pipeline.name.ilike(f"%{search}%"),
        ))
    if status:
        q = q.filter(PipelineTask.status == status)
    if enabled is not None:
        q = q.filter(PipelineTask.enabled.is_(enabled))
    if pipeline_id:
        q = q.filter(PipelineTask.pipeline_id == pipeline_id)
    total = q.count()
    tasks = q.order_by(PipelineTask.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "items": _with_pipeline_info(db, tasks), "page": page, "page_size": page_size}


@router.get("/pipeline-options")
def pipeline_filter_options(db: Session = Depends(get_db)):
    """任务池筛选候选：仅返回实际有关联任务的流水线及其任务数。"""
    from sqlalchemy import func

    rows = (
        db.query(
            PipelineTask.pipeline_id,
            Pipeline.name,
            func.count(PipelineTask.id).label("task_count"),
        )
        .outerjoin(Pipeline, Pipeline.id == PipelineTask.pipeline_id)
        .group_by(PipelineTask.pipeline_id, Pipeline.name)
        .order_by(Pipeline.name.asc(), PipelineTask.pipeline_id.asc())
        .all()
    )
    return {
        "items": [
            {
                "id": pipeline_id,
                "name": pipeline_name or "(流水线已删除)",
                "task_count": int(task_count),
            }
            for pipeline_id, pipeline_name, task_count in rows
        ]
    }


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
    if created_from and created_to and _as_utc(created_from) > _as_utc(created_to):
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
        trigger_expr = PipelineRun.stats["trigger_type"].as_string()
        if trigger_type == "manual":
            query = query.filter(or_(
                PipelineRun.stats.is_(None),
                trigger_expr.is_(None),
                trigger_expr == "manual",
            ))
        else:
            query = query.filter(trigger_expr == trigger_type)
    if created_from:
        query = query.filter(PipelineRun.created_at >= _as_utc(created_from).replace(tzinfo=None))
    if created_to:
        query = query.filter(PipelineRun.created_at <= _as_utc(created_to).replace(tzinfo=None))
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
        "curated_dataset_ids": stats.get("curated_dataset_ids", []),
        "lake_impact": stats.get("lake_impact"),
        "config_snapshot": stats.get("config_snapshot"),
        "error_message": run.error_log or "",
    }


@router.get("/histories")
def list_all_histories(
    search: Optional[str] = None,
    pipeline_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    status: Optional[HistoryStatus] = None,
    trigger_type: Optional[HistoryTriggerType] = None,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    db: Session = Depends(get_db),
):
    """分页查询任务池的全部执行记录，供全局历史弹窗使用。"""
    _validate_history_query(page, page_size, created_from, created_to)
    query = (
        db.query(PipelineRun, PipelineTask, Pipeline)
        .join(PipelineTask, PipelineTask.id == PipelineRun.task_id)
        .outerjoin(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
    )
    keyword = (search or "").strip()
    if keyword:
        query = query.filter(or_(
            PipelineTask.name.ilike(f"%{keyword}%"),
            Pipeline.name.ilike(f"%{keyword}%"),
        ))
    if pipeline_id:
        query = query.filter(PipelineRun.pipeline_id == pipeline_id)
    query = _apply_history_filters(
        query, status, trigger_type, created_from, created_to,
    )
    total = query.count()
    rows = query.order_by(
        PipelineRun.created_at.desc(),
        PipelineRun.id.desc(),
    ).offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for run, task, pipeline in rows:
        item = _history_item(run)
        item.update({
            "task_id": task.id,
            "task_name": task.name,
            "pipeline_id": run.pipeline_id,
            "pipeline_name": pipeline.name if pipeline else "(流水线已删除)",
        })
        items.append(item)
    return {"total": total, "items": items, "page": page, "page_size": page_size}


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
    _, pipe_pk = _validate(db, body, existing=task)
    for field, val in body.model_dump(exclude_unset=True).items():
        if field == "primary_key":
            continue
        setattr(task, field, val)
    # 兼容字段只保留当前发布契约快照，修复历史任务可能存在的自定义值。
    task.primary_key = pipe_pk
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
    if enabled:
        pipe = db.query(Pipeline).filter(Pipeline.id == task.pipeline_id).first()
        if not pipe or (pipe.status or "draft") != "published" or pipe.enabled is False:
            raise HTTPException(409, "关联流水线未发布或已停用，不能启用该调度任务")
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
        # 这里只做快速反馈；真正的并发边界在执行引擎的数据库原子租约。
        # 过期租约允许引擎恢复，不能被页面层的陈旧状态永久拦住。
        if task.lease_expires_at is None or task.lease_expires_at > datetime.utcnow():
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
    page_size: int = 10,
    status: Optional[HistoryStatus] = None,
    trigger_type: Optional[HistoryTriggerType] = None,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    db: Session = Depends(get_db),
):
    _validate_history_query(page, page_size, created_from, created_to)
    task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "PipelineTask not found")
    q = _apply_history_filters(
        db.query(PipelineRun).filter(PipelineRun.task_id == task_id),
        status, trigger_type, created_from, created_to,
    )
    total = q.count()
    runs = q.order_by(
        PipelineRun.created_at.desc(),
        PipelineRun.id.desc(),
    ).offset((page - 1) * page_size).limit(page_size).all()
    items = [_history_item(run) for run in runs]
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
