"""
本体智能体 API — /api/v2/formal/ontologies/{ontology_id}/agent/*

  GET    /agent/profile            边界配置（不存在则按安全默认创建）
  PUT    /agent/profile            更新边界（仅管理员）
  GET    /agent/capabilities       解析后的能力概览 + 技能卡预览（前端边界可视化）
  POST   /agent/chat               对话（默认 SSE 流式；stream=false 同步返回）
  GET    /agent/conversations      当前用户在该本体下的会话列表
  GET    /agent/conversations/{id} 会话详情（含完整轨迹，审计视图）
  DELETE /agent/conversations/{id}
  POST   /agent/execute-proposal   用户确认后真实执行提案（经动作引擎，HITL 闸门有效）
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin
from app.models.ontology import OntologyProject
from app.ontologies.agent_runtime import schemas as S
from app.ontologies.agent_runtime.boundary import ToolError, build_scope, get_or_create_profile
from app.ontologies.agent_runtime.models import AgentConversation, AgentMessage, AgentProfile
from app.ontologies.agent_runtime.orchestrator import run_agent_turn

router = APIRouter()
logger = logging.getLogger(__name__)

_PROFILE_FIELDS = ["enabled", "allowed_object_type_ids", "allowed_link_type_ids",
                   "allowed_action_ids", "allow_action_proposals",
                   "max_rows_per_query", "max_steps", "system_prompt_extra",
                   "default_model_id"]
_RESETTABLE = {"allowed_object_type_ids", "allowed_link_type_ids", "allowed_action_ids"}


def _require_ontology(db: Session, ontology_id: str) -> OntologyProject:
    p = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not p:
        raise HTTPException(404, "Ontology not found")
    return p


def _ok(data):
    return {"data": data}


def _profile_out(p: AgentProfile) -> dict:
    return S.AgentProfileOut.model_validate(p).model_dump(by_alias=True)


@router.get("/{ontology_id}/agent/profile")
def get_profile(ontology_id: str, db: Session = Depends(get_db),
                _=Depends(get_current_user)):
    _require_ontology(db, ontology_id)
    return _ok(_profile_out(get_or_create_profile(db, ontology_id)))


@router.put("/{ontology_id}/agent/profile")
def update_profile(ontology_id: str, body: S.AgentProfileUpdate,
                   db: Session = Depends(get_db), _=Depends(require_admin)):
    _require_ontology(db, ontology_id)
    profile = get_or_create_profile(db, ontology_id)
    data = body.model_dump(exclude_unset=True, exclude={"reset_to_all"})
    for field, value in data.items():
        if field in _PROFILE_FIELDS:
            setattr(profile, field, value)
    for field in body.reset_to_all:
        if field in _RESETTABLE:
            setattr(profile, field, None)
    db.commit()
    db.refresh(profile)
    return _ok(_profile_out(profile))


@router.get("/{ontology_id}/agent/capabilities")
def get_capabilities(ontology_id: str, db: Session = Depends(get_db),
                     _=Depends(get_current_user)):
    _require_ontology(db, ontology_id)
    try:
        _, _, scope = build_scope(db, ontology_id)
    except ToolError as e:
        raise HTTPException(404, str(e))
    return _ok({**scope.summary(), "skillCard": scope.skill_card()})


@router.post("/{ontology_id}/agent/chat")
def chat(ontology_id: str, body: S.ChatRequest,
         db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require_ontology(db, ontology_id)
    if not (body.message or "").strip():
        raise HTTPException(422, "message 不能为空")

    if not body.stream:
        events = list(run_agent_turn(db, ontology_id, current_user, body.message,
                                     conversation_id=body.conversation_id,
                                     model_id=body.model_id))
        answer = next((e for e in events if e["type"] == "answer"), None)
        error = next((e for e in events if e["type"] == "error"), None)
        meta = next((e for e in events if e["type"] == "meta"), {})
        steps = [e for e in events if e["type"] == "step"]
        return _ok({
            "conversationId": meta.get("conversationId"),
            "model": meta.get("model"),
            "steps": [{k: v for k, v in s.items() if k != "type"} for s in steps],
            "content": (answer or {}).get("content"),
            "citations": (answer or {}).get("citations") or [],
            "proposals": (answer or {}).get("proposals") or [],
            "usage": (answer or {}).get("usage"),
            "error": (error or {}).get("message"),
        })

    # SSE：流式生成器的生命周期长于请求依赖，自建 session 规避 yield 依赖
    # 在 StreamingResponse 下的提前关闭问题
    user = current_user

    def event_stream():
        from app.database import SessionLocal
        session = SessionLocal()
        try:
            for event in run_agent_turn(session, ontology_id, user, body.message,
                                        conversation_id=body.conversation_id,
                                        model_id=body.model_id):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        finally:
            session.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/{ontology_id}/agent/conversations")
def list_conversations(ontology_id: str, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    _require_ontology(db, ontology_id)
    rows = (db.query(AgentConversation)
            .filter(AgentConversation.ontology_id == ontology_id,
                    AgentConversation.user_id == getattr(current_user, "id", None))
            .order_by(AgentConversation.updated_at.desc())
            .limit(50).all())
    return _ok([S.ConversationOut.model_validate(c).model_dump(by_alias=True) for c in rows])


def _require_conversation(db: Session, ontology_id: str, conversation_id: str,
                          current_user) -> AgentConversation:
    conv = db.query(AgentConversation).filter(
        AgentConversation.id == conversation_id,
        AgentConversation.ontology_id == ontology_id).first()
    if not conv:
        raise HTTPException(404, "会话不存在")
    if conv.user_id and conv.user_id != getattr(current_user, "id", None) \
            and getattr(current_user, "role", "") != "admin":
        raise HTTPException(403, "无权访问他人会话")
    return conv


@router.get("/{ontology_id}/agent/conversations/{conversation_id}")
def get_conversation(ontology_id: str, conversation_id: str,
                     db: Session = Depends(get_db),
                     current_user=Depends(get_current_user)):
    conv = _require_conversation(db, ontology_id, conversation_id, current_user)
    messages = (db.query(AgentMessage)
                .filter(AgentMessage.conversation_id == conv.id)
                .order_by(AgentMessage.created_at.asc())
                .limit(200).all())
    return _ok({
        **S.ConversationOut.model_validate(conv).model_dump(by_alias=True),
        "messages": [S.MessageOut.model_validate(m).model_dump(by_alias=True) for m in messages],
    })


@router.delete("/{ontology_id}/agent/conversations/{conversation_id}", status_code=204)
def delete_conversation(ontology_id: str, conversation_id: str,
                        db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    conv = _require_conversation(db, ontology_id, conversation_id, current_user)
    db.query(AgentMessage).filter(AgentMessage.conversation_id == conv.id).delete()
    db.delete(conv)
    db.commit()


@router.post("/{ontology_id}/agent/execute-proposal")
def execute_proposal(ontology_id: str, body: S.ExecuteProposalRequest,
                     db: Session = Depends(get_db),
                     current_user=Depends(get_current_user)):
    """用户在界面确认提案后的真实执行。

    仍然只放行授权边界内的动作；执行走动作引擎全套治理（校验 / HITL 审批 /
    事实追加），actor 记为确认执行的用户 —— agent 只提案，人签字。
    """
    project = _require_ontology(db, ontology_id)
    if (project.status or "") != "published":
        raise HTTPException(409, "真实动作只能在已发布本体上执行；草稿中的提案仅可预演")
    try:
        _, profile, scope = build_scope(db, ontology_id)
        if not profile.enabled:
            raise ToolError("该本体的智能体已停用")
        action = scope.require_action(body.action_id)
    except ToolError as e:
        raise HTTPException(403, str(e))

    from app.ontologies.formal_modeling.schemas import RunActionRequest
    from app.services.formal.action_engine import execute_action
    log = execute_action(
        db, ontology_id,
        RunActionRequest(action_id=action.id, parameters=body.parameters,
                         target_instance_id=body.target_instance_id, dry_run=False),
        actor_id=getattr(current_user, "id", None),
    )
    return _ok(log)
