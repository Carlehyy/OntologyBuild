"""Read-side application services for Data Steward HTTP endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.data_channel.steward import browser_sources, service, workspace
from app.data_channel.steward.models import (
    N8nPipeline,
    StewardConversation,
    StewardMessage,
    STATUS_ARCHIVED,
)
from app.data_channel.steward.service import StewardError
from app.model_configs.selector import select_llm_model_config


def _ok(data):
    return {"data": data}


def _conv_out(conversation: StewardConversation) -> dict:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "browserSourceId": (
            conversation.browser_source_id or browser_sources.MANAGED_SOURCE_ID
        ),
        "createdAt": (
            conversation.created_at.isoformat()
            if conversation.created_at
            else None
        ),
        "updatedAt": (
            conversation.updated_at.isoformat()
            if conversation.updated_at
            else None
        ),
    }


def _msg_out(message: StewardMessage) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content or "",
        "steps": message.steps or [],
        "touchedPipelineIds": message.touched_pipeline_ids or [],
        "model": message.model,
        "tokenUsage": message.token_usage,
        "createdAt": (
            message.created_at.isoformat() if message.created_at else None
        ),
    }


def _require_conversation(
    db: Session,
    conversation_id: str,
    current_user,
) -> StewardConversation:
    conversation = db.query(StewardConversation).filter(
        StewardConversation.id == conversation_id
    ).first()
    if not conversation:
        raise HTTPException(404, "会话不存在")
    if (
        conversation.user_id
        and conversation.user_id != getattr(current_user, "id", None)
        and getattr(current_user, "role", "") != "admin"
    ):
        raise HTTPException(403, "无权访问他人会话")
    return conversation


def steward_status(
    db: Session,
    *,
    service_module=service,
    select_llm_model_config_fn=select_llm_model_config,
):
    n8n = service_module.n8n_config_status(db)
    if n8n["configured"] and n8n["enabled"]:
        try:
            service_module.get_n8n_client(db).test_connection()
            n8n["reachable"] = True
        except Exception as exc:  # noqa: BLE001
            n8n["reachable"] = False
            n8n["error"] = str(exc)[:300]
    llm_ready = select_llm_model_config_fn(db) is not None

    counts: dict[str, int] = {}
    rows = db.query(N8nPipeline).filter(
        N8nPipeline.status != STATUS_ARCHIVED
    ).all()
    for record in rows:
        publication_status = service_module.shadow_status(db, record)
        counts[publication_status] = counts.get(publication_status, 0) + 1
    return _ok({
        "n8n": n8n,
        # Only expose whether the Python execution capability is configured;
        # never return the gateway URL or authentication token to the browser.
        "python": {
            "configured": bool(
                (settings.python_kernel_gateway_url or "").strip()
            ),
        },
        "llmReady": llm_ready,
        "pipelineCounts": counts,
    })


def list_conversations(
    db: Session,
    current_user,
    *,
    conv_out_fn: Callable[[StewardConversation], dict] = _conv_out,
):
    rows = (
        db.query(StewardConversation)
        .filter(
            StewardConversation.user_id
            == getattr(current_user, "id", None)
        )
        .order_by(StewardConversation.updated_at.desc())
        .limit(50)
        .all()
    )
    return _ok([conv_out_fn(conversation) for conversation in rows])


def get_conversation(
    conversation_id: str,
    db: Session,
    current_user,
    *,
    require_conversation_fn=_require_conversation,
    conv_out_fn: Callable[[StewardConversation], dict] = _conv_out,
    msg_out_fn: Callable[[StewardMessage], dict] = _msg_out,
):
    conversation = require_conversation_fn(
        db, conversation_id, current_user
    )
    messages = (
        db.query(StewardMessage)
        .filter(StewardMessage.conversation_id == conversation.id)
        .order_by(StewardMessage.created_at.asc())
        .limit(200)
        .all()
    )
    return _ok({
        **conv_out_fn(conversation),
        "messages": [msg_out_fn(message) for message in messages],
    })


def export_conversation(
    conversation_id: str,
    db: Session,
    current_user,
    *,
    require_conversation_fn=_require_conversation,
    conv_out_fn: Callable[[StewardConversation], dict] = _conv_out,
    msg_out_fn: Callable[[StewardMessage], dict] = _msg_out,
):
    conversation = require_conversation_fn(
        db, conversation_id, current_user
    )
    messages = (
        db.query(StewardMessage)
        .filter(StewardMessage.conversation_id == conversation.id)
        .order_by(
            StewardMessage.created_at.asc(),
            StewardMessage.id.asc(),
        )
        .all()
    )
    return _ok({
        "format": "openontology.data-steward.conversation",
        "version": 1,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "conversation": {
            **conv_out_fn(conversation),
            "messageCount": len(messages),
            "messages": [msg_out_fn(message) for message in messages],
        },
    })


def list_conversation_files(
    conversation_id: str,
    db: Session,
    current_user,
    *,
    require_conversation_fn=_require_conversation,
):
    require_conversation_fn(db, conversation_id, current_user)
    return _ok(workspace.list_files(conversation_id))


def preview_conversation_file(
    conversation_id: str,
    artifact_id: str,
    max_chars: int,
    db: Session,
    current_user,
    *,
    require_conversation_fn=_require_conversation,
):
    require_conversation_fn(db, conversation_id, current_user)
    try:
        row, _ = workspace.require_file(conversation_id, artifact_id)
        content = workspace.extracted_text(
            conversation_id, artifact_id, max_chars
        )
    except workspace.WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _ok({
        "file": row,
        "content": content,
        "truncated": len(content) >= max_chars,
        "previewable": bool(content),
    })


def download_conversation_file(
    conversation_id: str,
    artifact_id: str,
    db: Session,
    current_user,
    *,
    require_conversation_fn=_require_conversation,
):
    require_conversation_fn(db, conversation_id, current_user)
    try:
        row, path = workspace.require_file(conversation_id, artifact_id)
    except workspace.WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(
        path,
        filename=row["filename"],
        media_type=row.get("mimeType"),
    )


def archive_conversation_files(
    conversation_id: str,
    db: Session,
    current_user,
    *,
    require_conversation_fn=_require_conversation,
):
    require_conversation_fn(db, conversation_id, current_user)
    path = workspace.archive_path(conversation_id)
    return FileResponse(
        path,
        filename=f"data-steward-{conversation_id[:8]}.zip",
        media_type="application/zip",
    )


def list_pipeline_records(
    include_archived: bool,
    db: Session,
    *,
    service_module=service,
):
    query = db.query(N8nPipeline)
    if not include_archived:
        query = query.filter(N8nPipeline.status != STATUS_ARCHIVED)
    records = (
        query.order_by(N8nPipeline.updated_at.desc()).limit(100).all()
    )

    active_map: dict[str, bool] = {}
    try:
        client = service_module.get_n8n_client(db)
        for workflow in client.list_workflows(limit=200):
            active_map[str(workflow.get("id"))] = bool(
                workflow.get("active")
            )
    except Exception:  # noqa: BLE001
        pass

    out = [
        service_module.record_out(
            db,
            record,
            active=active_map.get(record.n8n_workflow_id),
        )
        for record in records
    ]
    out = [
        item
        for item in out
        if (
            item["pipelineStatus"] != "published"
            and item.get("active") is not True
        )
    ]
    return _ok(out)


def get_pipeline_record(
    record_id: str,
    db: Session,
    *,
    service_module=service,
):
    try:
        record = service_module.require_record(db, record_id)
    except StewardError as exc:
        raise HTTPException(404, str(exc)) from exc

    out = service_module.record_out(db, record)
    out["workflow"] = record.workflow_snapshot
    try:
        client = service_module.get_n8n_client(db)
        workflow = client.get_workflow(record.n8n_workflow_id)
        live_snapshot, changed = service_module.refresh_draft_snapshot(
            db, record, workflow
        )
        if changed:
            db.commit()
        out["workflow"] = live_snapshot
        out["active"] = bool(workflow.get("active"))
        out["summary"] = service_module.summarize_workflow(
            record.workflow_snapshot
        )
    except Exception as exc:  # noqa: BLE001
        out["n8nError"] = str(exc)[:300]
    return _ok(out)
