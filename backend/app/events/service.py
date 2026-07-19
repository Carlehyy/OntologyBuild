"""
事件登记 — service 层（全部状态迁移与审计写入集中于此）

审计契约（照 formal_modeling/facts.py）：record_audit 只 db.add + db.flush，
绝不内部 commit —— 由调用方事务统一提交，保证「事件变更 + 审计行」原子落库。
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import aiofiles
from fastapi import HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.events import models as m
from app.events.models import (
    RegisteredEvent, EventAttachment, EventAuditLog, EventIngestKey,
)
from app.events.schemas import EventCreate, EventUpdate, IngestEvent


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── 审计（追加式，flush-not-commit）───────────────────────────────

def record_audit(
    db: Session, *, event: RegisteredEvent, action: str,
    actor_type: str = "user", actor_id: Optional[str] = None,
    actor_name: Optional[str] = None, changes: Optional[dict] = None,
    note: Optional[str] = None, ip: Optional[str] = None,
) -> EventAuditLog:
    """追加一条审计。seq 在该事件内单调递增。不 commit，交由调用方事务提交。"""
    max_seq = (db.query(func.max(EventAuditLog.seq))
               .filter(EventAuditLog.event_id == event.id).scalar()) or 0
    row = EventAuditLog(
        event_id=event.id, seq=max_seq + 1, action=action,
        actor_type=actor_type, actor_id=actor_id, actor_name=actor_name,
        changes=changes, note=note, ip=ip,
    )
    db.add(row)
    db.flush()
    return row


# ── 事件编号（人读，防撞）─────────────────────────────────────────

def generate_event_no(db: Session) -> str:
    day = _now().strftime("%Y%m%d")
    for _ in range(12):
        code = f"EVT-{day}-{secrets.token_hex(3)}"  # 6 hex
        exists = db.query(RegisteredEvent.id).filter(RegisteredEvent.event_no == code).first()
        if not exists:
            return code
    return f"EVT-{day}-{uuid.uuid4().hex[:8]}"  # 极端兜底


# ── 平台录入 / 编辑 / 归档 ───────────────────────────────────────

def create_event(db: Session, data: EventCreate, user) -> RegisteredEvent:
    if not (data.title or "").strip():
        raise HTTPException(422, "title 不能为空")
    ev = RegisteredEvent(
        event_no=generate_event_no(db),
        title=data.title.strip(),
        description=data.description or "",
        event_type=(data.event_type or "").strip(),
        severity=_norm_severity(data.severity),
        tags=data.tags or [],
        payload=data.payload or {},
        occurred_at=data.occurred_at,
        recorded_at=_now(),
        source_type=m.SOURCE_PLATFORM,
        reporter_type="user",
        reporter_id=getattr(user, "id", None),
        reporter_name=getattr(user, "username", None),
        confidence=data.confidence,
        ontology_id=data.ontology_id,
        subject_ref=data.subject_ref,
        supersedes_id=data.supersedes_id,
        status=m.STATUS_ACTIVE,
    )
    db.add(ev)
    db.flush()  # 拿到 id 供审计 FK
    record_audit(db, event=ev, action=m.ACTION_CREATED,
                 actor_type="user", actor_id=getattr(user, "id", None),
                 actor_name=getattr(user, "username", None),
                 note="平台录入")
    db.commit()
    db.refresh(ev)
    return ev


_EDITABLE = ("title", "description", "event_type", "severity", "tags",
             "payload", "occurred_at", "ontology_id", "subject_ref", "confidence")


def update_event(db: Session, ev: RegisteredEvent, data: EventUpdate, user) -> RegisteredEvent:
    patch = data.model_dump(exclude_unset=True)
    changes: dict[str, dict] = {}
    for field in _EDITABLE:
        if field not in patch:
            continue
        new_val = patch[field]
        if field == "severity" and new_val is not None:
            new_val = _norm_severity(new_val)
        old_val = getattr(ev, field)
        if old_val != new_val:
            changes[field] = {"from": _jsonable(old_val), "to": _jsonable(new_val)}
            setattr(ev, field, new_val)
    if not changes:
        return ev  # 无变化不写审计
    ev.updated_at = _now()
    record_audit(db, event=ev, action=m.ACTION_UPDATED,
                 actor_type="user", actor_id=getattr(user, "id", None),
                 actor_name=getattr(user, "username", None), changes=changes)
    db.commit()
    db.refresh(ev)
    return ev


def change_status(db: Session, ev: RegisteredEvent, status: str,
                  note: Optional[str], user) -> RegisteredEvent:
    status = (status or "").strip().lower()
    if status not in (m.STATUS_ACTIVE, m.STATUS_ARCHIVED):
        raise HTTPException(422, f"非法状态: {status}")
    if ev.status == status:
        return ev
    old = ev.status
    ev.status = status
    ev.updated_at = _now()
    record_audit(db, event=ev, action=m.ACTION_STATUS_CHANGED,
                 actor_type="user", actor_id=getattr(user, "id", None),
                 actor_name=getattr(user, "username", None),
                 changes={"status": {"from": old, "to": status}}, note=note)
    db.commit()
    db.refresh(ev)
    return ev


def hard_delete_event(db: Session, ev: RegisteredEvent) -> None:
    """物理删除（仅 admin）。连带清盘附件。审计随 CASCADE 一并删除。"""
    for att in db.query(EventAttachment).filter(EventAttachment.event_id == ev.id).all():
        _remove_file(att.file_path)
    db.query(EventAttachment).filter(EventAttachment.event_id == ev.id).delete()
    db.query(EventAuditLog).filter(EventAuditLog.event_id == ev.id).delete()
    db.delete(ev)
    db.commit()


# ── 附件 ────────────────────────────────────────────────────────

async def add_attachment(db: Session, ev: RegisteredEvent, *, upload: UploadFile,
                         user) -> EventAttachment:
    filename = upload.filename or ""
    mime = upload.content_type
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    allowed = {e.strip().lower() for e in settings.allowed_upload_extensions.split(",") if e.strip()}
    if ext and allowed and ext not in allowed:
        raise HTTPException(400, f"不支持的文件类型: .{ext}（允许: {settings.allowed_upload_extensions}）")

    att_id = str(uuid.uuid4())
    upload_dir = os.path.join(settings.uploads_dir, "events", ev.id)
    os.makedirs(upload_dir, exist_ok=True)
    ext_suffix = os.path.splitext(filename or "")[1]
    save_path = os.path.join(upload_dir, f"{att_id}{ext_suffix}")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    file_size = 0
    digest = hashlib.sha256()
    try:
        async with aiofiles.open(save_path, "wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                file_size += len(chunk)
                if file_size > max_bytes:
                    raise HTTPException(413, f"文件超过大小限制 {settings.max_upload_mb}MB")
                digest.update(chunk)
                await destination.write(chunk)
    except Exception:
        _remove_file(save_path)
        raise

    att = EventAttachment(
        id=att_id, event_id=ev.id, filename=filename or att_id,
        file_path=save_path, file_size=file_size, mime_type=mime,
        sha256=digest.hexdigest(),
        uploaded_by=getattr(user, "id", None),
    )
    try:
        db.add(att)
        record_audit(db, event=ev, action=m.ACTION_ATTACHMENT_ADDED,
                     actor_type=getattr(user, "_actor_type", "user"),
                     actor_id=getattr(user, "id", None),
                     actor_name=getattr(user, "username", None),
                     note=f"添加附件 {filename}")
        db.commit()
        db.refresh(att)
    except Exception:
        db.rollback()
        _remove_file(save_path)
        raise
    return att


def remove_attachment(db: Session, ev: RegisteredEvent, att: EventAttachment, user) -> None:
    fname = att.filename
    _remove_file(att.file_path)
    db.delete(att)
    record_audit(db, event=ev, action=m.ACTION_ATTACHMENT_REMOVED,
                 actor_type="user", actor_id=getattr(user, "id", None),
                 actor_name=getattr(user, "username", None),
                 note=f"删除附件 {fname}")
    db.commit()


def _remove_file(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ── 第三方接口上传（幂等）─────────────────────────────────────────

def ingest_event(db: Session, item: IngestEvent, key: EventIngestKey,
                 client_ip: Optional[str]) -> tuple[RegisteredEvent, bool]:
    """返回 (event, idempotent)。idempotent=True 表示命中既有事件、未新建。"""
    if not (item.title or "").strip():
        raise HTTPException(422, "title 不能为空")

    source_system = (item.source_system or key.name or "").strip() or None
    # 作用域约束：密钥限定了 source_system 时，上传声明必须一致
    if key.allowed_source_system:
        if source_system and source_system != key.allowed_source_system:
            raise HTTPException(403, f"该密钥仅允许来源 source_system={key.allowed_source_system}")
        source_system = key.allowed_source_system

    source_ref = (item.source_ref or "").strip() or None

    # 幂等：先查再插（并发重传下比 IntegrityError 兜底更稳、更友好）
    if source_system and source_ref:
        existing = (db.query(RegisteredEvent)
                    .filter(RegisteredEvent.source_system == source_system,
                            RegisteredEvent.source_ref == source_ref)
                    .first())
        if existing:
            record_audit(db, event=existing, action=m.ACTION_INGEST_DUPLICATE,
                         actor_type="service", actor_id=key.id, actor_name=key.name,
                         ip=client_ip, note=f"重复投递 source_ref={source_ref}")
            db.commit()
            return existing, True

    ev = RegisteredEvent(
        event_no=generate_event_no(db),
        title=item.title.strip(),
        description=item.description or "",
        event_type=(item.event_type or "").strip(),
        severity=_norm_severity(item.severity),
        tags=item.tags or [],
        payload=item.payload or {},
        occurred_at=item.occurred_at,
        recorded_at=_now(),
        source_type=m.SOURCE_API,
        source_system=source_system,
        source_ref=source_ref,
        reporter_type="service",
        reporter_id=key.id,
        reporter_name=key.name,
        ingest_key_id=key.id,
        client_ip=client_ip,
        confidence=item.confidence,
        ontology_id=item.ontology_id,
        subject_ref=item.subject_ref,
        status=m.STATUS_ACTIVE,
    )
    db.add(ev)
    db.flush()
    record_audit(db, event=ev, action=m.ACTION_INGESTED,
                 actor_type="service", actor_id=key.id, actor_name=key.name,
                 ip=client_ip,
                 note=f"第三方上传 · {source_system or '未标注来源'}"
                      + (f" · {source_ref}" if source_ref else ""))
    db.commit()
    db.refresh(ev)
    return ev, False


# ── 密钥 ────────────────────────────────────────────────────────

def hash_key(plaintext: str) -> str:
    return hashlib.sha256((plaintext or "").encode("utf-8")).hexdigest()


def mint_ingest_key(db: Session, name: str, allowed_source_system: Optional[str],
                    user) -> tuple[EventIngestKey, str]:
    """生成密钥。返回 (记录, 明文全串)。明文只在此刻可见，之后仅存 sha256。"""
    if not (name or "").strip():
        raise HTTPException(422, "name 不能为空")
    tag = secrets.token_hex(3)                 # 6 char 可见前缀标识
    secret = secrets.token_urlsafe(32)
    key_prefix = f"ob_ingest_{tag}"
    plaintext = f"{key_prefix}_{secret}"
    row = EventIngestKey(
        name=name.strip(),
        key_prefix=key_prefix,
        key_hash=hash_key(plaintext),
        secret_plain=plaintext,  # 留存以便反复复制
        enabled=True,
        allowed_source_system=(allowed_source_system or "").strip() or None,
        created_by=getattr(user, "id", None),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, plaintext


def verify_ingest_key(db: Session, plaintext: str) -> Optional[EventIngestKey]:
    if not plaintext:
        return None
    row = (db.query(EventIngestKey)
           .filter(EventIngestKey.key_hash == hash_key(plaintext),
                   EventIngestKey.enabled.is_(True),
                   EventIngestKey.revoked_at.is_(None))
           .first())
    return row


def revoke_ingest_key(db: Session, row: EventIngestKey) -> None:
    row.enabled = False
    row.revoked_at = _now()
    db.commit()


# ── 序列化助手（对外 camelCase）───────────────────────────────────

_SOURCE_LABELS = {m.SOURCE_PLATFORM: "平台录入", m.SOURCE_SYSTEM: "系统"}


def source_label(ev: RegisteredEvent) -> str:
    if ev.source_type == m.SOURCE_API:
        who = ev.source_system or ev.reporter_name or "未知来源"
        return f"第三方·{who}"
    return _SOURCE_LABELS.get(ev.source_type, ev.source_type)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def event_out(ev: RegisteredEvent, *, attachments: Optional[list] = None,
              audit: Optional[list] = None, attachment_count: Optional[int] = None) -> dict:
    out = {
        "id": ev.id, "eventNo": ev.event_no, "title": ev.title,
        "description": ev.description or "", "eventType": ev.event_type or "",
        "severity": ev.severity, "tags": ev.tags or [], "payload": ev.payload or {},
        "occurredAt": _iso(ev.occurred_at), "recordedAt": _iso(ev.recorded_at),
        "sourceType": ev.source_type, "sourceLabel": source_label(ev),
        "sourceSystem": ev.source_system, "sourceRef": ev.source_ref,
        "reporterType": ev.reporter_type, "reporterName": ev.reporter_name,
        "ingestKeyId": ev.ingest_key_id, "clientIp": ev.client_ip,
        "confidence": ev.confidence, "ontologyId": ev.ontology_id,
        "subjectRef": ev.subject_ref, "supersedesId": ev.supersedes_id,
        "status": ev.status, "createdAt": _iso(ev.created_at), "updatedAt": _iso(ev.updated_at),
    }
    if attachment_count is not None:
        out["attachmentCount"] = attachment_count
    if attachments is not None:
        out["attachments"] = [attachment_out(a) for a in attachments]
    if audit is not None:
        out["auditTrail"] = [audit_out(a) for a in audit]
    return out


def attachment_out(a: EventAttachment) -> dict:
    return {
        "id": a.id, "eventId": a.event_id, "filename": a.filename,
        "fileSize": a.file_size, "mimeType": a.mime_type, "sha256": a.sha256,
        "uploadedBy": a.uploaded_by, "createdAt": _iso(a.created_at),
    }


def audit_out(a: EventAuditLog) -> dict:
    return {
        "id": a.id, "seq": a.seq, "action": a.action,
        "actorType": a.actor_type, "actorId": a.actor_id, "actorName": a.actor_name,
        "changes": a.changes, "note": a.note, "ip": a.ip, "createdAt": _iso(a.created_at),
    }


def key_out(k: EventIngestKey, *, plaintext: Optional[str] = None) -> dict:
    out = {
        "id": k.id, "name": k.name, "keyPrefix": k.key_prefix,
        "enabled": k.enabled, "allowedSourceSystem": k.allowed_source_system,
        "createdBy": k.created_by, "createdAt": _iso(k.created_at),
        "lastUsedAt": _iso(k.last_used_at), "revokedAt": _iso(k.revoked_at),
        # 明文密钥随记录返回，前端可随时复制（旧密钥无留存则为 null）
        "plaintextKey": plaintext if plaintext is not None else k.secret_plain,
    }
    return out


# ── 小工具 ──────────────────────────────────────────────────────

def _norm_severity(sev: Optional[str]) -> str:
    s = (sev or "info").strip().lower()
    return s if s in m.SEVERITIES else "info"


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return v
