"""超级助手会话附件的 HTTP service 层：属权校验后委派会话工作区。"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth.models import User
from app.data_channel.steward import workspace
from app.shared.config import settings
from app.super_assistant import conversation_service, files_workspace


def _owned_conversation(db: Session, current_user: User, conversation_id: str) -> None:
    conversation_service._conversation(db, current_user.id, conversation_id)


def list_files(db: Session, current_user: User, conversation_id: str) -> list[dict]:
    _owned_conversation(db, current_user, conversation_id)
    return files_workspace.session_workspace().list_files(conversation_id)


def upload_file(
    db: Session,
    current_user: User,
    conversation_id: str,
    upload: UploadFile,
) -> dict:
    _owned_conversation(db, current_user, conversation_id)
    extension = os.path.splitext(upload.filename or "")[1].lower().lstrip(".")
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
        return files_workspace.session_workspace().save_stream(
            conversation_id,
            upload.filename or "attachment",
            upload.file,
            source="upload",
            mime_type=upload.content_type,
        )
    except workspace.WorkspaceError as exc:
        raise HTTPException(422, str(exc)) from exc


def download_file(
    db: Session,
    current_user: User,
    conversation_id: str,
    artifact_id: str,
) -> tuple[dict, Path]:
    _owned_conversation(db, current_user, conversation_id)
    try:
        return files_workspace.session_workspace().require_file(conversation_id, artifact_id)
    except workspace.WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc


def preview_file(
    db: Session,
    current_user: User,
    conversation_id: str,
    artifact_id: str,
    max_chars: int,
) -> dict:
    _owned_conversation(db, current_user, conversation_id)
    session = files_workspace.session_workspace()
    try:
        row, _ = session.require_file(conversation_id, artifact_id)
        content = session.extracted_text(conversation_id, artifact_id, max_chars)
    except workspace.WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "file": row,
        "content": content,
        "truncated": len(content) >= max_chars,
        "previewable": bool(content),
    }


def delete_file(
    db: Session,
    current_user: User,
    conversation_id: str,
    artifact_id: str,
) -> None:
    _owned_conversation(db, current_user, conversation_id)
    try:
        files_workspace.session_workspace().delete_file(conversation_id, artifact_id)
    except workspace.WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
