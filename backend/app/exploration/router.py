"""业务探索 API — /api/v2/exploration/*

  GET    /sessions                       当前用户的探索会话列表
  POST   /sessions                       新建会话
  GET    /sessions/{id}                  会话详情（消息 + 画布 + 完整度）
  DELETE /sessions/{id}
  POST   /sessions/{id}/chat             探索对话（默认 SSE；stream=false 同步）
  GET    /sessions/{id}/canvas           当前画布 + 完整度
  GET    /sessions/{id}/attachments      会话附件列表（仅本会话可见）
  POST   /sessions/{id}/attachments      上传参考资料（确定性转文本，注入对话上下文）
  DELETE /sessions/{id}/attachments/{aid}
  POST   /sessions/{id}/documents        从画布生成需求文档（版本递增）
  GET    /sessions/{id}/documents        文档列表
  GET    /sessions/{id}/drafts           草稿列表
  GET    /documents/{id}                 文档详情
  POST   /documents/{id}/drafts          需求文档 → 本体草稿（转化管线）
  GET    /drafts/{id}
  POST   /drafts/{id}/apply              人审勾选后落地（新建本体或保守合并；可重复应用）
  POST   /drafts/{id}/discard            废弃草稿（幂等；废弃后不可应用）
"""
import json
import logging
import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import get_db, get_current_user
from app.model_configs.selector import select_llm_model_config, llm_call_kwargs
from app.models.ontology import OntologyProject
from app.ontologies.release_context import create_initial_release
from app.exploration import canvas as C
from app.exploration import converter, readiness as R, schemas as S
from app.exploration import workspace as W
from app.exploration.diagram import DIAGRAM_KINDS, DiagramError, build_diagram
from app.exploration.document import generate_document
from app.exploration.models import (ExplorationAttachment, ExplorationDocument,
                                    ExplorationDraft, ExplorationMessage,
                                    ExplorationSession)
from app.exploration.orchestrator import run_exploration_turn

router = APIRouter()
logger = logging.getLogger(__name__)


def _ok(data):
    return {"data": data}


def _require_session(db: Session, session_id: str, current_user) -> ExplorationSession:
    s = db.query(ExplorationSession).filter(ExplorationSession.id == session_id).first()
    if not s:
        raise HTTPException(404, "探索会话不存在")
    if s.user_id and s.user_id != getattr(current_user, "id", None) \
            and getattr(current_user, "role", "") != "admin":
        raise HTTPException(403, "无权访问他人会话")
    return s


def _session_out(s: ExplorationSession) -> dict:
    return S.SessionOut.model_validate(s).model_dump(by_alias=True)


def _message_out(message: ExplorationMessage, canvas: dict) -> dict:
    """历史图表不能绕过当前质量门：读取时从当前画布重新确定性生成。

    画布已变化或旧版本曾保存半成品时，有效图会刷新为当前投影；质量不合格
    的图从响应中移除并留下可操作错误。数据库原始审计轨迹保持不变。
    """
    data = S.MessageOut.model_validate(message).model_dump(by_alias=True)
    for step in data.get("steps") or []:
        stored = step.get("diagram")
        if not isinstance(stored, dict):
            continue
        arguments = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
        kind = str(stored.get("kind") or arguments.get("kind") or "")
        target = arguments.get("target")
        if not target and "·" not in str(stored.get("target") or ""):
            target = stored.get("target")
        try:
            step["diagram"] = build_diagram(canvas, kind, str(target) if target else None)
        except DiagramError as error:
            step.pop("diagram", None)
            step["error"] = f"历史图表已被质量门拦截：{error}"
            step["summary"] = "历史图表不再满足当前画布的质量要求，请让 AI 修复后重新生成"
    return data


# ---------------------------------------------------------------- 会话


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rows = (db.query(ExplorationSession)
            .filter(ExplorationSession.user_id == getattr(current_user, "id", None))
            .order_by(ExplorationSession.updated_at.desc())
            .limit(100).all())
    return _ok([_session_out(s) for s in rows])


