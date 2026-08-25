"""助手评估 API — 仅 admin（路由挂载时统一施加 admin_guard）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.assistant_evaluation import service
from app.assistant_evaluation.adapters import get_adapters
from app.assistant_evaluation.dimensions import BASE_DIMENSION_KEYS, DIMENSIONS
from app.assistant_evaluation.engine import openjudge_available
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
    created_at: str | None
    finished_at: str | None
    duration_ms: int | None


class TaskDetailOut(TaskOut):
    items: list[TaskItemOut]


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
        created_at=_iso(task.created_at),
        finished_at=_iso(task.finished_at),
        duration_ms=task.duration_ms,
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
def create_task(payload: CreateTaskIn, db: Session = Depends(get_db)):
    try:
        task = service.create_task(
            db,
            assistant_key=payload.assistant_key,
            conversation_ids=payload.conversation_ids or None,
            sample_size=payload.sample_size,
            sample_days=payload.sample_days,
            dimension_keys=payload.dimension_keys or list(BASE_DIMENSION_KEYS),
            model_config_id=payload.model_config_id,
            created_by=None,
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
            created_at=_iso(item.created_at),
        )
        for item in service.task_items(db, task_id)
    ]
    return _ok(TaskDetailOut(**base))


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
