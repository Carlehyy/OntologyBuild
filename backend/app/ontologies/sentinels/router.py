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
from app.models.ontology import OntologyProject
from app.ontologies.release_context import current_release_context
from app.services.sentinel.engine import run_manual
from app.ontologies.access import ontology_access_guard
from app.ontologies.sentinels.dynamic_service import ORIGIN_BUILTIN

router = APIRouter(dependencies=[Depends(ontology_access_guard)])


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
    action_parameters: dict = Field(default_factory=dict)
    on_change: bool = True
    on_schedule: bool = False
    scan_interval_seconds: int = 300
    trigger_mode: str = "on_enter"
    muted: bool = False
    enabled: bool = True
    # 本体 release 是唯一上线边界；客户端不能把未校验定义直接标成 published。
    status: str = "draft"


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
    action_parameters: Optional[dict] = None
    on_change: Optional[bool] = None
    on_schedule: Optional[bool] = None
    scan_interval_seconds: Optional[int] = None
    trigger_mode: Optional[str] = None
    muted: Optional[bool] = None
    enabled: Optional[bool] = None


def _project(
        db: Session, ontology_id: str, *, for_update: bool = False) -> OntologyProject:
    query = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id)
    if for_update:
        query = query.with_for_update()
    project = query.first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    return project


def _require_draft(db: Session, ontology_id: str) -> OntologyProject:
    project = _project(db, ontology_id, for_update=True)
    if (project.status or "") != "draft":
        raise HTTPException(409, "Sentinel 结构只能在 draft 本体中维护；请先撤回发布")
    return project


def _dict(s: Sentinel) -> dict[str, Any]:
    return {
        "id": s.id, "ontologyId": s.ontology_id, "name": s.name,
        "displayName": s.display_name, "description": s.description,
        "bindings": s.bindings or [], "links": s.links or [],
        "condition": s.condition, "conditionRows": s.condition_rows or [],
        "conditionLogic": s.condition_logic or "and", "primaryAlias": s.primary_alias,
        "actionIds": s.action_ids or [], "actionParameters": s.action_parameters or {},
        "onChange": s.on_change,
        "onSchedule": s.on_schedule, "scanIntervalSeconds": s.scan_interval_seconds,
        "triggerMode": s.trigger_mode, "muted": s.muted,
        "lastScannedAt": s.last_scanned_at.isoformat() if s.last_scanned_at else None,
        "enabled": s.enabled, "status": s.status,
        "origin": s.origin,
        "source": s.source,
        "createdAt": s.created_at.isoformat() if s.created_at else None,
        "updatedAt": s.updated_at.isoformat() if s.updated_at else None,
    }


def _released_dict(ontology_id: str, raw: dict, live: Sentinel | None) -> dict[str, Any]:
    """Serialize definition fields only from the immutable release snapshot.

    ``sentinels`` is also the mutable runtime projection and can contain a draft
    that has not been promoted yet.  Only last-scanned telemetry is safe to
    overlay; enabled/muted remain the values that were actually published.
    """
    return {
        "id": str(raw.get("id") or ""),
        "ontologyId": ontology_id,
        "name": str(raw.get("name") or ""),
        "displayName": raw.get("displayName") or raw.get("name") or "",
        "description": raw.get("description"),
        "bindings": raw.get("bindings") or [],
        "links": raw.get("links") or [],
        "condition": raw.get("condition"),
        "conditionRows": raw.get("conditionRows") or [],
        "conditionLogic": raw.get("conditionLogic") or "and",
        "primaryAlias": raw.get("primaryAlias"),
        "actionIds": raw.get("actionIds") or [],
        "actionParameters": raw.get("actionParameters") or {},
        "onChange": bool(raw.get("onChange", True)),
        "onSchedule": bool(raw.get("onSchedule", False)),
        "scanIntervalSeconds": int(raw.get("scanIntervalSeconds") or 300),
        "triggerMode": raw.get("triggerMode") or "on_enter",
        "muted": bool(raw.get("muted", False)),
        "lastScannedAt": (
            live.last_scanned_at.isoformat()
            if live is not None and live.last_scanned_at else None
        ),
        "enabled": bool(raw.get("enabled", True)),
        "status": "published",
        "origin": ORIGIN_BUILTIN,
        "source": raw.get("source"),
        "createdAt": None,
        "updatedAt": None,
    }


@router.get("/")
def list_sentinels(ontology_id: str, release_id: Optional[str] = None,
                   db: Session = Depends(get_db), _=Depends(get_current_user)):
    if release_id:
        release = current_release_context(
            db, ontology_id, expected_release_id=release_id)
        released = [item for item in release.snapshot["sentinels"] if item.get("id")]
        ids = {str(item["id"]) for item in released}
        live_by_id = {item.id: item for item in db.query(Sentinel).filter(
            Sentinel.ontology_id == ontology_id,
            Sentinel.origin == ORIGIN_BUILTIN,
            Sentinel.id.in_(ids),
        ).all()} if ids else {}
        return {"data": [
            _released_dict(ontology_id, item, live_by_id.get(str(item["id"])))
            for item in released
        ]}
    items = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.origin == ORIGIN_BUILTIN,
    ).order_by(Sentinel.created_at.desc()).all()
    return {"data": [_dict(s) for s in items]}


