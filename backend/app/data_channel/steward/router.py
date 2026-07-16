"""
数据管家 API — /api/v2/steward/*

  GET    /status                     n8n 配置/连通性 + LLM 可用性 + 记录统计
  POST   /chat                       对话（默认 SSE 流式；stream=false 同步返回）
  GET    /conversations              当前用户的会话列表
  GET    /conversations/{id}         会话详情（含完整工具轨迹，审计视图）
  DELETE /conversations/{id}
  GET    /pipelines                  受管流水线记录列表（受管流水线面板）
  GET    /pipelines/{id}             记录详情（workflow 摘要，只读）
  POST   /pipelines/bootstrap        流水线列表「新建 n8n 流水线」：骨架工作流登记

职权边界：数据管家对 n8n 只有两项持久写权限——新建流水线（bootstrap / 对话工具
create_pipeline）与编排未发布未启用的流水线（对话工具 update_workflow）。此外，
它可按用户明确要求触发一次不入湖、执行后恢复启停状态的预览，也可在当前会话
隔离空间创建、编辑和删除文件，但不能访问任何其他文件路径。
发布只存在于流水线编辑向导，且发布后永久封版；
归档/删除不走 steward，归档在流水线列表（走 service.archive）。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import get_db, get_current_user
from app.model_configs.selector import select_llm_model_config
from app.settings.workflows.n8n_client import N8nApiError
from app.data_channel.steward import service
from app.data_channel.steward.models import (
    BROWSER_SOURCE_REMOTE_CDP, N8nPipeline, StewardBrowserSource,
    StewardConversation, StewardMessage, STATUS_ARCHIVED,
)
from app.data_channel.steward import browser_sources
from app.data_channel.steward.orchestrator import run_steward_turn
from app.data_channel.steward import workspace
from app.data_channel.steward.browser_runtime import (
    BrowserRuntimeError, browser_manager, probe_browser_cdp,
)
from app.data_channel.steward.service import StewardError

router = APIRouter()
logger = logging.getLogger(__name__)


def _ok(data):
    return {"data": data}


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, StewardError):
        return HTTPException(400, str(exc))
    if isinstance(exc, N8nApiError):
        return HTTPException(502, f"n8n API 错误 (HTTP {exc.status_code}): {exc.message}")
    logger.exception("数据管家操作失败")
    return HTTPException(500, f"操作失败: {exc}")


# ── 状态 ──────────────────────────────────────────────────────────

@router.get("/status")
def steward_status(db: Session = Depends(get_db), _=Depends(get_current_user)):
    n8n = service.n8n_config_status(db)
    if n8n["configured"] and n8n["enabled"]:
        try:
            service.get_n8n_client(db).test_connection()
            n8n["reachable"] = True
        except Exception as e:  # noqa: BLE001
            n8n["reachable"] = False
            n8n["error"] = str(e)[:300]
    llm_ready = select_llm_model_config(db) is not None

    # 统计按发布状态（影子流水线是生命周期唯一真源）：draft / published
    counts: dict[str, int] = {}
    for rec in db.query(N8nPipeline).filter(N8nPipeline.status != STATUS_ARCHIVED).all():
        pub = service.shadow_status(db, rec)
        counts[pub] = counts.get(pub, 0) + 1
    return _ok({"n8n": n8n, "llmReady": llm_ready, "pipelineCounts": counts})


# ── 对话 ──────────────────────────────────────────────────────────

class ChatBody(BaseModel):
    message: str
    conversationId: Optional[str] = None
    modelId: Optional[str] = None
    targetRecordId: Optional[str] = None
    stream: bool = True


class CreateConversationBody(BaseModel):
    title: str = "新对话"


@router.post("/chat")
def chat(body: ChatBody, db: Session = Depends(get_db),
         current_user=Depends(get_current_user)):
    if not (body.message or "").strip():
        raise HTTPException(422, "message 不能为空")

    if not body.stream:
        events = list(run_steward_turn(db, current_user, body.message,
                                       conversation_id=body.conversationId,
                                       model_id=body.modelId,
                                       target_record_id=body.targetRecordId))
        answer = next((e for e in events if e["type"] == "answer"), None)
        error = next((e for e in events if e["type"] == "error"), None)
        meta = next((e for e in events if e["type"] == "meta"), {})
        steps = [e for e in events if e["type"] == "step"]
        return _ok({
            "conversationId": meta.get("conversationId"),
            "model": meta.get("model"),
            "steps": [{k: v for k, v in s.items() if k != "type"} for s in steps],
            "content": (answer or {}).get("content"),
            "touchedPipelineIds": (answer or {}).get("touchedPipelineIds") or [],
            "usage": (answer or {}).get("usage"),
            "error": (error or {}).get("message"),
        })

    # SSE：流式生成器的生命周期长于请求依赖，自建 session 规避提前关闭问题
    user = current_user

    def event_stream():
        from app.database import SessionLocal
        session = SessionLocal()
        try:
            for event in run_steward_turn(session, user, body.message,
                                          conversation_id=body.conversationId,
                                          model_id=body.modelId,
                                          target_record_id=body.targetRecordId):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        finally:
            session.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ── 会话 ──────────────────────────────────────────────────────────

def _conv_out(c: StewardConversation) -> dict:
    return {"id": c.id, "title": c.title,
            "browserSourceId": c.browser_source_id or browser_sources.MANAGED_SOURCE_ID,
            "createdAt": c.created_at.isoformat() if c.created_at else None,
            "updatedAt": c.updated_at.isoformat() if c.updated_at else None}


def _msg_out(m: StewardMessage) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content or "",
            "steps": m.steps or [], "touchedPipelineIds": m.touched_pipeline_ids or [],
            "model": m.model,
            "createdAt": m.created_at.isoformat() if m.created_at else None}


def _require_conversation(db: Session, conversation_id: str, current_user) -> StewardConversation:
    conv = db.query(StewardConversation).filter(
        StewardConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "会话不存在")
    if conv.user_id and conv.user_id != getattr(current_user, "id", None) \
            and getattr(current_user, "role", "") != "admin":
        raise HTTPException(403, "无权访问他人会话")
    return conv


@router.post("/conversations", status_code=201)
def create_conversation(body: CreateConversationBody, db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    conv = StewardConversation(
        user_id=getattr(current_user, "id", None),
        title=(body.title or "新对话").strip()[:200] or "新对话",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    workspace.session_root(conv.id)
    return _ok(_conv_out(conv))


@router.get("/conversations")
def list_conversations(db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    rows = (db.query(StewardConversation)
            .filter(StewardConversation.user_id == getattr(current_user, "id", None))
            .order_by(StewardConversation.updated_at.desc())
            .limit(50).all())
    return _ok([_conv_out(c) for c in rows])


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, db: Session = Depends(get_db),
                     current_user=Depends(get_current_user)):
    conv = _require_conversation(db, conversation_id, current_user)
    messages = (db.query(StewardMessage)
                .filter(StewardMessage.conversation_id == conv.id)
                .order_by(StewardMessage.created_at.asc())
                .limit(200).all())
    return _ok({**_conv_out(conv), "messages": [_msg_out(m) for m in messages]})


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    conv = _require_conversation(db, conversation_id, current_user)
    try:
        browser_manager.close(conv.id)
    except Exception:  # noqa: BLE001 — 文件与数据库清理不能被失联浏览器阻塞
        logger.warning("关闭会话浏览器失败: %s", conv.id, exc_info=True)
    db.query(StewardMessage).filter(StewardMessage.conversation_id == conv.id).delete()
    db.delete(conv)
    db.commit()
    try:
        workspace.remove_session(conv.id)
    except OSError:
        logger.warning("会话目录清理失败，需后台重试: %s", conv.id, exc_info=True)


# ── 会话文件 ──────────────────────────────────────────────────────

@router.get("/conversations/{conversation_id}/files")
def list_conversation_files(conversation_id: str, db: Session = Depends(get_db),
                            current_user=Depends(get_current_user)):
    _require_conversation(db, conversation_id, current_user)
    return _ok(workspace.list_files(conversation_id))


@router.get("/conversations/{conversation_id}/files/{artifact_id}/preview")
def preview_conversation_file(conversation_id: str, artifact_id: str,
                              max_chars: int = Query(60_000, ge=1, le=100_000),
                              db: Session = Depends(get_db),
                              current_user=Depends(get_current_user)):
    """返回会话文件的安全文本预览；原始二进制仍由下载端点提供。"""
    _require_conversation(db, conversation_id, current_user)
    try:
        row, _ = workspace.require_file(conversation_id, artifact_id)
        content = workspace.extracted_text(conversation_id, artifact_id, max_chars)
    except workspace.WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _ok({
        "file": row,
        "content": content,
        "truncated": len(content) >= max_chars,
        "previewable": bool(content),
    })


@router.post("/conversations/{conversation_id}/files", status_code=201)
async def upload_conversation_file(conversation_id: str, file: UploadFile = File(...),
                                   db: Session = Depends(get_db),
                                   current_user=Depends(get_current_user)):
    _require_conversation(db, conversation_id, current_user)
    ext = os.path.splitext(file.filename or "")[1].lower().lstrip(".")
    allowed = {item.strip().lower() for item in settings.allowed_upload_extensions.split(",") if item.strip()}
    if ext not in allowed:
        raise HTTPException(400, f"不支持的文件类型 .{ext}（允许: {settings.allowed_upload_extensions}）")
    try:
        row = workspace.save_stream(
            conversation_id, file.filename or "attachment", file.file,
            source="upload", mime_type=file.content_type, extract=True,
        )
    except workspace.WorkspaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _ok(row)


@router.get("/conversations/{conversation_id}/files/{artifact_id}")
def download_conversation_file(conversation_id: str, artifact_id: str,
                               db: Session = Depends(get_db),
                               current_user=Depends(get_current_user)):
    _require_conversation(db, conversation_id, current_user)
    try:
        row, path = workspace.require_file(conversation_id, artifact_id)
    except workspace.WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, filename=row["filename"], media_type=row.get("mimeType"))


@router.delete("/conversations/{conversation_id}/files/{artifact_id}", status_code=204)
def delete_conversation_file(conversation_id: str, artifact_id: str,
                             db: Session = Depends(get_db),
                             current_user=Depends(get_current_user)):
    _require_conversation(db, conversation_id, current_user)
    try:
        workspace.delete_file(conversation_id, artifact_id)
    except workspace.WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/conversations/{conversation_id}/archive")
def archive_conversation_files(conversation_id: str, db: Session = Depends(get_db),
                               current_user=Depends(get_current_user)):
    _require_conversation(db, conversation_id, current_user)
    path = workspace.archive_path(conversation_id)
    return FileResponse(path, filename=f"data-steward-{conversation_id[:8]}.zip",
                        media_type="application/zip")


# ── 会话浏览器 ────────────────────────────────────────────────────

class BrowserUrlBody(BaseModel):
    url: str


class BrowserClickBody(BaseModel):
    text: str


class BrowserTypeBody(BaseModel):
    selector: str
    text: str
    pressEnter: bool = False


class CreateBrowserSourceBody(BaseModel):
    name: str
    sourceType: str
    endpointUrl: str | None = None
    headers: dict[str, str] | None = None


class UpdateBrowserSourceBody(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    endpointUrl: str | None = None
    headers: dict[str, str] | None = None


class BindBrowserSourceBody(BaseModel):
    sourceId: str | None = None


def _browser_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (BrowserRuntimeError, StewardError, workspace.WorkspaceError)):
        return HTTPException(422, str(exc))
    logger.exception("会话浏览器操作失败")
    return HTTPException(500, "会话浏览器操作失败")


@router.get("/browser/status")
def browser_status(_=Depends(get_current_user)):
    # CDP URLs may contain a browser-service token; never return them to clients.
    try:
        capacity = browser_manager.capacity_status()
    except Exception:  # 浏览器 sidecar 未就绪时也应能查看静态配额
        capacity = {
            "activeSessions": 0,
            "liveSessions": 0,
            "maxSessions": max(1, int(settings.steward_browser_max_sessions)),
            "maxSessionsPerUser": max(1, int(settings.steward_browser_max_sessions_per_user)),
            "idleTimeoutSeconds": max(30, int(settings.steward_browser_idle_timeout_seconds)),
        }
    return _ok({**probe_browser_cdp(), **capacity})


@router.get("/browser/sources")
def list_browser_sources(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    user_id = getattr(current_user, "id", None)
    rows = (db.query(StewardBrowserSource)
            .filter(StewardBrowserSource.user_id == user_id)
            .order_by(StewardBrowserSource.created_at.asc()).all())
    return _ok([browser_sources.managed_out(), *[browser_sources.source_out(row) for row in rows]])


@router.post("/browser/sources", status_code=201)
def create_browser_source(body: CreateBrowserSourceBody, db: Session = Depends(get_db),
                          current_user=Depends(get_current_user)):
    if body.sourceType == BROWSER_SOURCE_REMOTE_CDP and getattr(current_user, "role", "") != "admin":
        raise HTTPException(403, "远程 CDP 是服务器级高权限入口，只允许管理员配置；普通用户请使用本机浏览器助手")
    try:
        source, token = browser_sources.create_source(
            db, getattr(current_user, "id", None), name=body.name,
            source_type=body.sourceType, endpoint_url=body.endpointUrl, headers=body.headers)
    except StewardError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _ok({**browser_sources.source_out(source), "pairingToken": token})


@router.patch("/browser/sources/{source_id}")
def update_browser_source(source_id: str, body: UpdateBrowserSourceBody,
                          db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    try:
        source = browser_sources.require_source(
            db, source_id, getattr(current_user, "id", None),
            admin=getattr(current_user, "role", "") == "admin")
        if body.name is not None:
            source.name = body.name.strip()[:120] or source.name
        if body.enabled is not None:
            source.enabled = body.enabled
        if body.endpointUrl is not None:
            if source.source_type != BROWSER_SOURCE_REMOTE_CDP:
                raise StewardError("本机浏览器助手没有可编辑的远程地址")
            from app.shared.encryption import encrypt
            source.endpoint_url_encrypted = encrypt(browser_sources.validate_remote_endpoint(body.endpointUrl))
        if body.headers is not None:
            if source.source_type != BROWSER_SOURCE_REMOTE_CDP:
                raise StewardError("本机浏览器助手没有远程请求头")
            from app.shared.encryption import encrypt
            source.headers_encrypted = encrypt(json.dumps(
                browser_sources.normalize_headers(body.headers), ensure_ascii=False))
        db.commit()
        db.refresh(source)
        return _ok(browser_sources.source_out(source))
    except StewardError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/browser/sources/{source_id}/rotate-token")
def rotate_browser_source_token(source_id: str, db: Session = Depends(get_db),
                                current_user=Depends(get_current_user)):
    try:
        source = browser_sources.require_source(db, source_id, getattr(current_user, "id", None))
        return _ok({"sourceId": source.id, "pairingToken": browser_sources.rotate_companion_token(db, source)})
    except StewardError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/browser/sources/{source_id}", status_code=204)
def delete_browser_source(source_id: str, db: Session = Depends(get_db),
                          current_user=Depends(get_current_user)):
    try:
        source = browser_sources.require_source(db, source_id, getattr(current_user, "id", None))
    except StewardError as exc:
        raise HTTPException(404, str(exc)) from exc
    conversations = db.query(StewardConversation).filter(
        StewardConversation.browser_source_id == source.id).all()
    for conversation in conversations:
        try:
            browser_manager.close(conversation.id)
        except Exception:  # noqa: BLE001 — 删除来源不能被失联浏览器阻塞
            logger.warning("关闭已删除来源的浏览器会话失败: %s", conversation.id, exc_info=True)
        conversation.browser_source_id = None
    db.delete(source)
    db.commit()


@router.post("/browser/sources/{source_id}/test")
def test_browser_source(source_id: str, db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    try:
        target = browser_sources.resolve_target(
            db, None if source_id == browser_sources.MANAGED_SOURCE_ID else source_id,
            getattr(current_user, "id", None), admin=getattr(current_user, "role", "") == "admin")
        return _ok(browser_manager.test_target(target))
    except Exception as exc:  # noqa: BLE001
        raise _browser_error(exc)


@router.get("/browser/companion/script")
def download_browser_companion(_=Depends(get_current_user)):
    path = Path(__file__).with_name("companion_client.mjs")
    return FileResponse(path, filename="openontology-browser-companion.mjs",
                        media_type="text/javascript")


@router.put("/conversations/{conversation_id}/browser/source")
def bind_browser_source(conversation_id: str, body: BindBrowserSourceBody,
                        db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    conv = _require_conversation(db, conversation_id, current_user)
    source_id = body.sourceId
    if source_id and source_id != browser_sources.MANAGED_SOURCE_ID:
        try:
            browser_sources.require_source(db, source_id, getattr(current_user, "id", None))
        except StewardError as exc:
            raise HTTPException(422, str(exc)) from exc
    browser_manager.close(conversation_id)
    conv.browser_source_id = None if source_id in {None, browser_sources.MANAGED_SOURCE_ID} else source_id
    db.commit()
    return _ok(_conv_out(conv))


@router.post("/conversations/{conversation_id}/browser/start")
def start_browser(conversation_id: str, body: BrowserUrlBody,
                  db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    conv = _require_conversation(db, conversation_id, current_user)
    try:
        owner_id = conv.user_id or getattr(current_user, "id", None)
        target = browser_sources.resolve_target(
            db, conv.browser_source_id, owner_id,
            admin=getattr(current_user, "role", "") == "admin")
        return _ok(browser_manager.start(
            conversation_id, body.url, user_id=owner_id, actor="user", browser_target=target))
    except Exception as exc:  # noqa: BLE001
        raise _browser_error(exc)


@router.post("/conversations/{conversation_id}/browser/navigate")
def navigate_browser(conversation_id: str, body: BrowserUrlBody,
                     db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require_conversation(db, conversation_id, current_user)
    try:
        return _ok(browser_manager.navigate(conversation_id, body.url, actor="user"))
    except Exception as exc:  # noqa: BLE001
        raise _browser_error(exc)


@router.get("/conversations/{conversation_id}/browser/state")
def browser_state(conversation_id: str, db: Session = Depends(get_db),
                  current_user=Depends(get_current_user)):
    _require_conversation(db, conversation_id, current_user)
    try:
        return _ok(browser_manager.state(conversation_id, actor="user"))
    except Exception as exc:  # noqa: BLE001
        raise _browser_error(exc)


@router.get("/conversations/{conversation_id}/browser/session")
def browser_session(conversation_id: str, db: Session = Depends(get_db),
                    current_user=Depends(get_current_user)):
    """Tell the live-view UI whether the Agent already opened this browser."""
    _require_conversation(db, conversation_id, current_user)
    try:
        return _ok(browser_manager.session_info(conversation_id))
    except Exception as exc:  # noqa: BLE001
        raise _browser_error(exc)


@router.post("/conversations/{conversation_id}/browser/click")
def browser_click(conversation_id: str, body: BrowserClickBody,
                  db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require_conversation(db, conversation_id, current_user)
    try:
        return _ok(browser_manager.click_text(conversation_id, body.text, actor="user"))
    except Exception as exc:  # noqa: BLE001
        raise _browser_error(exc)


@router.post("/conversations/{conversation_id}/browser/type")
def browser_type(conversation_id: str, body: BrowserTypeBody,
                 db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require_conversation(db, conversation_id, current_user)
    try:
        return _ok(browser_manager.type_text(
            conversation_id, body.selector, body.text, body.pressEnter, actor="user"))
    except Exception as exc:  # noqa: BLE001
        raise _browser_error(exc)


@router.get("/conversations/{conversation_id}/browser/captures")
def browser_captures(conversation_id: str, keyword: str | None = None,
                     limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db),
                     current_user=Depends(get_current_user)):
    _require_conversation(db, conversation_id, current_user)
    return _ok(browser_manager.list_captures(conversation_id, keyword, limit))


@router.post("/conversations/{conversation_id}/browser/captures/{capture_id}/download")
def browser_capture_download(conversation_id: str, capture_id: str,
                             db: Session = Depends(get_db),
                             current_user=Depends(get_current_user)):
    _require_conversation(db, conversation_id, current_user)
    try:
        return _ok(browser_manager.download(conversation_id, capture_id, actor="user"))
    except Exception as exc:  # noqa: BLE001
        raise _browser_error(exc)


@router.post("/conversations/{conversation_id}/browser/ticket")
def browser_live_ticket(conversation_id: str, db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    _require_conversation(db, conversation_id, current_user)
    ticket = browser_manager.issue_ticket(conversation_id, getattr(current_user, "id", None))
    return _ok({"ticket": ticket, "expiresIn": 60})


# ── 受管流水线面板 ────────────────────────────────────────────────

@router.get("/pipelines")
def list_pipeline_records(include_archived: bool = Query(False),
                          db: Session = Depends(get_db), _=Depends(get_current_user)):
    """受管流水线面板：只列数据管家可编排的流水线 —— 未发布 且 未启用。

    发布即封版、编排移交编辑向导；已发布或 n8n 侧已激活的流水线超出管家职权
    （同 require_orchestrable 口径），到「数据流水线」列表管理，不在此面板出现。
    """
    q = db.query(N8nPipeline)
    if not include_archived:
        q = q.filter(N8nPipeline.status != STATUS_ARCHIVED)
    records = q.order_by(N8nPipeline.updated_at.desc()).limit(100).all()

    # 批量取 n8n 激活状态（尽力而为：n8n 不可达时不阻塞面板）
    active_map: dict[str, bool] = {}
    try:
        client = service.get_n8n_client(db)
        for wf in client.list_workflows(limit=200):
            active_map[str(wf.get("id"))] = bool(wf.get("active"))
    except Exception:  # noqa: BLE001
        pass

    # 只保留可编排的：未发布（影子流水线 status != published）且未启用（n8n active != True）。
    # active 未知（n8n 不可达）时按未启用放行，与 require_orchestrable 探测失败即放行同口径。
    out = [service.record_out(db, r, active=active_map.get(r.n8n_workflow_id))
           for r in records]
    out = [o for o in out
           if o["pipelineStatus"] != "published" and o.get("active") is not True]
    return _ok(out)


class BootstrapBody(BaseModel):
    name: str
    description: str = ""


@router.post("/pipelines/bootstrap", status_code=201)
def bootstrap_pipeline(body: BootstrapBody, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    """流水线列表「新建 n8n 流水线」：后台自动在 n8n 创建骨架工作流（草稿纳管）。

    与对话工具 create_pipeline 同源治理：创建即未发布、n8n 侧不激活；
    编排完善在数据管家对话完成，发布在流水线编辑向导完成。
    返回治理记录供前端深链跳转。
    """
    try:
        rec = service.bootstrap_blank_workflow(
            db, body.name, body.description,
            user_id=getattr(current_user, "id", None))
    except Exception as e:  # noqa: BLE001
        raise _handle(e)
    return _ok({"record": service.record_out(db, rec, active=False)})


@router.get("/pipelines/{record_id}")
def get_pipeline_record(record_id: str, db: Session = Depends(get_db),
                        _=Depends(get_current_user)):
    try:
        rec = service.require_record(db, record_id)
    except StewardError as e:
        raise HTTPException(404, str(e))

    out = service.record_out(db, rec)
    out["workflow"] = rec.workflow_snapshot
    try:
        client = service.get_n8n_client(db)
        workflow = client.get_workflow(rec.n8n_workflow_id)
        live_snapshot, changed = service.refresh_draft_snapshot(db, rec, workflow)
        if changed:
            db.commit()
        out["workflow"] = live_snapshot
        out["active"] = bool(workflow.get("active"))
        out["summary"] = service.summarize_workflow(rec.workflow_snapshot)
    except Exception as e:  # noqa: BLE001 — n8n 不可达时返回快照视图
        out["n8nError"] = str(e)[:300]
    return _ok(out)
