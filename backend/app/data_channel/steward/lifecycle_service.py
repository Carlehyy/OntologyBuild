"""Write-side conversation, workspace, and pipeline lifecycle services."""
from __future__ import annotations

import logging
import os

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.data_channel.steward import service, workspace
from app.data_channel.steward.browser_runtime import browser_manager
from app.data_channel.steward.contracts import (
    BootstrapBody,
    CreateConversationBody,
)
from app.data_channel.steward.models import (
    StewardConversation,
    StewardMessage,
)
from app.data_channel.steward.query_service import (
    _conv_out,
    _ok,
    _require_conversation,
)
from app.data_channel.steward.service import StewardError
from app.settings.workflows.n8n_client import N8nApiError


logger = logging.getLogger(__name__)


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, StewardError):
        return HTTPException(400, str(exc))
    if isinstance(exc, N8nApiError):
        return HTTPException(
            502,
            f"n8n API 错误 (HTTP {exc.status_code}): {exc.message}",
        )
    logger.exception("数据管家操作失败")
    return HTTPException(500, f"操作失败: {exc}")


def create_conversation(
    body: CreateConversationBody,
    db: Session,
    current_user,
    *,
    conv_out_fn=_conv_out,
):
    conversation = StewardConversation(
        user_id=getattr(current_user, "id", None),
        title=(body.title or "新对话").strip()[:200] or "新对话",
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    workspace.session_root(conversation.id)
    return _ok(conv_out_fn(conversation))


def delete_conversation(
    conversation_id: str,
    db: Session,
    current_user,
    *,
    require_conversation_fn=_require_conversation,
):
    conversation = require_conversation_fn(
        db, conversation_id, current_user
    )
    try:
        browser_manager.close(conversation.id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "关闭会话浏览器失败: %s",
            conversation.id,
            exc_info=True,
        )
    db.query(StewardMessage).filter(
        StewardMessage.conversation_id == conversation.id
    ).delete()
    db.delete(conversation)
    db.commit()
    try:
        workspace.remove_session(conversation.id)
    except OSError:
        logger.warning(
            "会话目录清理失败，需后台重试: %s",
            conversation.id,
            exc_info=True,
        )


def upload_conversation_file(
    conversation_id: str,
    file: UploadFile,
    db: Session,
    current_user,
    *,
    require_conversation_fn=_require_conversation,
):
    require_conversation_fn(db, conversation_id, current_user)
    extension = os.path.splitext(file.filename or "")[1].lower().lstrip(".")
    allowed = {
        item.strip().lower()
        for item in settings.allowed_upload_extensions.split(",")
        if item.strip()
    }
    if extension not in allowed:
        raise HTTPException(
            400,
            (
                f"不支持的文件类型 .{extension}"
                f"（允许: {settings.allowed_upload_extensions}）"
            ),
        )
    try:
        row = workspace.save_stream(
            conversation_id,
            file.filename or "attachment",
            file.file,
            source="upload",
            mime_type=file.content_type,
            extract=True,
        )
    except workspace.WorkspaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _ok(row)


def delete_conversation_file(
    conversation_id: str,
    artifact_id: str,
    db: Session,
    current_user,
    *,
    require_conversation_fn=_require_conversation,
):
    require_conversation_fn(db, conversation_id, current_user)
    try:
        workspace.delete_file(conversation_id, artifact_id)
    except workspace.WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc


def bootstrap_pipeline(
    body: BootstrapBody,
    db: Session,
    current_user,
    *,
    service_module=service,
    handle_fn=_handle,
):
    try:
        record = service_module.bootstrap_blank_workflow(
            db,
            body.name,
            body.description,
            user_id=getattr(current_user, "id", None),
        )
    except Exception as exc:  # noqa: BLE001
        raise handle_fn(exc)
    return _ok({
        "record": service_module.record_out(db, record, active=False)
    })
