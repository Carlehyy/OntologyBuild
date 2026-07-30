"""Attachment lookup, archive creation, and ownership workflows."""
from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass
from typing import Callable

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.events import service
from app.events.deps import IngestContext
from app.events.models import EventAttachment
from app.events.query_service import require_event


@dataclass(frozen=True)
class AttachmentArchive:
    path: str
    filename: str


def remove_temporary_archive(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def archive_name(filename: str, used_names: set[str]) -> str:
    """Return a traversal-safe, case-insensitively unique ZIP member name."""
    normalized = (filename or "").replace("\\", "/")
    base = os.path.basename(normalized).strip().strip(".") or "attachment"
    stem, suffix = os.path.splitext(base)
    candidate = base
    index = 2
    while candidate.casefold() in used_names:
        candidate = f"{stem or 'attachment'} ({index}){suffix}"
        index += 1
    used_names.add(candidate.casefold())
    return candidate


def build_archive(
    db: Session,
    event_id: str,
    *,
    named_temporary_file: Callable,
) -> AttachmentArchive:
    event = require_event(db, event_id)
    attachments = (
        db.query(EventAttachment)
        .filter(EventAttachment.event_id == event_id)
        .order_by(
            EventAttachment.created_at.asc(),
            EventAttachment.id.asc(),
        )
        .all()
    )
    if not attachments:
        raise HTTPException(404, "当前事件没有附件")

    missing = [
        attachment.filename
        for attachment in attachments
        if not os.path.isfile(attachment.file_path)
    ]
    if missing:
        raise HTTPException(410, f"附件文件已丢失: {missing[0]}")

    temporary = named_temporary_file(
        prefix="event-attachments-",
        suffix=".zip",
        delete=False,
    )
    archive_path = temporary.name
    temporary.close()
    try:
        used_names: set[str] = set()
        with zipfile.ZipFile(
            archive_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as archive:
            for attachment in attachments:
                archive.write(
                    attachment.file_path,
                    arcname=archive_name(attachment.filename, used_names),
                )
    except FileNotFoundError as exc:
        remove_temporary_archive(archive_path)
        raise HTTPException(
            410,
            "打包过程中有附件被删除，请刷新后重试",
        ) from exc
    except Exception:
        remove_temporary_archive(archive_path)
        raise

    safe_event_no = "".join(
        character
        for character in event.event_no
        if character not in '\\/:*?"<>|' and ord(character) >= 32
    ).strip() or "event"
    return AttachmentArchive(
        path=archive_path,
        filename=f"{safe_event_no}-附件.zip",
    )


def attachment_for_download(
    db: Session,
    event_id: str,
    attachment_id: str,
) -> EventAttachment:
    attachment = (
        db.query(EventAttachment)
        .filter(
            EventAttachment.id == attachment_id,
            EventAttachment.event_id == event_id,
        )
        .first()
    )
    if not attachment:
        raise HTTPException(404, "附件不存在")
    if not os.path.exists(attachment.file_path):
        raise HTTPException(410, "附件文件已丢失")
    return attachment


def remove_attachment(
    db: Session,
    event_id: str,
    attachment_id: str,
    user,
) -> dict:
    event = require_event(db, event_id)
    attachment = (
        db.query(EventAttachment)
        .filter(
            EventAttachment.id == attachment_id,
            EventAttachment.event_id == event_id,
        )
        .first()
    )
    if not attachment:
        raise HTTPException(404, "附件不存在")
    service.remove_attachment(db, event, attachment, user)
    return {"status": "deleted", "id": attachment_id}


async def add_ingest_attachment(
    db: Session,
    event_id: str,
    upload: UploadFile,
    context: IngestContext,
) -> dict:
    event = require_event(db, event_id)
    if event.ingest_key_id != context.key.id:
        raise HTTPException(403, "无权给该事件添加附件")
    actor = type(
        "KeyActor",
        (),
        {
            "id": context.key.id,
            "username": context.key.name,
            "_actor_type": "service",
        },
    )()
    attachment = await service.add_attachment(
        db,
        event,
        upload=upload,
        user=actor,
    )
    return service.attachment_out(attachment)
