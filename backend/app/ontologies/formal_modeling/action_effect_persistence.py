"""Fact persistence and derived projection helpers for Action effects.

These functions deliberately do not commit or roll back.  The effect interpreter
owns the transaction and calls them at the original ordered mutation points.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.ontology_formal import ObjectInstance, PropertyFact
from app.ontologies.formal_modeling.action_runtime_values import (
    _evaluate_context_derived_projection,
    _preview_find,
)
from app.ontologies.formal_modeling.derived import (
    DerivedComputationError,
    evaluate_instance_derived_projection,
    recompute_instance_derived,
)
from app.ontologies.formal_modeling.facts import (
    fact_order_clause,
    record_property_facts,
)


def _record_and_recompute(
    db: Session,
    ontology_id: str,
    instance: ObjectInstance,
    *,
    old_props: Optional[dict],
    new_props: dict,
    source: str,
    actor_id: Optional[str],
    caused_by: str,
    ontology_version: str | None,
    ontology_release_id: str | None,
    definition_context: dict | None,
    instance_release_id: str | None,
) -> tuple[list, int]:
    """Persist input facts, recompute derived values, then flush in-order."""
    input_facts = record_property_facts(
        db,
        ontology_id=ontology_id,
        instance_id=instance.id,
        object_type_id=instance.object_type_id,
        old_props=old_props,
        new_props=new_props,
        source=source,
        actor_id=actor_id,
        caused_by=caused_by,
        ontology_version=ontology_version,
        ontology_release_id=ontology_release_id,
    )
    frozen_object_type = (
        _preview_find(
            definition_context,
            "object_types",
            instance.object_type_id,
        )
        if definition_context is not None
        else None
    )
    if definition_context is None:
        derived_count = recompute_instance_derived(
            db,
            ontology_id=ontology_id,
            instance=instance,
            trigger_facts=input_facts,
            caused_by=caused_by,
        )
    elif frozen_object_type is None:
        raise DerivedComputationError(
            f"发布快照中缺少对象类型: {instance.object_type_id}"
        )
    else:
        old_computed = dict(instance.computed or {})
        new_computed = _evaluate_context_derived_projection(
            db,
            ontology_id,
            instance,
            frozen_object_type,
            definition_context,
            instance_release_id,
        )
        trigger_ids = [fact.id for fact in input_facts if fact.id]
        derived_count = 0
        for prop in getattr(frozen_object_type, "properties", None) or []:
            if not isinstance(prop, dict) or not (
                prop.get("source") == "computed"
                or bool(prop.get("computed"))
            ):
                continue
            name = str(prop.get("name") or "")
            function_id = str(prop.get("functionId") or "").strip()
            if not name or not function_id:
                continue
            function = _preview_find(
                definition_context,
                "functions",
                function_id,
            )
            if (
                function is None
                or str(getattr(function, "language", "") or "")
                .strip()
                .lower()
                != "expression"
            ):
                continue
            last = (
                db.query(PropertyFact)
                .filter(
                    PropertyFact.ontology_id == ontology_id,
                    PropertyFact.instance_id == instance.id,
                    PropertyFact.kind == "derived",
                    PropertyFact.property_name == name,
                )
                .order_by(*fact_order_clause())
                .first()
            )
            last_value = (
                (last.value or {}).get("v")
                if last is not None
                else None
            )
            new_value = new_computed.get(name)
            if last is not None and last_value == new_value:
                continue
            record_property_facts(
                db,
                ontology_id=ontology_id,
                instance_id=instance.id,
                object_type_id=instance.object_type_id,
                old_props=(
                    {name: last_value}
                    if last is not None
                    else None
                ),
                new_props={name: new_value},
                source=f"fn:{getattr(function, 'name', function_id)}",
                caused_by=caused_by,
                kind="derived",
                derived_from=trigger_ids or None,
                ontology_version=ontology_version,
                ontology_release_id=ontology_release_id,
            )
            derived_count += 1
        if old_computed != new_computed:
            instance.computed = new_computed
    db.flush()
    return input_facts, derived_count


def _preview_derived_projection(
    db: Session,
    ontology_id: str,
    candidate,
    object_type=None,
    *,
    preview_context: dict | None,
    definition_context: dict | None,
    instance_release_id: str | None,
    dry_run_created_objects: list,
) -> dict:
    """Project derived values against the same immutable preview data view."""
    callback = (
        preview_context.get("derive")
        if preview_context is not None
        else None
    )
    if callable(callback):
        return dict(
            callback(
                candidate,
                object_type,
                [*dry_run_created_objects],
            )
            or {}
        )
    if definition_context is not None:
        return _evaluate_context_derived_projection(
            db,
            ontology_id,
            candidate,
            object_type,
            definition_context,
            instance_release_id,
        )
    return evaluate_instance_derived_projection(
        db,
        ontology_id=ontology_id,
        instance=candidate,
        object_type=object_type,
    )
