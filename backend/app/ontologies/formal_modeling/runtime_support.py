"""Shared runtime/query helpers for formal ontology capabilities.

These helpers are transport-neutral.  The legacy router re-exports their
private names while action and dashboard services consume them directly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import and_, false, or_
from sqlalchemy.orm import Session

from app.models.ontology import OntologyProject
from app.models.ontology_formal import (
    ActionExecutionLog,
    ObjectInstance,
    ObjectType,
    PropertyFact,
)
from app.models.ontology_version import OntologyVersion
from app.ontologies.release_context import current_release_context
from app.shared.time_utils import utc_iso


def _require_ontology(
    db: Session,
    ontology_id: str,
    *,
    for_update: bool = False,
) -> OntologyProject:
    query = db.query(OntologyProject).filter(OntologyProject.id == ontology_id)
    if for_update:
        query = query.with_for_update()
    project = query.first()
    if not project:
        raise HTTPException(404, "Ontology not found")
    return project


def _ok(data):
    return {"data": data}


def _raise_validation_failed(
    errors: list[dict],
    message: str = "运行契约校验未通过",
) -> None:
    if errors:
        raise HTTPException(
            422,
            detail={
                "code": "validation_failed",
                "message": f"{message}（{len(errors)} 个错误）",
                "errors": errors,
            },
        )


def _orm_view(obj, updates: Optional[dict] = None):
    """Build an ORM-shaped candidate without mutating the attached object."""
    data = {
        column.key: getattr(obj, column.key)
        for column in obj.__table__.columns
    }
    data.update(updates or {})
    return SimpleNamespace(**data)


def _naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalize persisted timestamps before comparing SQLite/Postgres rows."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _current_release_view(
    db: Session,
    project: OntologyProject,
) -> tuple[OntologyVersion, dict]:
    """Resolve the exact immutable release selected by the project pointer."""
    context = current_release_context(db, project.id)
    return context.release, context.snapshot


def _release_fact_query(
    db: Session,
    ontology_id: str,
    release: OntologyVersion,
    snapshot: dict,
):
    """Facts produced by, or after, the selected current release only."""
    object_type_ids = {
        str(item.get("id"))
        for item in snapshot["objectTypes"]
        if item.get("id")
    }
    link_type_ids = {
        str(item.get("id"))
        for item in snapshot["linkTypes"]
        if item.get("id")
    }
    sentinel_ids = {
        str(item.get("id"))
        for item in snapshot["sentinels"]
        if item.get("id")
    }
    action_ids = {
        str(item.get("id"))
        for item in snapshot["actions"]
        if item.get("id")
    }
    log_ids = (
        {
            item[0]
            for item in db.query(ActionExecutionLog.id)
            .filter(
                ActionExecutionLog.ontology_id == ontology_id,
                ActionExecutionLog.ontology_release_id == release.id,
                ActionExecutionLog.action_id.in_(action_ids),
            )
            .all()
        }
        if action_ids
        else set()
    )

    subjects = []
    schema_ids = object_type_ids | link_type_ids
    if schema_ids:
        subjects.append(PropertyFact.object_type_id.in_(schema_ids))
    if log_ids:
        subjects.append(
            and_(
                PropertyFact.kind == "decision",
                PropertyFact.instance_id.in_(log_ids),
            ),
        )
    if sentinel_ids:
        subjects.append(
            and_(
                PropertyFact.kind == "absence",
                PropertyFact.instance_id.in_(sentinel_ids),
            ),
        )

    query = db.query(PropertyFact).filter(
        PropertyFact.ontology_id == ontology_id,
        PropertyFact.ontology_release_id == release.id,
    )
    return query.filter(or_(*subjects) if subjects else false())


def _approval_instance_label(
    instance: ObjectInstance,
    object_type: Optional[ObjectType],
) -> str:
    """为审批待办选择稳定、可读的业务标签，不把整份 properties 暴露给界面。"""
    properties = (
        instance.properties
        if isinstance(instance.properties, dict)
        else {}
    )
    candidates: list[str] = []
    primary_key = getattr(object_type, "primary_key", None)
    if primary_key:
        candidates.append(primary_key)
        for prop in (getattr(object_type, "properties", None) or []):
            if not isinstance(prop, dict):
                continue
            prop_id, prop_name = prop.get("id"), prop.get("name")
            if prop_id == primary_key and prop_name:
                candidates.append(prop_name)
            elif prop_name == primary_key and prop_id:
                candidates.append(prop_id)
    candidates.extend(
        ("name", "title", "label", "code", "month", "report_month"),
    )

    value = None
    for key in dict.fromkeys(candidates):
        candidate = properties.get(key)
        if (
            candidate is not None
            and not isinstance(candidate, (dict, list))
            and str(candidate).strip()
        ):
            value = str(candidate).strip()
            break
    if value is None and instance.external_id:
        value = str(instance.external_id)
    if value is None:
        value = instance.id[:10]

    type_name = None
    if object_type is not None:
        type_name = object_type.display_name or object_type.name
    return f"{type_name} · {value}" if type_name else value


def _fact_to_dict(fact: PropertyFact) -> dict:
    return {
        "id": fact.id,
        "instanceId": fact.instance_id,
        "propertyName": fact.property_name,
        "value": (fact.value or {}).get("v"),
        "present": (fact.value or {}).get("present", True),
        "kind": fact.kind or "property",
        "source": fact.source,
        "actorId": fact.actor_id,
        "causedBy": fact.caused_by,
        "supersedesId": fact.supersedes_id,
        "derivedFrom": fact.derived_from or [],
        "confidence": fact.confidence,
        "ontologyVersion": fact.ontology_version,
        "ontologyReleaseId": fact.ontology_release_id,
        "seq": fact.seq,
        "recordedAt": utc_iso(fact.recorded_at),
    }
