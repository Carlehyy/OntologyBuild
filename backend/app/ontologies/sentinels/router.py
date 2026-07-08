"""
哨兵引擎 API

挂载于 /api/v1/ontologies/{ontology_id}/sentinels
  - 哨兵 CRUD / 启停
  - 手动触发(全量评估)
  - 触发日志 / 通知 可观测查询
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.schemas.ontology_formal import CamelModel
from app.models.sentinel import Sentinel, SentinelFiring, SentinelMatchState, Notification
from app.services.sentinel.engine import run_manual

router = APIRouter()


class SentinelIn(CamelModel):
    name: str
    display_name: str
    description: Optional[str] = None
    bindings: list = Field(default_factory=list)
    links: list = Field(default_factory=list)
    condition: Optional[str] = None
    condition_rows: list = Field(default_factory=list)
    condition_logic: str = "and"
    primary_alias: Optional[str] = None
    action_ids: list = Field(default_factory=list)
    on_change: bool = True
    on_schedule: bool = False
    scan_interval_seconds: int = 300
    trigger_mode: str = "on_enter"
    muted: bool = False
    enabled: bool = True
    # UI 创建即上线（enabled 是运行开关）；status 是展示标签，默认与行为一致
    status: str = "published"


class SentinelUpdate(CamelModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    bindings: Optional[list] = None
    links: Optional[list] = None
    condition: Optional[str] = None
    condition_rows: Optional[list] = None
    condition_logic: Optional[str] = None
    primary_alias: Optional[str] = None
    action_ids: Optional[list] = None
    on_change: Optional[bool] = None
    on_schedule: Optional[bool] = None
    scan_interval_seconds: Optional[int] = None
    trigger_mode: Optional[str] = None
    muted: Optional[bool] = None
    enabled: Optional[bool] = None
    status: Optional[str] = None


def _dict(s: Sentinel) -> dict[str, Any]:
    return {
        "id": s.id, "ontologyId": s.ontology_id, "name": s.name,
        "displayName": s.display_name, "description": s.description,
        "bindings": s.bindings or [], "links": s.links or [],
        "condition": s.condition, "conditionRows": s.condition_rows or [],
        "conditionLogic": s.condition_logic or "and", "primaryAlias": s.primary_alias,
        "actionIds": s.action_ids or [], "onChange": s.on_change,
        "onSchedule": s.on_schedule, "scanIntervalSeconds": s.scan_interval_seconds,
        "triggerMode": s.trigger_mode, "muted": s.muted,
        "lastScannedAt": s.last_scanned_at.isoformat() if s.last_scanned_at else None,
        "enabled": s.enabled, "status": s.status,
        "source": s.source,
        "createdAt": s.created_at.isoformat() if s.created_at else None,
        "updatedAt": s.updated_at.isoformat() if s.updated_at else None,
    }


@router.get("/")
def list_sentinels(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    items = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id).order_by(Sentinel.created_at.desc()).all()
    return {"data": [_dict(s) for s in items]}


@router.post("/", status_code=201)
def create_sentinel(ontology_id: str, body: SentinelIn,
                    db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = Sentinel(ontology_id=ontology_id, **body.model_dump())
    if not s.primary_alias and s.bindings:
        s.primary_alias = s.bindings[0].get("alias")
    db.add(s); db.commit(); db.refresh(s)
    return {"data": _dict(s)}


@router.post("/run")
def run(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """手动触发：全量评估本体所有启用哨兵。"""
    return {"data": run_manual(db, ontology_id)}


@router.get("/firings")
def list_firings(ontology_id: str, sentinel_id: Optional[str] = None, limit: int = 50,
                 db: Session = Depends(get_db), _=Depends(get_current_user)):
    q = db.query(SentinelFiring).filter(SentinelFiring.ontology_id == ontology_id)
    if sentinel_id:
        q = q.filter(SentinelFiring.sentinel_id == sentinel_id)
    items = q.order_by(SentinelFiring.created_at.desc()).limit(limit).all()
    return {"data": [{
        "id": f.id, "sentinelId": f.sentinel_id, "sentinelName": f.sentinel_name,
        "triggerSource": f.trigger_source, "status": f.status,
        "matchCount": f.match_count, "matches": f.matches or [],
        "entered": f.entered or [], "left": f.left or [],
        "actionResults": f.action_results or [], "error": f.error,
        "durationMs": f.duration_ms,
        "createdAt": f.created_at.isoformat() if f.created_at else None,
    } for f in items]}


@router.get("/notifications")
def list_notifications(ontology_id: str, limit: int = 50,
                       db: Session = Depends(get_db), _=Depends(get_current_user)):
    items = db.query(Notification).filter(
        Notification.ontology_id == ontology_id).order_by(
        Notification.created_at.desc()).limit(limit).all()
    return {"data": [{
        "id": n.id, "channel": n.channel, "recipient": n.recipient,
        "subject": n.subject, "body": n.body, "relatedObjectId": n.related_object_id,
        "actionId": n.action_id, "status": n.status,
        "createdAt": n.created_at.isoformat() if n.created_at else None,
    } for n in items]}


@router.get("/{sentinel_id}")
def get_sentinel(ontology_id: str, sentinel_id: str,
                 db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = db.query(Sentinel).filter(
        Sentinel.id == sentinel_id, Sentinel.ontology_id == ontology_id).first()
    if not s:
        raise HTTPException(404, "Sentinel not found")
    return {"data": _dict(s)}


@router.put("/{sentinel_id}")
def update_sentinel(ontology_id: str, sentinel_id: str, body: SentinelUpdate,
                    db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = db.query(Sentinel).filter(
        Sentinel.id == sentinel_id, Sentinel.ontology_id == ontology_id).first()
    if not s:
        raise HTTPException(404, "Sentinel not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    if not s.primary_alias and s.bindings:
        s.primary_alias = s.bindings[0].get("alias")
    db.commit(); db.refresh(s)
    return {"data": _dict(s)}


@router.delete("/{sentinel_id}", status_code=204)
def delete_sentinel(ontology_id: str, sentinel_id: str,
                    db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = db.query(Sentinel).filter(
        Sentinel.id == sentinel_id, Sentinel.ontology_id == ontology_id).first()
    if not s:
        raise HTTPException(404, "Sentinel not found")
    # 命中状态一并清理：残留 match_state 会让重建同名哨兵时边沿差分失真
    db.query(SentinelMatchState).filter(
        SentinelMatchState.sentinel_id == sentinel_id).delete(synchronize_session=False)
    db.delete(s); db.commit()


@router.post("/{sentinel_id}/toggle")
def toggle_sentinel(ontology_id: str, sentinel_id: str,
                    db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = db.query(Sentinel).filter(
        Sentinel.id == sentinel_id, Sentinel.ontology_id == ontology_id).first()
    if not s:
        raise HTTPException(404, "Sentinel not found")
    s.enabled = not s.enabled
    db.commit()
    return {"enabled": s.enabled}