@router.post("/sessions", status_code=201)
def create_session(body: S.SessionCreate, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    s = ExplorationSession(user_id=getattr(current_user, "id", None),
                           title=(body.title or "").strip() or "新的业务探索",
                           canvas=C.empty_canvas())
    db.add(s)
    db.commit()
    db.refresh(s)
    return _ok(_session_out(s))


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db: Session = Depends(get_db),
                current_user=Depends(get_current_user)):
    s = _require_session(db, session_id, current_user)
    messages = (db.query(ExplorationMessage)
                .filter(ExplorationMessage.session_id == s.id)
                .order_by(ExplorationMessage.created_at.asc())
                .limit(300).all())
    return _ok({
        **_session_out(s),
        "canvas": C._ensure_canvas(s.canvas),
        "completeness": C.completeness(s.canvas),
        "readiness": R.evaluate(s.canvas),
        "messages": [_message_out(message, s.canvas) for message in messages],
    })


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    s = _require_session(db, session_id, current_user)
    for a in db.query(ExplorationAttachment).filter(ExplorationAttachment.session_id == s.id).all():
        _remove_attachment_file(a.file_path)
    db.query(ExplorationAttachment).filter(ExplorationAttachment.session_id == s.id).delete()
    db.query(ExplorationMessage).filter(ExplorationMessage.session_id == s.id).delete()
    db.query(ExplorationDraft).filter(ExplorationDraft.session_id == s.id).delete()
    db.query(ExplorationDocument).filter(ExplorationDocument.session_id == s.id).delete()
    db.delete(s)
    db.commit()


@router.get("/sessions/{session_id}/canvas")
def get_canvas(session_id: str, db: Session = Depends(get_db),
               current_user=Depends(get_current_user)):
    s = _require_session(db, session_id, current_user)
    return _ok({"canvas": C._ensure_canvas(s.canvas), "version": s.canvas_version,
                "completeness": C.completeness(s.canvas),
                "readiness": R.evaluate(s.canvas)})


@router.get("/sessions/{session_id}/readiness")
def get_readiness(session_id: str, db: Session = Depends(get_db),
                  current_user=Depends(get_current_user)):
    """质量门报告（确定性评估，与草稿生成闸门同一口径）。"""
    s = _require_session(db, session_id, current_user)
    return _ok(R.evaluate(s.canvas))


