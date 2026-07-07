"""
事件登记 API

平台侧（JWT） router          挂 /api/v2/events
  GET    /                      列表（筛选 + 分页）
  POST   /                      平台录入
  GET    /stats/summary         概览计数
  GET    /ingest-keys           密钥列表（admin）
  POST   /ingest-keys           创建密钥（admin，明文仅此一次返回）
  DELETE /ingest-keys/{id}      吊销密钥（admin）
  GET    /{id}                  详情（含附件 + 审计轨迹）
  PATCH  /{id}                  编辑
  POST   /{id}/status           改状态（active/archived）
  DELETE /{id}                  软删除→归档（?hard=true 仅 admin 物理删）
  POST   /{id}/attachments      上传附件
  GET    /{id}/attachments/{aid}/download
  DELETE /{id}/attachments/{aid}

第三方侧（X-API-Key） ingest_router   挂 /api/v2/ingest
  GET    /whoami                自测密钥
  POST   /events                单条或批量上传（幂等）
  POST   /events/{id}/attachments  给自己上传的事件补附件

治理不变式：所有变更都经 service 层并写审计；平台与第三方来源在 source_type
与 sourceLabel 上清晰区分。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin
from app.events import models as m, service
from app.events.deps import get_ingest_key, IngestContext
from app.events.models import RegisteredEvent, EventAttachment, EventAuditLog
from app.events.schemas import (
    EventCreate, EventUpdate, StatusChange, IngestEvent, IngestKeyCreate,
)

router = APIRouter()
ingest_router = APIRouter()


def _ok(data):
    return {"data": data}


def _require_event(db: Session, event_id: str) -> RegisteredEvent:
    ev = db.query(RegisteredEvent).filter(RegisteredEvent.id == event_id).first()
    if not ev:
        raise HTTPException(404, "事件不存在")
    return ev


# ══════════════════ 平台侧（JWT）══════════════════

# —— 静态路由须在 /{event_id} 之前声明，避免被动态段吞掉 ——

@router.get("")
def list_events(
    q: Optional[str] = Query(None, description="标题/描述/编号模糊搜索"),
    source_type: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="active|archived|all；缺省仅 active"),
    ontology_id: Optional[str] = Query(None),
    start: Optional[datetime] = Query(None, description="recorded_at 下界"),
    end: Optional[datetime] = Query(None, description="recorded_at 上界"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    query = db.query(RegisteredEvent)
    if not status:
        query = query.filter(RegisteredEvent.status == m.STATUS_ACTIVE)
    elif status != "all":
        query = query.filter(RegisteredEvent.status == status)
    if source_type:
        query = query.filter(RegisteredEvent.source_type == source_type)
    if event_type:
        query = query.filter(RegisteredEvent.event_type == event_type)
    if severity:
        query = query.filter(RegisteredEvent.severity == severity)
    if ontology_id:
        query = query.filter(RegisteredEvent.ontology_id == ontology_id)
    if start:
        query = query.filter(RegisteredEvent.recorded_at >= start)
    if end:
        query = query.filter(RegisteredEvent.recorded_at <= end)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            RegisteredEvent.title.ilike(like)
            | RegisteredEvent.description.ilike(like)
            | RegisteredEvent.event_no.ilike(like)
        )

    total = query.count()
    rows = (query.order_by(RegisteredEvent.recorded_at.desc(),
                           RegisteredEvent.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size).all())

    ids = [r.id for r in rows]
    counts: dict[str, int] = {}
    if ids:
        for eid, cnt in (db.query(EventAttachment.event_id, func.count(EventAttachment.id))
                         .filter(EventAttachment.event_id.in_(ids))
                         .group_by(EventAttachment.event_id).all()):
            counts[eid] = cnt
    items = [service.event_out(r, attachment_count=counts.get(r.id, 0)) for r in rows]
    return _ok({"items": items, "total": total, "page": page, "pageSize": page_size})


@router.post("", status_code=201)
def create_event(body: EventCreate, db: Session = Depends(get_db),
                 user=Depends(get_current_user)):
    ev = service.create_event(db, body, user)
    return _ok(service.event_out(ev, attachment_count=0))


@router.get("/stats/summary")
def stats_summary(db: Session = Depends(get_db), _=Depends(get_current_user)):
    def _count(*filters):
        query = db.query(func.count(RegisteredEvent.id))
        for f in filters:
            query = query.filter(f)
        return query.scalar() or 0

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    by_severity = {sev: _count(RegisteredEvent.severity == sev,
                               RegisteredEvent.status == m.STATUS_ACTIVE)
                   for sev in m.SEVERITIES}
    return _ok({
        "total": _count(),
        "active": _count(RegisteredEvent.status == m.STATUS_ACTIVE),
        "archived": _count(RegisteredEvent.status == m.STATUS_ARCHIVED),
        "platform": _count(RegisteredEvent.source_type == m.SOURCE_PLATFORM),
        "api": _count(RegisteredEvent.source_type == m.SOURCE_API),
        "today": _count(RegisteredEvent.recorded_at >= today),
        "bySeverity": by_severity,
    })


# —— 密钥管理（admin）——

@router.get("/ingest-keys")
def list_keys(db: Session = Depends(get_db), _=Depends(require_admin)):
    from app.events.models import EventIngestKey
    rows = (db.query(EventIngestKey)
            .order_by(EventIngestKey.created_at.desc()).all())
    return _ok([service.key_out(k) for k in rows])


@router.post("/ingest-keys", status_code=201)
def create_key(body: IngestKeyCreate, db: Session = Depends(get_db),
               user=Depends(require_admin)):
    row, plaintext = service.mint_ingest_key(db, body.name, body.allowed_source_system, user)
    return _ok(service.key_out(row, plaintext=plaintext))


@router.delete("/ingest-keys/{key_id}")
def revoke_key(key_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    from app.events.models import EventIngestKey
    row = db.query(EventIngestKey).filter(EventIngestKey.id == key_id).first()
    if not row:
        raise HTTPException(404, "密钥不存在")
    service.revoke_ingest_key(db, row)
    return _ok(service.key_out(row))


# —— 单条事件 ——

@router.get("/{event_id}")
def get_event(event_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    ev = _require_event(db, event_id)
    attachments = (db.query(EventAttachment)
                   .filter(EventAttachment.event_id == ev.id)
                   .order_by(EventAttachment.created_at.asc()).all())
    audit = (db.query(EventAuditLog)
             .filter(EventAuditLog.event_id == ev.id)
             .order_by(EventAuditLog.seq.asc()).all())
    return _ok(service.event_out(ev, attachments=attachments, audit=audit))


@router.patch("/{event_id}")
def update_event(event_id: str, body: EventUpdate, db: Session = Depends(get_db),
                 user=Depends(get_current_user)):
    ev = _require_event(db, event_id)
    ev = service.update_event(db, ev, body, user)
    return _ok(service.event_out(ev))


@router.post("/{event_id}/status")
def change_status(event_id: str, body: StatusChange, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    ev = _require_event(db, event_id)
    ev = service.change_status(db, ev, body.status, body.note, user)
    return _ok(service.event_out(ev))


@router.delete("/{event_id}")
def delete_event(event_id: str, hard: bool = Query(False),
                 db: Session = Depends(get_db), user=Depends(get_current_user)):
    ev = _require_event(db, event_id)
    if hard:
        if getattr(user, "role", "") != "admin":
            raise HTTPException(403, "物理删除仅管理员可用")
        service.hard_delete_event(db, ev)
        return _ok({"status": "deleted", "id": event_id})
    ev = service.change_status(db, ev, m.STATUS_ARCHIVED, "归档", user)
    return _ok(service.event_out(ev))


# —— 附件 ——

@router.post("/{event_id}/attachments", status_code=201)
async def upload_attachment(event_id: str, file: UploadFile = File(...),
                            db: Session = Depends(get_db), user=Depends(get_current_user)):
    ev = _require_event(db, event_id)
    content = await file.read()
    att = service.add_attachment(db, ev, filename=file.filename,
                                 content=content, mime=file.content_type, user=user)
    return _ok(service.attachment_out(att))


@router.get("/{event_id}/attachments/{att_id}/download")
def download_attachment(event_id: str, att_id: str, db: Session = Depends(get_db),
                        _=Depends(get_current_user)):
    att = (db.query(EventAttachment)
           .filter(EventAttachment.id == att_id, EventAttachment.event_id == event_id).first())
    if not att:
        raise HTTPException(404, "附件不存在")
    import os
    if not os.path.exists(att.file_path):
        raise HTTPException(410, "附件文件已丢失")
    return FileResponse(att.file_path, filename=att.filename,
                        media_type=att.mime_type or "application/octet-stream")


@router.delete("/{event_id}/attachments/{att_id}")
def delete_attachment(event_id: str, att_id: str, db: Session = Depends(get_db),
                      user=Depends(get_current_user)):
    ev = _require_event(db, event_id)
    att = (db.query(EventAttachment)
           .filter(EventAttachment.id == att_id, EventAttachment.event_id == event_id).first())
    if not att:
        raise HTTPException(404, "附件不存在")
    service.remove_attachment(db, ev, att, user)
    return _ok({"status": "deleted", "id": att_id})


# ══════════════════ 第三方侧（X-API-Key）══════════════════

@ingest_router.get("/whoami")
def whoami(ctx: IngestContext = Depends(get_ingest_key)):
    k = ctx.key
    return _ok({"name": k.name, "keyPrefix": k.key_prefix,
                "allowedSourceSystem": k.allowed_source_system,
                "clientIp": ctx.client_ip})


@ingest_router.post("/events")
async def ingest_events(request: Request, ctx: IngestContext = Depends(get_ingest_key),
                        db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(422, "请求体必须是合法 JSON")

    if isinstance(body, list):
        items, single = body, False
    elif isinstance(body, dict) and isinstance(body.get("events"), list):
        items, single = body["events"], False
    elif isinstance(body, dict):
        items, single = [body], True
    else:
        raise HTTPException(422, "请求体应为事件对象、[...] 或 {events:[...]}")

    if not items:
        raise HTTPException(422, "没有可上传的事件")
    if len(items) > 500:
        raise HTTPException(413, "单次批量上限 500 条")

    results = []
    created = duplicated = failed = 0
    for i, raw in enumerate(items):
        try:
            item = IngestEvent.model_validate(raw)
            ev, idem = service.ingest_event(db, item, ctx.key, ctx.client_ip)
            if idem:
                duplicated += 1
            else:
                created += 1
            results.append({"index": i, "ok": True, "idempotent": idem,
                            "event": service.event_out(ev)})
        except HTTPException as e:
            failed += 1
            results.append({"index": i, "ok": False, "error": e.detail, "status": e.status_code})
        except ValidationError as e:
            failed += 1
            msgs = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                             for err in e.errors())
            results.append({"index": i, "ok": False, "error": msgs or "字段校验失败", "status": 422})

    if single:
        r = results[0]
        if not r["ok"]:
            # 保留原始状态码（作用域违规=403，字段错误=422），单条上传不吞成 422
            raise HTTPException(r.get("status", 422), r["error"])
        return _ok({**r["event"], "idempotent": r["idempotent"]})
    return _ok({"created": created, "duplicated": duplicated, "failed": failed,
                "total": len(items), "results": results})


@ingest_router.post("/events/{event_id}/attachments", status_code=201)
async def ingest_attachment(event_id: str, file: UploadFile = File(...),
                            ctx: IngestContext = Depends(get_ingest_key),
                            db: Session = Depends(get_db)):
    ev = db.query(RegisteredEvent).filter(RegisteredEvent.id == event_id).first()
    if not ev:
        raise HTTPException(404, "事件不存在")
    # 边界：密钥只能给「自己上传的」事件补附件
    if ev.ingest_key_id != ctx.key.id:
        raise HTTPException(403, "无权给该事件添加附件")
    content = await file.read()
    # 以密钥身份记 uploaded_by / 审计 actor
    actor = type("KeyActor", (), {"id": ctx.key.id, "username": ctx.key.name,
                                  "_actor_type": "service"})()
    att = service.add_attachment(db, ev, filename=file.filename,
                                 content=content, mime=file.content_type, user=actor)
    return _ok(service.attachment_out(att))
