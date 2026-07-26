"""
哨兵引擎 API

挂载于 /api/v1/ontologies/{ontology_id}/sentinels
  - 哨兵 CRUD / 启停
  - 手动触发(全量评估)
  - 触发日志 / 通知 可观测查询
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import (
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)
from sqlalchemy.orm import Session, sessionmaker

from app.deps import get_db, get_current_user
from app.schemas.ontology_formal import CamelModel
from app.models.sentinel import Sentinel, SentinelFiring, SentinelMatchState, Notification
from app.models.ontology import OntologyProject
from app.ontologies.release_context import current_release_context
from app.services.sentinel.engine import run_manual
from app.ontologies.access import ontology_access_guard
from app.ontologies.sentinels.dynamic_service import (
    ORIGIN_BUILTIN,
    _sentinel_write_fence,
)
from app.shared.time_utils import utc_iso

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


class SentinelOperationalUpdate(CamelModel):
    enabled: Optional[StrictBool] = None
    muted: Optional[StrictBool] = None
    expected_release_id: StrictStr
    expected_generation: StrictInt = Field(ge=0)

    @field_validator("expected_release_id")
    @classmethod
    def normalize_release_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("expectedReleaseId 不能为空")
        return normalized

    @model_validator(mode="after")
    def require_state_field(self):
        if self.enabled is None and self.muted is None:
            raise ValueError("enabled 与 muted 至少提供一个")
        return self


def _project(
        db: Session, ontology_id: str, *, for_update: bool = False) -> OntologyProject:
    query = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id)
    if for_update:
        query = query.with_for_update().populate_existing()
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
        "lastScannedAt": utc_iso(s.last_scanned_at),
        "enabled": s.enabled, "status": s.status,
        "enableGeneration": int(s.enable_generation or 0),
        "releaseId": s.bound_release_id,
        "origin": s.origin,
        "source": s.source,
        "createdAt": utc_iso(s.created_at),
        "updatedAt": utc_iso(s.updated_at),
    }


def _released_dict(
        ontology_id: str, release_id: str, raw: dict,
        live: Sentinel | None) -> dict[str, Any]:
    """Serialize definition fields only from the immutable release snapshot.

    ``sentinels`` is also the mutable runtime projection and can contain a draft
    that has not been promoted yet.  Definition fields therefore remain frozen;
    enabled/muted and last-scanned telemetry are the deliberately mutable
    operational overlay consumed by the runtime.
    """
    operational = live if live is not None and live.status == "published" else None
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
        "muted": (
            bool(operational.muted)
            if operational is not None else bool(raw.get("muted", False))
        ),
        "lastScannedAt": (
            utc_iso(operational.last_scanned_at)
            if operational is not None else None
        ),
        "enabled": (
            bool(operational.enabled)
            if operational is not None else bool(raw.get("enabled", True))
        ),
        "enableGeneration": (
            int(operational.enable_generation or 0)
            if operational is not None else 0
        ),
        "releaseId": release_id,
        "status": "published",
        "origin": ORIGIN_BUILTIN,
        "source": raw.get("source"),
        "createdAt": None,
        "updatedAt": None,
    }


@router.get("/")
def list_sentinels(ontology_id: str, release_id: Optional[str] = None,
                   db: Session = Depends(get_db), _=Depends(get_current_user)):
    release = current_release_context(
        db, ontology_id, expected_release_id=release_id)
    released = [
        item for item in release.snapshot["sentinels"]
        if isinstance(item, dict) and item.get("id")
    ]
    ids = {str(item["id"]) for item in released}
    live_by_id = {item.id: item for item in db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.origin == ORIGIN_BUILTIN,
        Sentinel.id.in_(ids),
    ).all()} if ids else {}
    return {"data": [
        _released_dict(
            ontology_id, release.id, item,
            live_by_id.get(str(item["id"])),
        )
        for item in released
    ]}


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
                 include_history: bool = False,
                 db: Session = Depends(get_db), _=Depends(get_current_user)):
    q = db.query(SentinelFiring).filter(SentinelFiring.ontology_id == ontology_id)
    released_names: dict[str, str] = {}
    if not include_history:
        release = current_release_context(
            db, ontology_id, expected_release_id=release_id)
        builtin_ids = {
            str(item["id"])
            for item in release.snapshot["sentinels"] if item.get("id")
        }
        dynamic_ids = {
            str(item[0])
            for item in db.query(Sentinel.id).filter(
                Sentinel.ontology_id == ontology_id,
                Sentinel.origin == "assistant_dynamic",
                Sentinel.bound_release_id == release.id,
            ).all()
        }
        allowed_ids = builtin_ids | dynamic_ids
        released_names = {
            str(item["id"]): str(item.get("displayName") or item.get("name") or "")
            for item in release.snapshot["sentinels"] if item.get("id")
        }
        # Firing lineage, rather than snapshot membership, is authoritative:
        # assistant-created overlays are release-bound but intentionally absent
        # from the built-in Sentinel snapshot.
        if not allowed_ids:
            return {"data": []}
        q = q.filter(
            SentinelFiring.ontology_release_id == release.id,
            SentinelFiring.sentinel_id.in_(allowed_ids),
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
        "createdAt": utc_iso(f.created_at),
    } for f in items]}


@router.get("/notifications")
def list_notifications(ontology_id: str, limit: int = 50,
                       release_id: Optional[str] = None,
                       include_history: bool = False,
                       db: Session = Depends(get_db), _=Depends(get_current_user)):
    query = db.query(Notification).filter(
        Notification.ontology_id == ontology_id)
    if not include_history:
        release = current_release_context(
            db, ontology_id, expected_release_id=release_id)
        query = query.filter(
            Notification.ontology_release_id == release.id)
    items = query.order_by(
        Notification.created_at.desc()).limit(limit).all()
    return {"data": [{
        "id": n.id, "channel": n.channel, "recipient": n.recipient,
        "subject": n.subject, "body": n.body, "relatedObjectId": n.related_object_id,
        "actionId": n.action_id, "status": n.status,
        "ontologyReleaseId": n.ontology_release_id,
        "sentinelId": n.sentinel_id,
        "actionLogId": n.action_log_id,
        "createdAt": utc_iso(n.created_at),
    } for n in items]}


@router.get("/cdc-status")
def get_cdc_status(
        ontology_id: str, release_id: Optional[str] = None,
        include_history: bool = False,
        db: Session = Depends(get_db),
        _=Depends(get_current_user)):
    """Authenticated operational view of durable Sentinel CDC and dead letters."""
    _project(db, ontology_id)
    from app.ontologies.sentinels.cdc import cdc_dispatch_status

    factory = sessionmaker(
        bind=db.get_bind(), expire_on_commit=False)
    return {
        "data": cdc_dispatch_status(
            ontology_id,
            ontology_release_id=release_id,
            include_history=include_history,
            session_factory=factory,
        ),
    }


@router.patch("/{sentinel_id}/operational-state")
def update_operational_state(
        ontology_id: str, sentinel_id: str,
        body: SentinelOperationalUpdate,
        db: Session = Depends(get_db), _=Depends(get_current_user)):
    """CAS-update the mutable overlay of one exact published built-in."""
    with _sentinel_write_fence(db, sentinel_id):
        _project(db, ontology_id, for_update=True)
        context = current_release_context(
            db,
            ontology_id,
            expected_release_id=body.expected_release_id,
        )
        released = next((
            item for item in context.snapshot.get("sentinels") or []
            if isinstance(item, dict)
            and str(item.get("id") or "") == sentinel_id
        ), None)
        if released is None:
            raise HTTPException(409, detail={
                "code": "builtin_sentinel_not_in_current_release",
                "message": "该哨兵不属于当前不可变发布版本，请刷新后重试",
                "currentReleaseId": context.id,
            })

        sentinel = (
            db.query(Sentinel)
            .filter(
                Sentinel.id == sentinel_id,
                Sentinel.ontology_id == ontology_id,
                Sentinel.origin == ORIGIN_BUILTIN,
            )
            .with_for_update()
            .populate_existing()
            .first()
        )
        if (
            sentinel is None
            or sentinel.status != "published"
            or sentinel.retired_at is not None
        ):
            raise HTTPException(409, detail={
                "code": "builtin_sentinel_not_operational",
                "message": "当前发布哨兵缺少可用的运行态投影，已拒绝修改",
                "currentReleaseId": context.id,
            })

        generation = int(sentinel.enable_generation or 0)
        if generation != body.expected_generation:
            raise HTTPException(409, detail={
                "code": "builtin_sentinel_generation_conflict",
                "message": "哨兵运行状态已被其他会话修改，请刷新后重试",
                "expectedGeneration": body.expected_generation,
                "currentGeneration": generation,
                "currentReleaseId": context.id,
            })

        was_enabled = bool(sentinel.enabled)
        was_muted = bool(sentinel.muted)
        target_enabled = (
            was_enabled if body.enabled is None else bool(body.enabled)
        )
        target_muted = was_muted if body.muted is None else bool(body.muted)
        activation_transition = (
            (not was_enabled and target_enabled)
            or (
                was_enabled
                and target_enabled
                and was_muted
                and not target_muted
            )
        )

        sentinel.enabled = target_enabled
        sentinel.muted = target_muted
        if was_enabled and not target_enabled:
            # A later re-enable is a new lifecycle. Keeping completed match
            # state here would make activation see every existing match as
            # no_change and silently skip the action.
            db.query(SentinelMatchState).filter(
                SentinelMatchState.ontology_id == ontology_id,
                SentinelMatchState.sentinel_id == sentinel.id,
            ).delete(synchronize_session=False)

        if activation_transition:
            sentinel.enable_generation = generation + 1
            if bool(released.get("onChange", True)):
                from app.ontologies.sentinels.cdc import (
                    capture_builtin_activation,
                )
                activation = capture_builtin_activation(
                    db,
                    ontology_id=ontology_id,
                    ontology_release_id=context.id,
                    sentinel_id=sentinel.id,
                    enable_generation=sentinel.enable_generation,
                )
                if activation is None:
                    raise HTTPException(503, detail={
                        "code": "builtin_sentinel_activation_unavailable",
                        "message": "哨兵初始化任务无法持久化，运行状态修改已回滚",
                    })

        db.commit()
        db.refresh(sentinel)
        return {
            "data": _released_dict(
                ontology_id, context.id, released, sentinel)
        }


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
    with _sentinel_write_fence(db, sentinel_id):
        update = body.model_dump(exclude_unset=True)
        project = _project(db, ontology_id, for_update=True)
        s = (
            db.query(Sentinel)
            .filter(
                Sentinel.id == sentinel_id,
                Sentinel.ontology_id == ontology_id,
                Sentinel.origin == ORIGIN_BUILTIN,
            )
            .with_for_update()
            .populate_existing()
            .first()
        )
        if not s:
            raise HTTPException(404, "Sentinel not found")
        operational_fields = {"enabled", "muted"}
        if s.status == "published" and set(update) & operational_fields:
            raise HTTPException(409, detail={
                "code": "sentinel_operational_api_required",
                "message": (
                    "已发布 Sentinel 的启停/静默必须使用带发布版本与代次校验的 "
                    "operational-state 接口"
                ),
            })
        if (project.status or "") != "draft" and set(update) - operational_fields:
            raise HTTPException(
                409,
                "已发布 Sentinel 仅允许启停/静默；修改条件、绑定或动作前请先撤回本体发布",
            )
        if ((project.status or "") == "published"
                and update.get("enabled") is True
                and (s.status or "") != "published"):
            raise HTTPException(
                409, "该 Sentinel 不属于当前发布版本；请撤回、重新发布后再启用")
        for k, v in update.items():
            setattr(s, k, v)
        if not s.primary_alias and s.bindings:
            s.primary_alias = s.bindings[0].get("alias")
        if (project.status or "") == "draft":
            s.status = "draft"
        db.commit()
        db.refresh(s)
        return {"data": _dict(s)}


@router.delete("/{sentinel_id}", status_code=204)
def delete_sentinel(ontology_id: str, sentinel_id: str,
                    db: Session = Depends(get_db), _=Depends(get_current_user)):
    with _sentinel_write_fence(db, sentinel_id):
        _require_draft(db, ontology_id)
        s = (
            db.query(Sentinel)
            .filter(
                Sentinel.id == sentinel_id,
                Sentinel.ontology_id == ontology_id,
                Sentinel.origin == ORIGIN_BUILTIN,
            )
            .with_for_update()
            .populate_existing()
            .first()
        )
        if not s:
            raise HTTPException(404, "Sentinel not found")
        # 命中状态一并清理：残留 match_state 会让重建同名哨兵时边沿差分失真
        db.query(SentinelMatchState).filter(
            SentinelMatchState.sentinel_id == sentinel_id).delete(
                synchronize_session=False)
        db.delete(s)
        db.commit()


@router.post("/{sentinel_id}/toggle")
def toggle_sentinel(ontology_id: str, sentinel_id: str,
                    db: Session = Depends(get_db), _=Depends(get_current_user)):
    with _sentinel_write_fence(db, sentinel_id):
        project = _project(db, ontology_id, for_update=True)
        s = (
            db.query(Sentinel)
            .filter(
                Sentinel.id == sentinel_id,
                Sentinel.ontology_id == ontology_id,
                Sentinel.origin == ORIGIN_BUILTIN,
            )
            .with_for_update()
            .populate_existing()
            .first()
        )
        if not s:
            raise HTTPException(404, "Sentinel not found")
        if s.status == "published":
            raise HTTPException(409, detail={
                "code": "sentinel_operational_api_required",
                "message": (
                    "已发布 Sentinel 的启停必须使用带发布版本与代次校验的 "
                    "operational-state 接口"
                ),
            })
        s.enabled = not s.enabled
        db.commit()
        return {"enabled": s.enabled}