@router.get("/sessions/{session_id}/diagrams/{kind}")
def get_diagram(session_id: str, kind: str, target: str | None = None,
                db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """画布 → 业务建模图表（er/flow/sequence/state，确定性生成，不经 LLM）。

    flow/sequence 可用 ?target=场景名 指定场景；state 用 ?target=对象名。
    """
    s = _require_session(db, session_id, current_user)
    try:
        return _ok(build_diagram(s.canvas, kind, target))
    except DiagramError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/sessions/{session_id}/diagrams")
def list_diagram_kinds(session_id: str, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    _require_session(db, session_id, current_user)
    return _ok({"kinds": [{"kind": k, "label": v} for k, v in DIAGRAM_KINDS.items()]})


# ---------------------------------------------------------------- 会话附件


def _remove_attachment_file(path: str | None) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            logger.warning("附件文件清理失败: %s", path)


def _attachment_out(a: ExplorationAttachment) -> dict:
    # 迁移前记录没有逻辑路径；对外始终给出可展示路径。
    if not a.relative_path:
        a.relative_path = a.filename
    return S.AttachmentOut.model_validate(a).model_dump(by_alias=True)


@router.get("/sessions/{session_id}/attachments")
def list_attachments(session_id: str, db: Session = Depends(get_db),
                     current_user=Depends(get_current_user)):
    s = _require_session(db, session_id, current_user)
    rows = (db.query(ExplorationAttachment)
            .filter(ExplorationAttachment.session_id == s.id)
            .order_by(ExplorationAttachment.created_at.asc()).all())
    return _ok([_attachment_out(a) for a in rows])


@router.post("/sessions/{session_id}/attachments", status_code=201)
async def upload_attachment(session_id: str, file: UploadFile = File(...),
                            db: Session = Depends(get_db),
                            current_user=Depends(get_current_user)):
    """上传会话参考资料：确定性转为文本后随每个对话回合注入引导师上下文。
    附件严格绑定本会话，跨会话不可见，随会话删除一并清理。"""
    s = _require_session(db, session_id, current_user)

    ext_name = (file.filename or "").rsplit(".", 1)[-1].lower()
    allowed = {e.strip() for e in settings.allowed_upload_extensions.split(",") if e.strip()}
    if ext_name not in allowed:
        raise HTTPException(400, f"不支持的文件类型: .{ext_name}（允许: {settings.allowed_upload_extensions}）")

    content = await file.read()
    row = W.create_bytes(db, s, file.filename or "attachment", content,
                         file.content_type, source="upload")
    return _ok(_attachment_out(row))


@router.post("/sessions/{session_id}/workspace/files", status_code=201)
def create_workspace_text_file(session_id: str, body: S.WorkspaceTextCreate,
                               db: Session = Depends(get_db),
                               current_user=Depends(get_current_user)):
    """在会话空间中新建文本文件（用户与 Agent 使用同一套并发/隔离契约）。"""
    s = _require_session(db, session_id, current_user)
    row = W.create_text(db, s, body.path, body.content, body.mime_type, source="user")
    return _ok(_attachment_out(row))


@router.get("/sessions/{session_id}/attachments/{attachment_id}/content")
def get_workspace_text_file(session_id: str, attachment_id: str,
                            db: Session = Depends(get_db),
                            current_user=Depends(get_current_user)):
    s = _require_session(db, session_id, current_user)
    row = W.require_file(db, s.id, attachment_id)
    return _ok(S.WorkspaceTextOut(
        id=row.id, relative_path=row.relative_path or row.filename,
        content=W.read_text(row), version=row.version or 1,
        sha256=row.sha256).model_dump(by_alias=True))


@router.get("/sessions/{session_id}/attachments/{attachment_id}/preview")
def preview_workspace_file(session_id: str, attachment_id: str,
                           db: Session = Depends(get_db),
                           current_user=Depends(get_current_user)):
    """预览常见文件：文本读取原文，Office/PDF 等返回确定性抽取文本。"""
    s = _require_session(db, session_id, current_user)
    row = W.require_file(db, s.id, attachment_id)
    content = W.read_text(row) if row.editable else (row.extracted_text or "")
    return _ok(S.WorkspacePreviewOut(
        id=row.id,
        relative_path=row.relative_path or row.filename,
        content=content,
        version=row.version or 1,
        mime_type=row.mime_type,
        editable=bool(row.editable),
        truncated=(row.char_count or 0) > len(content),
    ).model_dump(by_alias=True))


@router.put("/sessions/{session_id}/attachments/{attachment_id}/content")
def update_workspace_text_file(session_id: str, attachment_id: str,
                               body: S.WorkspaceTextUpdate,
                               db: Session = Depends(get_db),
                               current_user=Depends(get_current_user)):
    s = _require_session(db, session_id, current_user)
    row = W.require_file(db, s.id, attachment_id)
    row = W.update_text(db, row, body.content, body.expected_version, source="user")
    return _ok(_attachment_out(row))


@router.get("/sessions/{session_id}/attachments/{attachment_id}/download")
def download_workspace_file(session_id: str, attachment_id: str,
                            db: Session = Depends(get_db),
                            current_user=Depends(get_current_user)):
    s = _require_session(db, session_id, current_user)
    row = W.require_file(db, s.id, attachment_id)
    if not row.file_path or not os.path.isfile(row.file_path):
        raise HTTPException(410, "文件内容已丢失")
    return FileResponse(row.file_path, media_type=row.mime_type or "application/octet-stream",
                        filename=row.filename or "download")


@router.delete("/sessions/{session_id}/attachments/{attachment_id}", status_code=204)
def delete_attachment(session_id: str, attachment_id: str, db: Session = Depends(get_db),
                      current_user=Depends(get_current_user)):
    s = _require_session(db, session_id, current_user)
    row = W.require_file(db, s.id, attachment_id)
    W.delete_file(db, row)


# ---------------------------------------------------------------- 对话


@router.post("/sessions/{session_id}/chat")
def chat(session_id: str, body: S.ChatRequest,
         db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require_session(db, session_id, current_user)
    if not (body.message or "").strip():
        raise HTTPException(422, "message 不能为空")

    if not body.stream:
        events = list(run_exploration_turn(
            db, session_id, current_user, body.message,
            model_id=body.model_id, web_search=body.web_search,
        ))
        answer = next((e for e in events if e["type"] == "answer"), None)
        error = next((e for e in events if e["type"] == "error"), None)
        meta = next((e for e in events if e["type"] == "meta"), {})
        canvas_ev = next((e for e in reversed(events) if e["type"] == "canvas"), None)
        steps = [e for e in events if e["type"] == "step"]
        return _ok({
            "sessionId": meta.get("sessionId") or session_id,
            "model": meta.get("model"),
            "steps": [{k: v for k, v in s.items() if k != "type"} for s in steps],
            "content": (answer or {}).get("content"),
            "usage": (answer or {}).get("usage"),
            "canvas": (canvas_ev or {}).get("canvas"),
            "completeness": (canvas_ev or {}).get("completeness"),
            "error": (error or {}).get("message"),
        })

    user = current_user

    def event_stream():
        # SSE 生成器生命周期长于请求依赖，自建 session（同 agent_runtime 的处理）
        from app.database import SessionLocal
        session = SessionLocal()
        try:
            for event in run_exploration_turn(
                session, session_id, user, body.message,
                model_id=body.model_id, web_search=body.web_search,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        finally:
            session.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- 需求文档


@router.post("/sessions/{session_id}/documents", status_code=201)
def create_document(session_id: str, body: S.GenerateDocumentRequest,
                    db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    s = _require_session(db, session_id, current_user)
    comp = C.completeness(s.canvas)
    if not any(comp["counts"].values()):
        raise HTTPException(422, "画布还是空的 —— 先通过对话沉淀一些业务模型再生成文档")
    cfg = select_llm_model_config(db, model_id=body.model_id)
    try:
        call_kwargs = llm_call_kwargs(cfg)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    doc = generate_document(db, s, call_kwargs)
    return _ok(S.DocumentOut.model_validate(doc).model_dump(by_alias=True))


@router.get("/sessions/{session_id}/documents")
def list_documents(session_id: str, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    s = _require_session(db, session_id, current_user)
    rows = (db.query(ExplorationDocument)
            .filter(ExplorationDocument.session_id == s.id)
            .order_by(ExplorationDocument.version.desc()).all())
    return _ok([S.DocumentListItem.model_validate(d).model_dump(by_alias=True) for d in rows])


def _require_document(db: Session, document_id: str, current_user) -> ExplorationDocument:
    d = db.query(ExplorationDocument).filter(ExplorationDocument.id == document_id).first()
    if not d:
        raise HTTPException(404, "需求文档不存在")
    _require_session(db, d.session_id, current_user)
    return d


@router.get("/documents/{document_id}")
def get_document(document_id: str, db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    d = _require_document(db, document_id, current_user)
    return _ok(S.DocumentOut.model_validate(d).model_dump(by_alias=True))


# ---------------------------------------------------------------- 本体草稿


@router.post("/documents/{document_id}/drafts", status_code=201)
def create_draft(document_id: str, body: S.GenerateDraftRequest,
                 db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """需求文档 → 本体草稿。质量门是准入闸：堵门项未清零时拒绝生成，
    除非 force=true 显式越权（越权事实与未决项写入草稿报告，人审可见）。"""
    d = _require_document(db, document_id, current_user)

    rd = R.evaluate(d.canvas_snapshot or {})
    if not rd["ready"] and not body.force:
        raise HTTPException(422, detail={
            "code": "quality_gate_blocked",
            "message": (f"质量门未通过（{rd['gatesPassed']}/{rd['gatesTotal']} 门，"
                        f"剩余 {rd['blockingCount']} 项堵门问题）。"
                        "请回到对话完成定量澄清后重新生成文档，或显式越权强制生成。"),
            "readiness": rd,
        })

    existing = None
    if body.target_ontology_id:
        if not db.query(OntologyProject).filter(
                OntologyProject.id == body.target_ontology_id).first():
            raise HTTPException(404, "目标本体不存在")
        existing = converter.existing_name_sets(db, body.target_ontology_id)

    cfg = select_llm_model_config(db, model_id=body.model_id)
    try:
        call_kwargs = llm_call_kwargs(cfg)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    draft_data, report = converter.build_draft(
        d.canvas_snapshot or {}, existing=existing, call_kwargs=call_kwargs)

    # 质量门结论随草稿留档：人审抽屉与落地审计都能看到生成时刻的门禁状态
    report["readiness"] = {k: rd[k] for k in
                           ("ready", "stage", "gatesPassed", "gatesTotal",
                            "blockingCount", "advisoryCount")}
    if not rd["ready"]:
        report["gateOverride"] = True
        blocking = [f"[{g['label']}] {item}"
                    for g in rd["gates"] for item in g["blockingItems"]]
        report["warnings"] = (
            [f"⚠️ 质量门未通过被显式越权：{rd['blockingCount']} 项堵门问题未解决即生成草稿"]
            + blocking[:12] + report.get("warnings", []))

    row = ExplorationDraft(session_id=d.session_id, document_id=d.id,
                           target_ontology_id=body.target_ontology_id,
                           draft=draft_data, report=report)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _ok(S.DraftOut.model_validate(row).model_dump(by_alias=True))


@router.get("/sessions/{session_id}/drafts")
def list_drafts(session_id: str, db: Session = Depends(get_db),
                current_user=Depends(get_current_user)):
    s = _require_session(db, session_id, current_user)
    rows = (db.query(ExplorationDraft)
            .filter(ExplorationDraft.session_id == s.id)
            .order_by(ExplorationDraft.created_at.desc()).all())
    return _ok([S.DraftOut.model_validate(r).model_dump(by_alias=True) for r in rows])


def _require_draft(db: Session, draft_id: str, current_user) -> ExplorationDraft:
    r = db.query(ExplorationDraft).filter(ExplorationDraft.id == draft_id).first()
    if not r:
        raise HTTPException(404, "本体草稿不存在")
    _require_session(db, r.session_id, current_user)
    return r


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, db: Session = Depends(get_db),
              current_user=Depends(get_current_user)):
    r = _require_draft(db, draft_id, current_user)
    return _ok(S.DraftOut.model_validate(r).model_dump(by_alias=True))


@router.post("/drafts/{draft_id}/validate")
def validate_draft(draft_id: str, body: S.DraftValidationRequest,
                   db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """按最终选择集执行确定性预检；与 apply 使用完全相同的校验函数。"""
    r = _require_draft(db, draft_id, current_user)
    target_id = r.applied_ontology_id or r.target_ontology_id
    existing = converter.existing_name_sets(db, target_id) if target_id else None
    return _ok(converter.validate_draft_selection(
        r.draft or {}, body.selected_keys, existing=existing))


@router.post("/drafts/{draft_id}/apply")
def apply_draft(draft_id: str, body: S.ApplyDraftRequest,
                db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """人审勾选后的真实落地。草稿→本体是唯一写路径，且保守合并（同名跳过）。

    可重复应用（同名跳过使其幂等）：部分勾选落地后，剩余元素可再次勾选落地；
    首次落地后再次应用固定合并进首次的目标本体，不再新建。废弃的草稿不可应用。
    """
    r = _require_draft(db, draft_id, current_user)
    if r.status == "discarded":
        raise HTTPException(409, "该草稿已废弃，不可应用；如需落地请重新生成草稿")
    if body.selected_keys is not None and len(body.selected_keys) == 0:
        raise HTTPException(422, "未勾选任何草稿元素")

    validation_target_id = r.applied_ontology_id or r.target_ontology_id
    validation_existing = (converter.existing_name_sets(db, validation_target_id)
                           if validation_target_id else None)
    validation = converter.validate_draft_selection(
        r.draft or {}, body.selected_keys, existing=validation_existing)
    if not validation["valid"]:
        raise HTTPException(422, detail={
            "code": "draft_validation_failed",
            "message": f"本体草稿选择集预检未通过（{len(validation['errors'])} 项错误），已拒绝落地",
            "validation": validation,
        })

    project = None
    created_project = False
    if r.applied_ontology_id:
        # 再次应用：固定回到首次落地的本体（该本体被删则回退常规目标解析）
        project = db.query(OntologyProject).filter(
            OntologyProject.id == r.applied_ontology_id).first()
    if project is None and r.target_ontology_id:
        project = db.query(OntologyProject).filter(
            OntologyProject.id == r.target_ontology_id).first()
        if not project:
            raise HTTPException(404, "目标本体不存在（可能已被删除）")
    if project is None:
        if not body.new_ontology or not (body.new_ontology.name or "").strip():
            raise HTTPException(422, "该草稿目标为新建本体，请提供 newOntology.name")
        name = body.new_ontology.name.strip()
        if db.query(OntologyProject).filter(OntologyProject.name.ilike(name)).first():
            raise HTTPException(409, f"本体名称「{name}」已存在")
        project = OntologyProject(
            name=name, domain=(body.new_ontology.domain or "业务探索").strip() or "业务探索",
            description=body.new_ontology.description or f"由业务探索会话生成（草稿 {r.id[:8]}）",
            build_mode="business_exploration",
            created_by=getattr(current_user, "id", None))
        db.add(project)
        db.flush()
        created_project = True

    if not created_project:
        # Freeze a legacy project's existing structure before merging new draft
        # content, so the merge remains outside its current released snapshot.
        from app.ontologies.versions.router import _current_release

        _current_release(db, project)

    result = converter.apply_draft(
        db, r.draft or {}, body.selected_keys, project.id,
        lineage={"sessionId": r.session_id, "documentId": r.document_id, "draftId": r.id})
    if created_project:
        from app.ontologies.versions.router import _snapshot_formal

        create_initial_release(
            db,
            project,
            snapshot=_snapshot_formal(db, project.id),
            created_by=getattr(current_user, "id", None),
            version_label="业务探索初始基线",
            description="由业务探索草稿生成的完整发布基线",
        )
    r.status = "applied"
    r.applied_ontology_id = project.id
    db.commit()
    return _ok({"ontologyId": project.id, "ontologyName": project.name, **result})


@router.post("/drafts/{draft_id}/discard")
def discard_draft(draft_id: str, db: Session = Depends(get_db),
                  current_user=Depends(get_current_user)):
    """废弃草稿（幂等）：废弃后不可再应用；记录保留在列表中可追溯。"""
    r = _require_draft(db, draft_id, current_user)
    if r.status != "discarded":
        r.status = "discarded"
        db.commit()
        db.refresh(r)
    return _ok(S.DraftOut.model_validate(r).model_dump(by_alias=True))
