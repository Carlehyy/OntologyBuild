"""审计时间线 — 飞轮全流程事件的统一留痕与查询。

record_event 只向调用方会话添加事件行、不提交：路由上下文随业务
提交一并落库，worker 线程随自身事务提交，保证事件与业务状态同事务
一致。M1 覆盖任务 / 基准集 / 校准三类事件，M2/M3 的提案、投产与
回退事件写入同一张表。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.assistant_evaluation.models import AssistantEvalTimelineEvent

# 事件类型（M1 词汇表；M2/M3 扩展 proposal_*/apply_*/rollback_*）
EVENT_TASK_CREATED = "task_created"
EVENT_TASK_SUCCEEDED = "task_succeeded"
EVENT_TASK_FAILED = "task_failed"
EVENT_BENCHMARK_CREATED = "benchmark_created"
EVENT_BENCHMARK_DELETED = "benchmark_deleted"
EVENT_BENCHMARK_ITEMS_ADDED = "benchmark_items_added"
EVENT_BENCHMARK_ITEM_REMOVED = "benchmark_item_removed"
EVENT_CALIBRATION_CREATED = "calibration_created"
EVENT_CALIBRATION_SUCCEEDED = "calibration_succeeded"
EVENT_CALIBRATION_FAILED = "calibration_failed"
EVENT_PROPOSAL_CREATED = "proposal_created"
EVENT_EXPERIMENT_CREATED = "experiment_created"
EVENT_EXPERIMENT_SUCCEEDED = "experiment_succeeded"
EVENT_EXPERIMENT_FAILED = "experiment_failed"
EVENT_PROPOSAL_APPLIED = "proposal_applied"
EVENT_VERSION_ROLLED_BACK = "version_rolled_back"
EVENT_CYCLE_STARTED = "cycle_started"
EVENT_CYCLE_SKIPPED = "cycle_skipped"
EVENT_CYCLE_SUCCEEDED = "cycle_succeeded"
EVENT_CYCLE_FAILED = "cycle_failed"

ACTOR_ADMIN = "admin"
ACTOR_SYSTEM = "system"
ACTOR_AUTOPILOT = "autopilot"


def record_event(db: Session, *, event_type: str, assistant_key: str | None = None,
                 actor: str = ACTOR_ADMIN, actor_user_id: str | None = None,
                 ref_type: str | None = None, ref_id: str | None = None,
                 detail: dict | None = None) -> AssistantEvalTimelineEvent:
    """追加一条时间线事件（不提交，由调用方事务统一落库）。"""
    event = AssistantEvalTimelineEvent(
        event_type=event_type,
        assistant_key=assistant_key,
        actor=actor,
        actor_user_id=actor_user_id,
        ref_type=ref_type,
        ref_id=ref_id,
        detail=detail or {},
    )
    db.add(event)
    return event


def list_events(db: Session, *, assistant_key: str | None = None,
                ref_type: str | None = None, ref_id: str | None = None,
                limit: int = 50) -> list[AssistantEvalTimelineEvent]:
    """按时间倒序查询时间线（可选按助手 / 引用对象过滤）。"""
    query = db.query(AssistantEvalTimelineEvent)
    if assistant_key:
        query = query.filter(AssistantEvalTimelineEvent.assistant_key == assistant_key)
    if ref_type:
        query = query.filter(AssistantEvalTimelineEvent.ref_type == ref_type)
    if ref_id:
        query = query.filter(AssistantEvalTimelineEvent.ref_id == ref_id)
    return (
        query.order_by(AssistantEvalTimelineEvent.created_at.desc(),
                       AssistantEvalTimelineEvent.id.desc())
        .limit(min(max(1, int(limit or 50)), 200))
        .all()
    )
