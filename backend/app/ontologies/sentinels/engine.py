"""
哨兵引擎 (Sentinel Engine) — 三种执行入口，统一汇入评估器

  1. run_manual    手动触发：评估本体下全部启用的哨兵
  2. run_for_change 变化驱动：某对象类型属性变化 → 挑出引用该类型的哨兵 → 逐个评估
  3. run_scheduled  定期扫描：到达各哨兵的扫描间隔 → 逐个评估，更新 last_scanned_at
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.sentinel import Sentinel
from app.models.ontology import OntologyProject
from app.models.ontology_version import OntologyVersion
from app.services.sentinel.evaluator import evaluate_sentinel
from app.ontologies.agent_runtime.boundary import build_scope
from app.ontologies.release_context import current_release_context
from app.ontologies.sentinels.dynamic_service import reconcile_release


def _now():
    return datetime.now(timezone.utc)


def _summary(firings) -> dict:
    return {
        "evaluated": len(firings),
        "fired": sum(1 for f in firings if f.status == "fired"),
        "errors": sum(1 for f in firings if f.status == "error"),
        "no_change": sum(1 for f in firings if f.status == "no_change"),
        "no_match": sum(1 for f in firings if f.status == "no_match"),
        "firings": [{"sentinelId": f.sentinel_id, "sentinelName": f.sentinel_name,
                     "status": f.status, "matchCount": f.match_count,
                     "entered": f.entered or [], "left": f.left or [],
                     "actionResults": f.action_results} for f in firings],
    }


def _current_release_id(db: Session, ontology_id: str) -> str | None:
    """Return the valid current release without consulting project.status."""
    try:
        return current_release_context(db, ontology_id).id
    except HTTPException:
        return None


def _legacy_published_runtime(db: Session, ontology_id: str) -> bool:
    """Temporary compatibility for pre-version-tree built-in Sentinels.

    Assistant-created overlays always require an exact release.  This narrow
    fallback only keeps historical graph-editor Sentinels running until the
    legacy project is repaired with its v0 release pointer.
    """
    return db.query(OntologyProject.id).filter(
        OntologyProject.id == ontology_id,
        OntologyProject.current_release_id.is_(None),
        OntologyProject.status == "published",
    ).first() is not None


def _runtime_available(db: Session, ontology_id: str) -> bool:
    return (
        _current_release_id(db, ontology_id) is not None
        or _legacy_published_runtime(db, ontology_id)
    )


def _release_unavailable_summary() -> dict:
    result = _summary([])
    result["skipped"] = "current_release_unavailable"
    return result


def _prepare_runtime(db: Session, ontology_id: str) -> str | None:
    """Fail closed when the published contract changed since the last trial."""
    has_dynamic = db.query(Sentinel.id).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.origin == "assistant_dynamic",
        Sentinel.retired_at.is_(None),
    ).first()
    if has_dynamic is None:
        return None
    context = current_release_context(db, ontology_id)
    _, _, scope = build_scope(db, ontology_id, release_id=context.id)
    reconcile_release(db, context, scope)
    return context.id


def run_manual(db: Session, ontology_id: str) -> dict:
    """手动触发：全量评估本体所有启用哨兵。"""
    if not _runtime_available(db, ontology_id):
        return _release_unavailable_summary()
    _prepare_runtime(db, ontology_id)
    sentinels = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.enabled == True,  # noqa: E712
        Sentinel.status == "published",
    ).all()
    firings = [evaluate_sentinel(db, ontology_id, s, "manual") for s in sentinels]
    return _summary(firings)


def run_for_save(db: Session, ontology_id: str) -> dict:
    """整体保存后触发：评估该本体所有启用且开启 on_change 的哨兵。

    图谱编辑器的实例变更只有保存时才落库——保存即"变化到达"时刻。
    边沿触发语义(SentinelMatchState)保证重复评估幂等，不会重复放炮。
    """
    if not _runtime_available(db, ontology_id):
        return _release_unavailable_summary()
    _prepare_runtime(db, ontology_id)
    sentinels = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.enabled == True,  # noqa: E712
        Sentinel.on_change == True,  # noqa: E712
        Sentinel.status == "published",
    ).all()
    firings = [evaluate_sentinel(db, ontology_id, s, "change") for s in sentinels]
    return _summary(firings)


def run_for_change(db: Session, ontology_id: str, object_type_id: str,
                   changed_keys: Optional[list] = None) -> dict:
    """变化驱动：挑出 bindings 引用了该对象类型、且开启 on_change 的哨兵并评估。"""
    if not _runtime_available(db, ontology_id):
        return _release_unavailable_summary()
    _prepare_runtime(db, ontology_id)
    sentinels = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.enabled == True,  # noqa: E712
        Sentinel.on_change == True,  # noqa: E712
        Sentinel.status == "published",
    ).all()
    # 仅挑选监听了该对象类型的哨兵(属性级可在此进一步收窄)
    affected = [s for s in sentinels
                if any(b.get("objectTypeId") == object_type_id for b in (s.bindings or []))]
    firings = [evaluate_sentinel(db, ontology_id, s, "change") for s in affected]
    return _summary(firings)


def run_for_link_change(db: Session, ontology_id: str) -> dict:
    """链接拓扑变化驱动：链接实例增删对声明了 links 约束的跨对象哨兵是"变化"。
    只评估 on_change 且带链接约束的哨兵（无链接约束的由属性 CDC 覆盖）。"""
    if not _runtime_available(db, ontology_id):
        return _release_unavailable_summary()
    _prepare_runtime(db, ontology_id)
    sentinels = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.enabled == True,  # noqa: E712
        Sentinel.on_change == True,  # noqa: E712
        Sentinel.status == "published",
    ).all()
    affected = [s for s in sentinels if (s.links or [])]
    firings = [evaluate_sentinel(db, ontology_id, s, "change") for s in affected]
    return _summary(firings)


def run_scheduled(db: Session) -> dict:
    """定期扫描：评估所有到达扫描间隔的哨兵(跨本体)，更新 last_scanned_at。"""
    now = _now()
    dynamic_released_ids = [row[0] for row in db.query(Sentinel.ontology_id).join(
        OntologyProject, OntologyProject.id == Sentinel.ontology_id,
    ).join(
        OntologyVersion,
        OntologyVersion.id == OntologyProject.current_release_id,
    ).filter(
        OntologyVersion.node_kind == "release",
        OntologyVersion.lifecycle_status == "released",
        Sentinel.origin == "assistant_dynamic",
        Sentinel.retired_at.is_(None),
    ).distinct().all()]
    for ontology_id in dynamic_released_ids:
        _prepare_runtime(db, ontology_id)
    sentinels = db.query(Sentinel).join(
        OntologyProject, OntologyProject.id == Sentinel.ontology_id,
    ).outerjoin(
        OntologyVersion,
        OntologyVersion.id == OntologyProject.current_release_id,
    ).filter(
        Sentinel.enabled == True,  # noqa: E712
        Sentinel.on_schedule == True,  # noqa: E712
        Sentinel.status == "published",
        or_(
            and_(
                OntologyVersion.node_kind == "release",
                OntologyVersion.lifecycle_status == "released",
            ),
            and_(
                OntologyProject.current_release_id.is_(None),
                OntologyProject.status == "published",
            ),
        ),
    ).all()
    due = []
    for s in sentinels:
        interval = timedelta(seconds=max(s.scan_interval_seconds or 300, 5))
        last = s.last_scanned_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or (now - last) >= interval:
            due.append(s)
    firings = []
    for s in due:
        firings.append(evaluate_sentinel(db, s.ontology_id, s, "schedule"))
        s.last_scanned_at = now
    db.commit()
    return _summary(firings)