@router.post("/", status_code=201)
def create_sentinel(ontology_id: str, body: SentinelIn,
                    db: Session = Depends(get_db), _=Depends(get_current_user)):
    _require_draft(db, ontology_id)
    s = Sentinel(
        ontology_id=ontology_id,
        origin=ORIGIN_BUILTIN,
        **body.model_dump(exclude={"status"}),
        status="draft",
    )
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
                 release_id: Optional[str] = None,
                 db: Session = Depends(get_db), _=Depends(get_current_user)):
    q = db.query(SentinelFiring).filter(SentinelFiring.ontology_id == ontology_id)
    released_names: dict[str, str] = {}
    if release_id:
        release = current_release_context(
            db, ontology_id, expected_release_id=release_id)
        sentinel_ids = {
            str(item["id"]) for item in release.snapshot["sentinels"] if item.get("id")
        }
        released_names = {
            str(item["id"]): str(item.get("displayName") or item.get("name") or "")
            for item in release.snapshot["sentinels"] if item.get("id")
        }
        if sentinel_id and sentinel_id not in sentinel_ids:
            return {"data": []}
        if not sentinel_ids:
            return {"data": []}
        q = q.filter(
            SentinelFiring.ontology_release_id == release.id,
            SentinelFiring.sentinel_id.in_(sentinel_ids),
        )
    if sentinel_id:
        q = q.filter(SentinelFiring.sentinel_id == sentinel_id)
    items = q.order_by(SentinelFiring.created_at.desc()).limit(limit).all()
    return {"data": [{
        "id": f.id, "sentinelId": f.sentinel_id,
        "sentinelName": released_names.get(f.sentinel_id) or f.sentinel_name,
        "triggerSource": f.trigger_source, "status": f.status,
        "matchCount": f.match_count, "matches": f.matches or [],
        "entered": f.entered or [], "left": f.left or [],
        "actionResults": f.action_results or [], "error": f.error,
        "durationMs": f.duration_ms,
        "ontologyVersion": f.ontology_version,
        "ontologyReleaseId": f.ontology_release_id,
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
        Sentinel.id == sentinel_id, Sentinel.ontology_id == ontology_id,
        Sentinel.origin == ORIGIN_BUILTIN).first()
    if not s:
        raise HTTPException(404, "Sentinel not found")
    return {"data": _dict(s)}


@router.put("/{sentinel_id}")
def update_sentinel(ontology_id: str, sentinel_id: str, body: SentinelUpdate,
                    db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = db.query(Sentinel).filter(
        Sentinel.id == sentinel_id, Sentinel.ontology_id == ontology_id,
        Sentinel.origin == ORIGIN_BUILTIN).first()
    if not s:
        raise HTTPException(404, "Sentinel not found")
    update = body.model_dump(exclude_unset=True)
    project = _project(db, ontology_id, for_update=True)
    operational_fields = {"enabled", "muted"}
    if (project.status or "") != "draft" and set(update) - operational_fields:
        raise HTTPException(
            409,
            "已发布 Sentinel 仅允许启停/静默；修改条件、绑定或动作前请先撤回本体发布",
        )
    if ((project.status or "") == "published" and update.get("enabled") is True
            and (s.status or "") != "published"):
        raise HTTPException(
            409, "该 Sentinel 不属于当前发布版本；请撤回、重新发布后再启用")
    for k, v in update.items():
        setattr(s, k, v)
    if not s.primary_alias and s.bindings:
        s.primary_alias = s.bindings[0].get("alias")
    if (project.status or "") == "draft":
        s.status = "draft"
    db.commit(); db.refresh(s)
    return {"data": _dict(s)}


@router.delete("/{sentinel_id}", status_code=204)
def delete_sentinel(ontology_id: str, sentinel_id: str,
                    db: Session = Depends(get_db), _=Depends(get_current_user)):
    _require_draft(db, ontology_id)
    s = db.query(Sentinel).filter(
        Sentinel.id == sentinel_id, Sentinel.ontology_id == ontology_id,
        Sentinel.origin == ORIGIN_BUILTIN).first()
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
        Sentinel.id == sentinel_id, Sentinel.ontology_id == ontology_id,
        Sentinel.origin == ORIGIN_BUILTIN).first()
    if not s:
        raise HTTPException(404, "Sentinel not found")
    project = _project(db, ontology_id, for_update=True)
    if (not s.enabled and (project.status or "") == "published"
            and (s.status or "") != "published"):
        raise HTTPException(
            409, "该 Sentinel 不属于当前发布版本；请撤回、重新发布后再启用")
    s.enabled = not s.enabled
    db.commit()
    return {"enabled": s.enabled}
