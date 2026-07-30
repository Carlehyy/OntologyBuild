"""Attachment and session-workspace application services for Exploration."""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.exploration import schemas as S
from app.exploration import workspace as W
from app.exploration.models import ExplorationAttachment
from app.exploration.session_service import _ok, _require_session


logger = logging.getLogger(__name__)


def _remove_attachment_file(
    path: str | None,
    *,
    os_module=os,
    logger_obj=logger,
) -> None:
    if path and os_module.path.exists(path):
        try:
            os_module.remove(path)
        except OSError:
            logger_obj.warning("附件文件清理失败: %s", path)


def _attachment_out(
    attachment: ExplorationAttachment,
    *,
    schemas_module=S,
) -> dict:
    # 迁移前记录没有逻辑路径；对外始终给出可展示路径。
    if not attachment.relative_path:
        attachment.relative_path = attachment.filename
    return schemas_module.AttachmentOut.model_validate(
        attachment
    ).model_dump(by_alias=True)


def list_attachments(
    session_id: str,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    attachment_model=ExplorationAttachment,
    attachment_out_fn: Callable[[ExplorationAttachment], dict] = (
        _attachment_out
    ),
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = require_session_fn(db, session_id, current_user)
    rows = (
        db.query(attachment_model)
        .filter(attachment_model.session_id == session.id)
        .order_by(attachment_model.created_at.asc())
        .all()
    )
    return ok_fn([
        attachment_out_fn(attachment)
        for attachment in rows
    ])


async def upload_attachment(
    session_id: str,
    file,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    settings_obj=settings,
    workspace_module=W,
    attachment_out_fn: Callable[[ExplorationAttachment], dict] = (
        _attachment_out
    ),
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = require_session_fn(db, session_id, current_user)
    extension = (file.filename or "").rsplit(".", 1)[-1].lower()
    allowed = {
        item.strip()
        for item in settings_obj.allowed_upload_extensions.split(",")
        if item.strip()
    }
    if extension not in allowed:
        raise HTTPException(
            400,
            (
                f"不支持的文件类型: .{extension}"
                f"（允许: {settings_obj.allowed_upload_extensions}）"
            ),
        )

    content = await file.read()
    row = workspace_module.create_bytes(
        db,
        session,
        file.filename or "attachment",
        content,
        file.content_type,
        source="upload",
    )
    return ok_fn(attachment_out_fn(row))


def create_workspace_text_file(
    session_id: str,
    body,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    workspace_module=W,
    attachment_out_fn: Callable[[ExplorationAttachment], dict] = (
        _attachment_out
    ),
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = require_session_fn(db, session_id, current_user)
    row = workspace_module.create_text(
        db,
        session,
        body.path,
        body.content,
        body.mime_type,
        source="user",
    )
    return ok_fn(attachment_out_fn(row))


def get_workspace_text_file(
    session_id: str,
    attachment_id: str,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    workspace_module=W,
    schemas_module=S,
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = require_session_fn(db, session_id, current_user)
    row = workspace_module.require_file(db, session.id, attachment_id)
    return ok_fn(schemas_module.WorkspaceTextOut(
        id=row.id,
        relative_path=row.relative_path or row.filename,
        content=workspace_module.read_text(row),
        version=row.version or 1,
        sha256=row.sha256,
    ).model_dump(by_alias=True))


def preview_workspace_file(
    session_id: str,
    attachment_id: str,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    workspace_module=W,
    schemas_module=S,
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = require_session_fn(db, session_id, current_user)
    row = workspace_module.require_file(db, session.id, attachment_id)
    content = (
        workspace_module.read_text(row)
        if row.editable
        else (row.extracted_text or "")
    )
    return ok_fn(schemas_module.WorkspacePreviewOut(
        id=row.id,
        relative_path=row.relative_path or row.filename,
        content=content,
        version=row.version or 1,
        mime_type=row.mime_type,
        editable=bool(row.editable),
        truncated=(row.char_count or 0) > len(content),
    ).model_dump(by_alias=True))


def update_workspace_text_file(
    session_id: str,
    attachment_id: str,
    body,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    workspace_module=W,
    attachment_out_fn: Callable[[ExplorationAttachment], dict] = (
        _attachment_out
    ),
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = require_session_fn(db, session_id, current_user)
    row = workspace_module.require_file(db, session.id, attachment_id)
    row = workspace_module.update_text(
        db,
        row,
        body.content,
        body.expected_version,
        source="user",
    )
    return ok_fn(attachment_out_fn(row))


def download_workspace_file(
    session_id: str,
    attachment_id: str,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    workspace_module=W,
    os_module=os,
    file_response_cls=FileResponse,
):
    session = require_session_fn(db, session_id, current_user)
    row = workspace_module.require_file(db, session.id, attachment_id)
    if not row.file_path or not os_module.path.isfile(row.file_path):
        raise HTTPException(410, "文件内容已丢失")
    return file_response_cls(
        row.file_path,
        media_type=row.mime_type or "application/octet-stream",
        filename=row.filename or "download",
    )


def delete_attachment(
    session_id: str,
    attachment_id: str,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    workspace_module=W,
):
    session = require_session_fn(db, session_id, current_user)
    row = workspace_module.require_file(db, session.id, attachment_id)
    workspace_module.delete_file(db, row)
