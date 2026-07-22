"""决策推演 REST API。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.model_configs.selector import llm_call_kwargs, select_llm_model_config
from app.ontologies.agent_runtime.boundary import ToolError, build_scope
from app.ontologies.agent_runtime.models import AgentConversation
from app.ontologies.decision_simulation import schemas as S
from app.ontologies.decision_simulation.models import DecisionSimulationRun
from app.ontologies.decision_simulation.service import execute

router = APIRouter()


def _owner_id(current_user) -> str:
    value = getattr(current_user, "id", None)
    if not value:
        raise HTTPException(401, "无法识别当前用户")
    return value


def _require_conversation(db: Session, ontology_id: str, conversation_id: str,
                          current_user) -> AgentConversation:
    row = db.query(AgentConversation).filter(
        AgentConversation.id == conversation_id,
        AgentConversation.ontology_id == ontology_id,
        AgentConversation.user_id == _owner_id(current_user),
    ).first()
    if not row:
        raise HTTPException(404, "会话不存在")
    return row


def _require_run(db: Session, ontology_id: str, run_id: str,
                 current_user) -> DecisionSimulationRun:
    row = db.query(DecisionSimulationRun).filter(
        DecisionSimulationRun.id == run_id,
        DecisionSimulationRun.ontology_id == ontology_id,
    ).first()
    if not row:
        raise HTTPException(404, "决策推演记录不存在")
    if row.created_by != _owner_id(current_user) \
            and getattr(current_user, "role", "") != "admin":
        raise HTTPException(403, "无权访问该决策推演记录")
    return row


def _summary(row: DecisionSimulationRun) -> S.DecisionSimulationSummaryOut:
    recommendation = row.recommendation or {}
    return S.DecisionSimulationSummaryOut(
        id=row.id,
        ontology_id=row.ontology_id,
        ontology_release_id=row.ontology_release_id,
        conversation_id=row.conversation_id,
        title=row.title,
        question=row.question,
        status=row.status,
        model_name=row.model_name,
        recommended_option=recommendation.get("recommendedOption"),
        robust_score=recommendation.get("robustScore"),
        perspective_count=len(row.perspectives or []),
        diagnostics=row.diagnostics or {},
        error_message=row.error_message,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _detail(row: DecisionSimulationRun) -> S.DecisionSimulationOut:
    return S.DecisionSimulationOut.model_validate(row, from_attributes=True)


@router.post("/{ontology_id}/agent/decision-simulations", status_code=201)
def create_decision_simulation(
    ontology_id: str,
    body: S.DecisionSimulationRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        _, profile, scope = build_scope(db, ontology_id, release_id=body.release_id)
    except ToolError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not profile.enabled:
        raise HTTPException(409, "该本体的智能体已停用")

    conversation_id = body.conversation_id
    if conversation_id:
        conversation = _require_conversation(
            db, ontology_id, conversation_id, current_user)
        if conversation.ontology_release_id != scope.release_id:
            raise HTTPException(409, "会话与本次推演的发布版本不一致")

    cfg = select_llm_model_config(
        db, model_id=body.model_id or profile.default_model_id)
    call_kwargs = llm_call_kwargs(cfg)
    if not call_kwargs:
        raise HTTPException(409, "尚未配置可用的对话模型")
    try:
        row = execute(
            db,
            scope,
            question=body.question,
            alternatives=body.alternatives,
            horizon=body.horizon,
            conversation_id=conversation_id,
            created_by=_owner_id(current_user),
            call_kwargs=call_kwargs,
            model_config_id=getattr(cfg, "id", None),
        )
    except ToolError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"data": _detail(row)}


@router.get("/{ontology_id}/agent/decision-simulations")
def list_decision_simulations(
    ontology_id: str,
    release_id: Optional[str] = Query(default=None),
    conversation_id: Optional[str] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(DecisionSimulationRun).filter(
        DecisionSimulationRun.ontology_id == ontology_id,
        DecisionSimulationRun.created_by == _owner_id(current_user),
    )
    if release_id:
        query = query.filter(DecisionSimulationRun.ontology_release_id == release_id)
    if conversation_id:
        query = query.filter(DecisionSimulationRun.conversation_id == conversation_id)
    rows = query.order_by(DecisionSimulationRun.started_at.desc()).limit(limit).all()
    return {"data": [_summary(row) for row in rows]}


@router.get("/{ontology_id}/agent/decision-simulations/{run_id}")
def get_decision_simulation(
    ontology_id: str,
    run_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return {"data": _detail(_require_run(db, ontology_id, run_id, current_user))}
