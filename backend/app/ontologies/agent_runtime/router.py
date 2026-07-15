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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin
from app.models.ontology import OntologyProject
from app.ontologies.agent_runtime import schemas as S
from app.ontologies.agent_runtime.boundary import ToolError, build_scope, get_or_create_profile
from app.ontologies.agent_runtime.models import (
    AgentConversation, AgentMessage, AgentProfile,
    AnalysisReportRun, AnalysisReportTemplate,
)
from app.ontologies.agent_runtime.orchestrator import run_agent_turn
from app.ontologies.agent_runtime.graph_service import (
    analyze_change_impact,
    build_workspace_graph,
    find_paths,
    get_instance_detail,
)
from app.ontologies.agent_runtime import reporting

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


@router.get("/{ontology_id}/agent/graph")
def get_agent_graph(
    ontology_id: str,
    depth: int = Query(default=2, ge=1, le=3),
    query: str | None = Query(default=None, max_length=200),
    object_type: str | None = Query(default=None, max_length=200),
    focus_instance_id: str | None = Query(default=None, max_length=200),
    limit_per_type: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """授权范围内的渐进式数据图谱：L1 类型、L2 实例、L3 聚焦属性。"""
    _require_ontology(db, ontology_id)
    try:
        _, _, scope = build_scope(db, ontology_id)
        return _ok(build_workspace_graph(
            scope,
            depth=depth,
            query=query,
            object_type_ref=object_type,
            focus_instance_id=focus_instance_id,
            limit_per_type=limit_per_type,
        ))
    except ToolError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{ontology_id}/agent/graph/instances/{instance_id}")
def get_agent_graph_instance(
    ontology_id: str,
    instance_id: str,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    _require_ontology(db, ontology_id)
    try:
        _, _, scope = build_scope(db, ontology_id)
        return _ok(get_instance_detail(scope, instance_id))
    except ToolError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{ontology_id}/agent/graph/paths")
def query_agent_graph_paths(
    ontology_id: str,
    body: S.GraphPathRequest,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    _require_ontology(db, ontology_id)
    try:
        _, _, scope = build_scope(db, ontology_id)
        return _ok(find_paths(
            scope,
            body.source_instance_id,
            body.target_instance_id,
            direction=body.direction,
            max_depth=body.max_depth,
            max_paths=body.max_paths,
        ))
    except ToolError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{ontology_id}/agent/graph/impact")
def query_agent_graph_impact(
    ontology_id: str,
    body: S.GraphImpactRequest,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    _require_ontology(db, ontology_id)
    try:
        _, _, scope = build_scope(db, ontology_id)
        return _ok(analyze_change_impact(
            scope,
            body.instance_id,
            body.property,
            body.proposed_value,
            direction=body.direction,
            max_depth=body.max_depth,
        ))
    except ToolError as exc:
        raise HTTPException(422, str(exc)) from exc


# ---------------------------------------------------------------- 分析报告模板


def _template_out(row: AnalysisReportTemplate) -> dict:
    return S.ReportTemplateOut.model_validate(row).model_dump(by_alias=True)


def _run_out(row: AnalysisReportRun, *, include_html: bool = True) -> dict:
    data = S.ReportRunOut.model_validate(row).model_dump(by_alias=True)
    if not include_html:
        data["htmlContent"] = ""
        data["sectionResults"] = []
    return data


def _require_report_template(db: Session, ontology_id: str, template_id: str,
                             current_user) -> AnalysisReportTemplate:
    row = db.query(AnalysisReportTemplate).filter(
        AnalysisReportTemplate.id == template_id,
        AnalysisReportTemplate.ontology_id == ontology_id,
    ).first()
    if not row:
        raise HTTPException(404, "分析报告模板不存在")
    if row.status != "published" and row.created_by != getattr(current_user, "id", None) \
            and getattr(current_user, "role", "") != "admin":
        raise HTTPException(403, "无权访问该分析报告模板")
    return row


def _require_report_run(db: Session, ontology_id: str, run_id: str,
                        current_user) -> AnalysisReportRun:
    row = db.query(AnalysisReportRun).filter(
        AnalysisReportRun.id == run_id,
        AnalysisReportRun.ontology_id == ontology_id,
    ).first()
    if not row:
        raise HTTPException(404, "分析报告运行记录不存在")
    if row.created_by != getattr(current_user, "id", None) \
            and getattr(current_user, "role", "") != "admin":
        raise HTTPException(403, "无权访问该分析报告运行记录")
    return row


@router.get("/{ontology_id}/agent/report-templates")
def list_report_templates(ontology_id: str, db: Session = Depends(get_db),
                          current_user=Depends(get_current_user)):
    _require_ontology(db, ontology_id)
    query = db.query(AnalysisReportTemplate).filter(
        AnalysisReportTemplate.ontology_id == ontology_id)
    if getattr(current_user, "role", "") != "admin":
        query = query.filter(or_(
            AnalysisReportTemplate.created_by == getattr(current_user, "id", None),
            AnalysisReportTemplate.status == "published",
        ))
    rows = query.order_by(AnalysisReportTemplate.updated_at.desc()).limit(100).all()
    return _ok([_template_out(row) for row in rows])


@router.post("/{ontology_id}/agent/report-templates/ai-draft", status_code=201)
def create_ai_report_template(ontology_id: str, body: S.ReportTemplateAIDraftRequest,
                              db: Session = Depends(get_db),
                              current_user=Depends(get_current_user)):
    _require_ontology(db, ontology_id)
    brief = (body.brief or "").strip()
    if len(brief) < 8:
        raise HTTPException(422, "请用至少 8 个字说明报告面向谁、要回答什么问题")

    context = ""
    if body.conversation_id:
        conversation = _require_conversation(db, ontology_id, body.conversation_id, current_user)
        messages = (db.query(AgentMessage)
                    .filter(AgentMessage.conversation_id == conversation.id)
                    .order_by(AgentMessage.created_at.asc()).limit(30).all())
        context = "\n".join(
            f"{'用户' if item.role == 'user' else '助手'}：{(item.content or '')[:500]}"
            for item in messages if (item.content or "").strip())

    try:
        spec = reporting.generate_template_spec(
            db, ontology_id, brief, model_id=body.model_id,
            conversation_context=context)
        sections = reporting.normalize_sections(spec["sections"])
    except (ToolError, ValueError) as exc:
        raise HTTPException(422, str(exc))

    row = AnalysisReportTemplate(
        ontology_id=ontology_id,
        created_by=getattr(current_user, "id", None),
        name=spec["name"],
        description=spec.get("description") or "",
        source_prompt=brief,
        generation_mode=spec.get("generationMode") or "ai",
        sections=sections,
        style=reporting.normalize_style(spec.get("style")),
        default_model_id=body.model_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _ok(_template_out(row))


@router.get("/{ontology_id}/agent/report-templates/{template_id}")
def get_report_template(ontology_id: str, template_id: str,
                        db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    return _ok(_template_out(
        _require_report_template(db, ontology_id, template_id, current_user)))


@router.put("/{ontology_id}/agent/report-templates/{template_id}")
def update_report_template(ontology_id: str, template_id: str,
                           body: S.ReportTemplateUpdate,
                           db: Session = Depends(get_db),
                           current_user=Depends(get_current_user)):
    row = _require_report_template(db, ontology_id, template_id, current_user)
    if row.status == "published":
        raise HTTPException(409, "已发布模板不可原地修改；请基于它创建新草稿版本")
    if body.expected_revision != row.revision:
        raise HTTPException(409, detail={
            "code": "report_revision_conflict",
            "message": "模板已在其他页面更新，请刷新后再编辑，避免覆盖较新的修改",
            "currentRevision": row.revision,
        })
    try:
        sections = reporting.normalize_sections(body.sections)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    name = (body.name or "").strip()[:240]
    if not name:
        raise HTTPException(422, "报告名称不能为空")
    next_state = {
        "name": name,
        "description": (body.description or "").strip()[:5000],
        "sections": sections,
        "style": reporting.normalize_style(body.style),
        "default_model_id": body.default_model_id,
    }
    current_state = {key: getattr(row, key) for key in next_state}
    if current_state != next_state:
        for key, value in next_state.items():
            setattr(row, key, value)
        row.revision = (row.revision or 0) + 1
        row.last_preview_run_id = None
        row.last_preview_revision = None
        db.commit()
        db.refresh(row)
    return _ok(_template_out(row))


@router.delete("/{ontology_id}/agent/report-templates/{template_id}", status_code=204)
def delete_report_template(ontology_id: str, template_id: str,
                           db: Session = Depends(get_db),
                           current_user=Depends(get_current_user)):
    row = _require_report_template(db, ontology_id, template_id, current_user)
    if row.status == "published":
        raise HTTPException(409, "已发布模板不可删除，请保留运行审计记录")
    db.query(AnalysisReportRun).filter(AnalysisReportRun.template_id == row.id).delete()
    db.delete(row)
    db.commit()


@router.post("/{ontology_id}/agent/report-templates/{template_id}/preview")
def preview_report_template(ontology_id: str, template_id: str,
                            body: S.ReportRunRequest,
                            db: Session = Depends(get_db),
                            current_user=Depends(get_current_user)):
    row = _require_report_template(db, ontology_id, template_id, current_user)
    if row.status == "published":
        raise HTTPException(409, "已发布模板请使用正式运行入口")
    run = reporting.execute_report(db, row, current_user, "preview", body.model_id)
    return _ok(_run_out(run))


@router.post("/{ontology_id}/agent/report-templates/{template_id}/publish")
def publish_report_template(ontology_id: str, template_id: str,
                            db: Session = Depends(get_db),
                            current_user=Depends(get_current_user)):
    row = _require_report_template(db, ontology_id, template_id, current_user)
    if row.status == "published":
        return _ok(_template_out(row))
    if not row.last_preview_run_id or row.last_preview_revision != row.revision:
        raise HTTPException(409, detail={
            "code": "report_preview_required",
            "message": "模板已变化或尚未试运行，请重新查询真实数据并确认结果后再发布",
        })
    run = db.query(AnalysisReportRun).filter(
        AnalysisReportRun.id == row.last_preview_run_id,
        AnalysisReportRun.template_id == row.id,
    ).first()
    if not run or run.status != "succeeded":
        raise HTTPException(409, "最近一次真实数据试运行未成功")
    quality = run.quality_report or {}
    if not quality.get("passed"):
        raise HTTPException(422, detail={
            "code": "report_quality_gate_blocked",
            "message": quality.get("summary") or "报告未达到汇报级发布标准",
            "quality": quality,
        })
    row.status = "published"
    row.published_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _ok(_template_out(row))


@router.post("/{ontology_id}/agent/report-templates/{template_id}/runs")
def run_published_report(ontology_id: str, template_id: str,
                         body: S.ReportRunRequest,
                         db: Session = Depends(get_db),
                         current_user=Depends(get_current_user)):
    row = _require_report_template(db, ontology_id, template_id, current_user)
    if row.status != "published":
        raise HTTPException(409, "模板尚未发布，只能先执行真实数据试运行")
    run = reporting.execute_report(db, row, current_user, "manual", body.model_id)
    return _ok(_run_out(run))


@router.get("/{ontology_id}/agent/report-templates/{template_id}/runs")
def list_report_runs(ontology_id: str, template_id: str,
                     db: Session = Depends(get_db),
                     current_user=Depends(get_current_user)):
    row = _require_report_template(db, ontology_id, template_id, current_user)
    query = db.query(AnalysisReportRun).filter(AnalysisReportRun.template_id == row.id)
    # 已发布模板可被本体用户复用，但每次运行仍属于发起者自己的审计记录。
    # 列表与详情使用同一权限边界，避免列表展示一条随后无法打开的他人运行。
    if getattr(current_user, "role", "") != "admin":
        query = query.filter(
            AnalysisReportRun.created_by == getattr(current_user, "id", None))
    runs = query.order_by(AnalysisReportRun.started_at.desc()).limit(50).all()
    return _ok([_run_out(run, include_html=False) for run in runs])


@router.get("/{ontology_id}/agent/report-runs/{run_id}")
def get_report_run(ontology_id: str, run_id: str,
                   db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    return _ok(_run_out(_require_report_run(db, ontology_id, run_id, current_user)))


@router.get("/{ontology_id}/agent/report-runs/{run_id}/html", response_class=HTMLResponse)
def get_report_html(ontology_id: str, run_id: str,
                    db: Session = Depends(get_db),
                    current_user=Depends(get_current_user)):
    run = _require_report_run(db, ontology_id, run_id, current_user)
    if run.status != "succeeded" or not run.html_content:
        raise HTTPException(409, "该运行尚无可用 HTML 报告")
    return HTMLResponse(
        content=run.html_content,
        headers={
            "Content-Disposition": f'inline; filename="analysis-report-{run.id[:8]}.html"',
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )
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
    _require_ontology(db, ontology_id)
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
