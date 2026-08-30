"""助手评估 API — 仅 admin（路由挂载时统一施加 admin_guard）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.assistant_evaluation import (
    benchmark_service,
    calibration_service,
    experiment_service,
    service,
    timeline,
)
from app.assistant_evaluation.adapters import get_adapters
from app.assistant_evaluation.dimensions import BASE_DIMENSION_KEYS, DIMENSIONS
from app.assistant_evaluation.engine import openjudge_available
from app.assistant_evaluation.models import AssistantEvalItem
from app.auth.models import User
from app.deps import get_db, require_admin

router = APIRouter(prefix="/assistant-evaluation", tags=["assistant-evaluation"],
                   dependencies=[Depends(require_admin)])


# ---------------------------------------------------------------- schemas


class ConversationRefOut(BaseModel):
    id: str
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    message_count: int = 0


class AssistantMetaOut(BaseModel):
    key: str
    label: str
    description: str
    conversation_count: int
    supported_dimension_keys: list[str]


class MetaOut(BaseModel):
    engine: str
    assistants: list[AssistantMetaOut]
    dimension_catalog: list[dict]
    base_dimension_keys: list[str]


class ConversationPageOut(BaseModel):
    total: int
    items: list[ConversationRefOut]


class CreateTaskIn(BaseModel):
    assistant_key: str
    conversation_ids: list[str] = Field(default_factory=list)
    sample_size: int = 10
    sample_days: int = 30
    dimension_keys: list[str] = Field(default_factory=list)
    model_config_id: str | None = None
    rubric_id: str | None = None


class RubricIn(BaseModel):
    name: str
    task_description: str
    sample_queries: list[str] = Field(default_factory=list)
    min_score: float = 0
    max_score: float = 5
    model_config_id: str | None = None


class RubricOut(BaseModel):
    id: str
    name: str
    task_description: str
    rubrics: str
    min_score: float
    max_score: float
    judge_model_name: str
    created_by: str | None = None
    created_at: str | None = None


class TrendPointOut(BaseModel):
    id: str
    title: str
    created_at: str | None = None
    overall: float | None = None
    dimensions: dict
    judge_model_name: str


class TaskItemReason(BaseModel):
    score: float | None = None
    reason: str = ""


class TaskItemOut(BaseModel):
    id: str
    conversation_id: str
    conversation_title: str
    overall_score: float | None
    scores: dict
    reasons: dict[str, TaskItemReason]
    flags: dict
    root_cause: str
    attribution: dict = Field(default_factory=dict)
    created_at: str | None = None


class TaskOut(BaseModel):
    id: str
    assistant_key: str
    assistant_label: str
    title: str
    status: str
    params: dict
    judge_model_name: str
    conversation_count: int
    completed_conversations: int
    summary: dict
    error: str | None
    created_by: str | None = None
    created_at: str | None
    finished_at: str | None
    duration_ms: int | None


class TaskDetailOut(TaskOut):
    items: list[TaskItemOut]


class TraceOut(BaseModel):
    conversation_id: str
    conversation_title: str
    query: str
    response: str
    openai_messages: list
    actions: list
    tool_error_count: int


class BenchmarkItemIn(BaseModel):
    conversation_id: str
    split: str | None = None      # train | heldout；缺省按稳定哈希切分
    origin: str = "manual"        # manual | badcase | task


class BenchmarkSetIn(BaseModel):
    assistant_key: str
    name: str
    description: str = ""
    # 本体助手必填：基准会话与沙箱回放所属本体
    ontology_id: str | None = None
    items: list[BenchmarkItemIn] = Field(default_factory=list)


class BenchmarkItemsAddIn(BaseModel):
    items: list[BenchmarkItemIn]


class BenchmarkFromTaskIn(BaseModel):
    task_id: str
    name: str | None = None
    include: str = "badcase"      # badcase | all
    description: str = ""


class BenchmarkItemOut(BaseModel):
    id: str
    conversation_id: str
    conversation_title: str
    split: str
    origin: str
    created_at: str | None = None


class BenchmarkSetOut(BaseModel):
    id: str
    assistant_key: str
    ontology_id: str | None = None
    name: str
    description: str
    source_task_id: str | None
    item_count: int
    train_count: int
    heldout_count: int
    created_at: str | None = None


class BenchmarkSetDetailOut(BenchmarkSetOut):
    items: list[BenchmarkItemOut]


class CalibrationIn(BaseModel):
    assistant_key: str
    conversation_ids: list[str] = Field(default_factory=list)
    benchmark_set_id: str | None = None
    repeats: int = 2
    dimension_keys: list[str] = Field(default_factory=list)
    model_config_id: str | None = None


class CalibrationOut(BaseModel):
    id: str
    assistant_key: str
    status: str
    params: dict
    judge_model_name: str
    result: dict
    error: str | None
    created_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None


class TimelineEventOut(BaseModel):
    id: str
    assistant_key: str | None
    event_type: str
    actor: str
    actor_user_id: str | None
    ref_type: str | None
    ref_id: str | None
    detail: dict
    created_at: str | None = None


class ProposalIn(BaseModel):
    ontology_id: str
    type: str                       # prompt_patch | model_swap
    title: str = ""
    rationale: str = ""
    payload: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)


class ProposalOut(BaseModel):
    id: str
    ontology_id: str
    assistant_key: str
    type: str
    title: str
    rationale: str
    payload: dict
    evidence: dict
    status: str
    created_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ExperimentIn(BaseModel):
    proposal_id: str
    benchmark_set_id: str
    dimension_keys: list[str] = Field(default_factory=list)
    threshold: float = 5.0          # 留出集增量的门禁阈值（实际下界为 max(threshold, 2×噪声地板))
    model_config_id: str | None = None


class ExperimentOut(BaseModel):
    id: str
    ontology_id: str
    proposal_id: str
    benchmark_set_id: str | None
    status: str
    params: dict
    judge_model_name: str
    result: dict
    error: str | None
    created_by: str | None = None
    created_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None


class ExperimentItemOut(BaseModel):
    id: str
    arm: str
    conversation_id: str
    conversation_title: str
    split: str
    overall_score: float | None
    scores: dict
    flags: dict
    transcript: dict = Field(default_factory=dict)
    created_at: str | None = None


class ExperimentDetailOut(ExperimentOut):
    items: list[ExperimentItemOut]


# ---------------------------------------------------------------- helpers


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _ok(data) -> dict:
    """平台统一响应信封（见 app/auth/router.py）。"""
    return {"data": data, "message": "ok"}


def _task_out(db: Session, task) -> TaskOut:
    adapter = get_adapters().get(task.assistant_key)
    return TaskOut(
        id=task.id,
        assistant_key=task.assistant_key,
        assistant_label=adapter.label if adapter else task.assistant_key,
        title=task.title,
        status=task.status,
        params=task.params or {},
        judge_model_name=task.judge_model_name,
        conversation_count=task.conversation_count,
        completed_conversations=task.completed_conversations,
        summary=task.summary or {},
        error=task.error,
        created_by=task.created_by,
        created_at=_iso(task.created_at),
        finished_at=_iso(task.finished_at),
        duration_ms=task.duration_ms,
    )


def _rubric_out(row) -> RubricOut:
    return RubricOut(
        id=row.id,
        name=row.name,
        task_description=row.task_description,
        rubrics=row.rubrics or "",
        min_score=row.min_score,
        max_score=row.max_score,
        judge_model_name=row.judge_model_name,
        created_by=row.created_by,
        created_at=_iso(row.created_at),
    )


# ---------------------------------------------------------------- endpoints


@router.get("/meta")
def get_meta(db: Session = Depends(get_db)):
    assistants = []
    for adapter in get_adapters().values():
        total, _refs = adapter.list_conversations(db, limit=1, offset=0)
        assistants.append(AssistantMetaOut(
            key=adapter.key, label=adapter.label, description=adapter.description,
            conversation_count=int(total or 0),
            supported_dimension_keys=list(DIMENSIONS.keys()),
        ))
    return _ok(MetaOut(
        engine="openjudge" if openjudge_available() else "builtin",
        assistants=assistants,
        dimension_catalog=[
            {"key": d.key, "label": d.label, "kind": d.kind, "description": d.description}
            for d in DIMENSIONS.values()
        ],
        base_dimension_keys=list(BASE_DIMENSION_KEYS),
    ))


@router.post("/rubrics")
def create_rubric(payload: RubricIn, db: Session = Depends(get_db),
                  admin: User = Depends(require_admin)):
    try:
        row = service.create_rubric(
            db,
            name=payload.name,
            task_description=payload.task_description,
            sample_queries=payload.sample_queries,
            min_score=payload.min_score,
            max_score=payload.max_score,
            model_config_id=payload.model_config_id,
            created_by=str(admin.id),
        )
    except (service.ServiceError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok(_rubric_out(row))


@router.get("/rubrics")
def list_rubrics(db: Session = Depends(get_db)):
    return _ok([_rubric_out(r) for r in service.list_rubrics(db)])


@router.delete("/rubrics/{rubric_id}")
def delete_rubric(rubric_id: str, db: Session = Depends(get_db)):
    if not service.delete_rubric(db, rubric_id):
        raise HTTPException(status_code=404, detail="评分标准不存在")
    return _ok({"deleted": True})


@router.get("/trend")
def get_trend(assistant_key: str, limit: int = 12, db: Session = Depends(get_db)):
    rows = service.trend(db, assistant_key, limit=limit)
    return _ok([TrendPointOut(
        id=t.id, title=t.title, created_at=_iso(t.created_at),
        overall=(t.summary or {}).get("overall"),
        dimensions=(t.summary or {}).get("dimensions") or {},
        judge_model_name=t.judge_model_name,
    ) for t in rows])


@router.get("/{assistant_key}/conversations")
def list_assistant_conversations(assistant_key: str, limit: int = 50, offset: int = 0,
                                 db: Session = Depends(get_db)):
    adapter = get_adapters().get(assistant_key)
    if not adapter:
        raise HTTPException(status_code=404, detail=f"未知的助手类型：{assistant_key}")
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    total, refs = adapter.list_conversations(db, limit=limit, offset=offset)
    return _ok(ConversationPageOut(
        total=int(total or 0),
        items=[ConversationRefOut(
            id=r.id, title=r.title,
            created_at=_iso(r.created_at), updated_at=_iso(r.updated_at),
            message_count=r.message_count,
        ) for r in refs],
    ))


@router.post("/tasks")
def create_task(payload: CreateTaskIn, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    try:
        task = service.create_task(
            db,
            assistant_key=payload.assistant_key,
            conversation_ids=payload.conversation_ids or None,
            sample_size=payload.sample_size,
            sample_days=payload.sample_days,
            dimension_keys=payload.dimension_keys or list(BASE_DIMENSION_KEYS),
            model_config_id=payload.model_config_id,
            rubric_id=payload.rubric_id,
            created_by=str(admin.id),
        )
    except service.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(task)  # 后台线程可能已推进状态（内联执行时）
    return _ok(_task_out(db, task))


@router.get("/tasks")
def list_tasks(assistant_key: str | None = None, limit: int = 20,
               db: Session = Depends(get_db)):
    tasks = service.list_tasks(db, assistant_key, limit=limit)
    return _ok([_task_out(db, t) for t in tasks])


@router.get("/tasks/{task_id}")
def get_task_detail(task_id: str, db: Session = Depends(get_db)):
    task = service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="评估任务不存在")
    base = _task_out(db, task).model_dump()
    base["items"] = [
        TaskItemOut(
            id=item.id,
            conversation_id=item.conversation_id,
            conversation_title=item.conversation_title,
            overall_score=item.overall_score,
            scores=item.scores or {},
            reasons={k: TaskItemReason(**v) for k, v in (item.reasons or {}).items()
                     if isinstance(v, dict)},
            flags=item.flags or {},
            root_cause=item.root_cause,
            attribution=item.attribution or {},
            created_at=_iso(item.created_at),
        )
        for item in service.task_items(db, task_id)
    ]
    return _ok(TaskDetailOut(**base))


@router.get("/tasks/{task_id}/items/{item_id}/trace")
def get_item_trace(task_id: str, item_id: str, db: Session = Depends(get_db)):
    task = service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="评估任务不存在")
    item = (
        db.query(AssistantEvalItem)
        .filter(AssistantEvalItem.id == item_id, AssistantEvalItem.task_id == task_id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="评估明细不存在")
    payload = service.load_item_trace(db, task, item)
    if payload is None:
        raise HTTPException(status_code=404, detail="会话轨迹不存在或内容为空")
    return _ok(TraceOut(**payload))


@router.get("/tasks/{task_id}/export")
def export_task_report(task_id: str, db: Session = Depends(get_db)):
    task = service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="评估任务不存在")
    markdown = service.export_markdown(task, service.task_items(db, task_id))
    filename = f"assistant-eval-{task.id[:8]}.md"
    return PlainTextResponse(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, db: Session = Depends(get_db)):
    try:
        deleted = service.delete_task(db, task_id)
    except service.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="评估任务不存在")
    return _ok({"deleted": True})


# ---------------------------------------------------------------- 基准集 / 噪声校准 / 时间线（飞轮 M1 地基）


def _benchmark_out(row, counts: dict | None = None) -> BenchmarkSetOut:
    slot = (counts or {}).get(row.id) or {}
    return BenchmarkSetOut(
        id=row.id,
        assistant_key=row.assistant_key,
        ontology_id=row.ontology_id,
        name=row.name,
        description=row.description,
        source_task_id=row.source_task_id,
        item_count=slot.get("total", 0),
        train_count=slot.get("train", 0),
        heldout_count=slot.get("heldout", 0),
        created_at=_iso(row.created_at),
    )


def _benchmark_item_out(item) -> BenchmarkItemOut:
    return BenchmarkItemOut(
        id=item.id,
        conversation_id=item.conversation_id,
        conversation_title=item.conversation_title,
        split=item.split,
        origin=item.origin,
        created_at=_iso(item.created_at),
    )


def _calibration_out(row) -> CalibrationOut:
    return CalibrationOut(
        id=row.id,
        assistant_key=row.assistant_key,
        status=row.status,
        params=row.params or {},
        judge_model_name=row.judge_model_name,
        result=row.result or {},
        error=row.error,
        created_at=_iso(row.created_at),
        finished_at=_iso(row.finished_at),
        duration_ms=row.duration_ms,
    )


def _get_benchmark_or_404(db: Session, set_id: str):
    try:
        return benchmark_service.get_set(db, set_id)
    except service.ServiceError as exc:
        raise HTTPException(status_code=404, detail="基准集不存在") from exc


@router.post("/benchmarks")
def create_benchmark_set(payload: BenchmarkSetIn, db: Session = Depends(get_db),
                         admin: User = Depends(require_admin)):
    try:
        row = benchmark_service.create_set(
            db,
            assistant_key=payload.assistant_key,
            name=payload.name,
            description=payload.description,
            ontology_id=payload.ontology_id,
            entries=[item.model_dump() for item in payload.items],
            created_by=str(admin.id),
        )
    except (service.ServiceError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok(_benchmark_out(row, benchmark_service.set_counts(db)))


@router.post("/benchmarks/from-task")
def create_benchmark_from_task(payload: BenchmarkFromTaskIn,
                               db: Session = Depends(get_db),
                               admin: User = Depends(require_admin)):
    try:
        row = benchmark_service.create_from_task(
            db,
            task_id=payload.task_id,
            name=payload.name,
            include=payload.include,
            description=payload.description,
            created_by=str(admin.id),
        )
    except (service.ServiceError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok(_benchmark_out(row, benchmark_service.set_counts(db)))


@router.get("/benchmarks")
def list_benchmark_sets(assistant_key: str | None = None,
                        db: Session = Depends(get_db)):
    rows = benchmark_service.list_sets(db, assistant_key)
    counts = benchmark_service.set_counts(db)
    return _ok([_benchmark_out(row, counts) for row in rows])


@router.get("/benchmarks/{set_id}")
def get_benchmark_set(set_id: str, db: Session = Depends(get_db)):
    row = _get_benchmark_or_404(db, set_id)
    counts = benchmark_service.set_counts(db)
    detail = _benchmark_out(row, counts).model_dump()
    detail["items"] = [
        _benchmark_item_out(item) for item in benchmark_service.items_of(db, set_id)
    ]
    return _ok(BenchmarkSetDetailOut(**detail))


@router.post("/benchmarks/{set_id}/items")
def add_benchmark_items(set_id: str, payload: BenchmarkItemsAddIn,
                        db: Session = Depends(get_db),
                        admin: User = Depends(require_admin)):
    _get_benchmark_or_404(db, set_id)
    try:
        row = benchmark_service.add_items(
            db, set_id,
            entries=[item.model_dump() for item in payload.items],
            actor_user_id=str(admin.id),
        )
    except (service.ServiceError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok(_benchmark_out(row, benchmark_service.set_counts(db)))


@router.delete("/benchmarks/{set_id}/items/{item_id}")
def remove_benchmark_item(set_id: str, item_id: str, db: Session = Depends(get_db),
                          admin: User = Depends(require_admin)):
    _get_benchmark_or_404(db, set_id)
    if not benchmark_service.remove_item(db, set_id, item_id, str(admin.id)):
        raise HTTPException(status_code=404, detail="基准集条目不存在")
    return _ok({"deleted": True})


@router.delete("/benchmarks/{set_id}")
def delete_benchmark_set(set_id: str, db: Session = Depends(get_db),
                         admin: User = Depends(require_admin)):
    if not benchmark_service.delete_set(db, set_id, str(admin.id)):
        raise HTTPException(status_code=404, detail="基准集不存在")
    return _ok({"deleted": True})


@router.post("/calibrations")
def create_calibration(payload: CalibrationIn, db: Session = Depends(get_db),
                       admin: User = Depends(require_admin)):
    try:
        row = calibration_service.create_calibration(
            db,
            assistant_key=payload.assistant_key,
            conversation_ids=payload.conversation_ids,
            benchmark_set_id=payload.benchmark_set_id,
            repeats=payload.repeats,
            dimension_keys=payload.dimension_keys,
            model_config_id=payload.model_config_id,
            created_by=str(admin.id),
        )
    except (service.ServiceError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(row)  # 内联 worker 可能已推进状态
    return _ok(_calibration_out(row))


@router.get("/calibrations")
def list_calibrations(assistant_key: str | None = None, limit: int = 20,
                      db: Session = Depends(get_db)):
    rows = calibration_service.list_calibrations(db, assistant_key, limit=limit)
    return _ok([_calibration_out(row) for row in rows])


@router.get("/calibrations/{calibration_id}")
def get_calibration_detail(calibration_id: str, db: Session = Depends(get_db)):
    row = calibration_service.get_calibration(db, calibration_id)
    if row is None:
        raise HTTPException(status_code=404, detail="校准任务不存在")
    return _ok(_calibration_out(row))


@router.delete("/calibrations/{calibration_id}")
def delete_calibration(calibration_id: str, db: Session = Depends(get_db)):
    try:
        deleted = calibration_service.delete_calibration(db, calibration_id)
    except service.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="校准任务不存在")
    return _ok({"deleted": True})


@router.get("/timeline")
def get_timeline(assistant_key: str | None = None, ref_type: str | None = None,
                 ref_id: str | None = None, limit: int = 50,
                 db: Session = Depends(get_db)):
    events = timeline.list_events(db, assistant_key=assistant_key, ref_type=ref_type,
                                  ref_id=ref_id, limit=limit)
    return _ok([TimelineEventOut(
        id=e.id,
        assistant_key=e.assistant_key,
        event_type=e.event_type,
        actor=e.actor,
        actor_user_id=e.actor_user_id,
        ref_type=e.ref_type,
        ref_id=e.ref_id,
        detail=e.detail or {},
        created_at=_iso(e.created_at),
    ) for e in events])


# ---------------------------------------------------------------- 优化提案 / 双臂实验（飞轮 M2 沙箱验证）


def _proposal_out(row) -> ProposalOut:
    return ProposalOut(
        id=row.id,
        ontology_id=row.ontology_id,
        assistant_key=row.assistant_key,
        type=row.type,
        title=row.title,
        rationale=row.rationale or "",
        payload=row.payload or {},
        evidence=row.evidence or {},
        status=row.status,
        created_by=row.created_by,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


def _experiment_out(row) -> ExperimentOut:
    return ExperimentOut(
        id=row.id,
        ontology_id=row.ontology_id,
        proposal_id=row.proposal_id,
        benchmark_set_id=row.benchmark_set_id,
        status=row.status,
        params=row.params or {},
        judge_model_name=row.judge_model_name,
        result=row.result or {},
        error=row.error,
        created_by=row.created_by,
        created_at=_iso(row.created_at),
        finished_at=_iso(row.finished_at),
        duration_ms=row.duration_ms,
    )


@router.post("/proposals")
def create_proposal(payload: ProposalIn, db: Session = Depends(get_db),
                    admin: User = Depends(require_admin)):
    try:
        row = experiment_service.create_proposal(
            db,
            ontology_id=payload.ontology_id,
            type=payload.type,
            title=payload.title,
            rationale=payload.rationale,
            payload=payload.payload,
            evidence=payload.evidence,
            created_by=str(admin.id),
        )
    except (service.ServiceError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok(_proposal_out(row))


@router.get("/proposals")
def list_proposals(ontology_id: str | None = None, limit: int = 20,
                   db: Session = Depends(get_db)):
    return _ok([_proposal_out(row) for row in
                experiment_service.list_proposals(db, ontology_id, limit=limit)])


@router.get("/proposals/{proposal_id}")
def get_proposal_detail(proposal_id: str, db: Session = Depends(get_db)):
    row = experiment_service.get_proposal(db, proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="提案不存在")
    return _ok(_proposal_out(row))


@router.post("/experiments")
def create_experiment(payload: ExperimentIn, db: Session = Depends(get_db),
                      admin: User = Depends(require_admin)):
    try:
        row = experiment_service.create_experiment(
            db,
            proposal_id=payload.proposal_id,
            benchmark_set_id=payload.benchmark_set_id,
            dimension_keys=payload.dimension_keys,
            threshold=payload.threshold,
            model_config_id=payload.model_config_id,
            created_by=str(admin.id),
        )
    except (service.ServiceError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(row)  # 内联 worker 可能已推进状态
    return _ok(_experiment_out(row))


@router.get("/experiments")
def list_experiments(ontology_id: str | None = None, limit: int = 20,
                     db: Session = Depends(get_db)):
    return _ok([_experiment_out(row) for row in
                experiment_service.list_experiments(db, ontology_id, limit=limit)])


@router.get("/experiments/{experiment_id}")
def get_experiment_detail(experiment_id: str, arm: str | None = None,
                          db: Session = Depends(get_db)):
    row = experiment_service.get_experiment(db, experiment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="实验不存在")
    detail = _experiment_out(row).model_dump()
    detail["items"] = [
        ExperimentItemOut(
            id=item.id,
            arm=item.arm,
            conversation_id=item.conversation_id,
            conversation_title=item.conversation_title,
            split=item.split,
            overall_score=item.overall_score,
            scores=item.scores or {},
            flags=item.flags or {},
            transcript=item.transcript or {},
            created_at=_iso(item.created_at),
        )
        for item in experiment_service.experiment_items(db, experiment_id, arm=arm)
    ]
    return _ok(ExperimentDetailOut(**detail))


@router.delete("/experiments/{experiment_id}")
def delete_experiment(experiment_id: str, db: Session = Depends(get_db)):
    try:
        deleted = experiment_service.delete_experiment(db, experiment_id)
    except service.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="实验不存在")
    return _ok({"deleted": True})
