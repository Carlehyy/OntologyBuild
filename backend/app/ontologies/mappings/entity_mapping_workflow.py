"""Application workflows for object/entity mappings.

These functions own transaction ordering and mapping use-case orchestration.
The router supplies validation callbacks at request time so legacy
``router._helper`` monkeypatch points remain effective during the migration.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session


Rule = Callable[..., Any]


def _projection_locked_writer(func):
    @wraps(func)
    def wrapped(db: Session, ontology_id: str, *args, **kwargs):
        from app.ontologies.runtime_fence import _ontology_build_lock

        with _ontology_build_lock(db, ontology_id):
            return func(db, ontology_id, *args, **kwargs)

    return wrapped


@dataclass(frozen=True)
class EntityMappingRules:
    require_draft_ontology: Rule
    lock_ontology: Rule
    validate_target_type: Rule
    reject_reserved_mapping_keys: Rule
    canonical_primary_key: Rule
    assert_client_primary_key_matches: Rule
    validate_user_field_mapping: Rule
    assert_ignored_fields_do_not_hide_identity: Rule
    assert_mapping_types_compatible: Rule
    validate_version_automation_policy: Rule


def create_mapping(
    db: Session,
    ontology_id: str,
    body: Any,
    *,
    rules: EntityMappingRules,
) -> dict:
    from app.ontologies.mappings.models import OntologyMapping
    from app.ontologies.mappings.mapping_service import MappingService

    rules.require_draft_ontology(db, ontology_id)
    target_type = rules.validate_target_type(
        db,
        ontology_id,
        body.target_object_type_id,
    )
    rules.reject_reserved_mapping_keys(body.field_mapping, "field_mapping")

    declared_pk = rules.canonical_primary_key(
        db,
        body.curated_dataset_id,
    )
    rules.assert_client_primary_key_matches(
        body.primary_key_column,
        declared_pk,
        body.curated_dataset_id,
    )
    rules.validate_user_field_mapping(
        body.field_mapping or {},
        body.ignored_fields,
    )
    rules.assert_ignored_fields_do_not_hide_identity(
        body.ignored_fields,
        declared_pk,
    )
    rules.assert_mapping_types_compatible(
        db,
        body.curated_dataset_id,
        target_type,
        body.field_mapping or {},
    )

    service = MappingService(db)
    field_mapping = dict(body.field_mapping or {})
    if body.ignored_fields:
        field_mapping["__ignored_fields__"] = sorted(
            set(body.ignored_fields)
        )
    if body.property_mappings:
        field_mapping["__properties__"] = body.property_mappings
    if body.auto_apply_on_review:
        field_mapping["__auto_apply_on_review__"] = True
    if body.auto_apply_on_version:
        rules.validate_version_automation_policy(
            db,
            body.curated_dataset_id,
        )
        field_mapping["__auto_apply_on_version__"] = True
    client_definition = {
        "entity_class": body.entity_class,
        "field_mapping": dict(body.field_mapping or {}),
        "ignored_fields": sorted(set(body.ignored_fields)),
        "auto_apply_on_review": bool(body.auto_apply_on_review),
        "auto_apply_on_version": bool(body.auto_apply_on_version),
        "target_object_type_id": body.target_object_type_id,
    }
    field_mapping["__client_definition__"] = client_definition

    identity_query = db.query(OntologyMapping).filter(
        OntologyMapping.ontology_id == ontology_id,
        OntologyMapping.curated_dataset_id == body.curated_dataset_id,
    )
    identity_query = (
        identity_query.filter(
            OntologyMapping.target_object_type_id
            == body.target_object_type_id
        )
        if body.target_object_type_id
        else identity_query.filter(
            OntologyMapping.target_object_type_id.is_(None),
            OntologyMapping.entity_class == body.entity_class,
        )
    )
    existing = identity_query.first()
    if existing is not None:
        existing_map = dict(existing.field_mapping or {})
        existing_user = {
            key: value
            for key, value in existing_map.items()
            if not str(key).startswith("__")
        }
        candidate_user = {
            key: value
            for key, value in field_mapping.items()
            if not str(key).startswith("__")
        }
        same_definition = (
            existing_map.get("__client_definition__") == client_definition
        ) or (
            existing.entity_class == body.entity_class
            and existing_user == candidate_user
            and sorted(existing_map.get("__ignored_fields__") or [])
            == sorted(field_mapping.get("__ignored_fields__") or [])
            and bool(existing_map.get("__auto_apply_on_review__"))
            == bool(field_mapping.get("__auto_apply_on_review__"))
            and bool(existing_map.get("__auto_apply_on_version__"))
            == bool(field_mapping.get("__auto_apply_on_version__"))
        )
        if same_definition:
            return {
                "mapping_id": existing.id,
                "status": existing.status,
                "idempotent_replay": True,
            }
        raise HTTPException(
            409,
            detail={
                "code": "object_mapping_already_exists",
                "message": (
                    "该数据集到目标对象的映射已存在；请维护现有映射，不要重复创建。"
                ),
                "mapping_id": existing.id,
            },
        )
    mapping = service.create_mapping(
        ontology_id=ontology_id,
        curated_dataset_id=body.curated_dataset_id,
        entity_class=body.entity_class,
        field_mapping=field_mapping,
        primary_key_column=declared_pk,
        confidence=body.confidence,
        target_object_type_id=body.target_object_type_id,
    )
    return {
        "mapping_id": mapping.id,
        "status": mapping.status,
    }


def update_mapping(
    db: Session,
    ontology_id: str,
    mapping_id: str,
    body: Any,
    *,
    rules: EntityMappingRules,
) -> dict:
    """映射维护：结构和版本化自动触发策略均通过 draft 发布。"""
    from app.ontologies.mappings.models import OntologyMapping

    provided = body.model_fields_set
    structural_fields = {
        "entity_class",
        "field_mapping",
        "ignored_fields",
        "primary_key_column",
        "target_object_type_id",
    }
    locked_project = None
    if provided & structural_fields:
        rules.require_draft_ontology(db, ontology_id)
    else:
        locked_project = rules.lock_ontology(db, ontology_id)

    mapping = (
        db.query(OntologyMapping)
        .filter(
            OntologyMapping.id == mapping_id,
            OntologyMapping.ontology_id == ontology_id,
        )
        .first()
    )
    if not mapping:
        raise HTTPException(404, "Mapping not found")
    policy_fields = {
        "auto_apply_on_review",
        "auto_apply_on_version",
    } & provided
    if locked_project is not None and locked_project.current_release_id:
        current_policy = {
            "auto_apply_on_review": bool(
                (mapping.field_mapping or {}).get(
                    "__auto_apply_on_review__"
                )
            ),
            "auto_apply_on_version": bool(
                (mapping.field_mapping or {}).get(
                    "__auto_apply_on_version__"
                )
            ),
        }
        requested_policy = {
            key: getattr(body, key)
            for key in policy_fields
            if getattr(body, key) is not None
        }
        changed_fields = sorted(
            key
            for key, value in requested_policy.items()
            if bool(value) != current_policy[key]
        )
        if changed_fields:
            raise HTTPException(
                409,
                detail={
                    "code": "mapping_policy_requires_versioned_draft",
                    "message": (
                        "当前发布映射的自动触发策略属于版本化行为，不能直接修改。"
                        "请新建本体草稿，完成试跑后再发布。"
                    ),
                    "fields": changed_fields,
                    "current_release_id": locked_project.current_release_id,
                },
            )
        # A repeated live request with exactly the released value is a true
        # no-op. Returning before client-definition bookkeeping prevents an
        # idempotent call from drifting away from the immutable release scope.
        return {
            "mapping_id": mapping.id,
            "status": mapping.status,
            "target_object_type_id": mapping.target_object_type_id,
            **current_policy,
            "idempotent_replay": True,
        }

    declared_pk = rules.canonical_primary_key(
        db,
        mapping.curated_dataset_id,
    )
    rules.assert_client_primary_key_matches(
        body.primary_key_column,
        declared_pk,
        mapping.curated_dataset_id,
    )
    candidate_target_type_id = (
        body.target_object_type_id
        if "target_object_type_id" in provided
        else mapping.target_object_type_id
    )
    target_type = rules.validate_target_type(
        db,
        ontology_id,
        candidate_target_type_id,
    )
    field_mapping = dict(mapping.field_mapping or {})
    previous_pk = field_mapping.get("__primary_key__")
    if body.field_mapping is not None:
        rules.reject_reserved_mapping_keys(
            body.field_mapping,
            "field_mapping",
        )
        # Preserve runtime-owned keys; user payloads containing them are
        # rejected.
        system_keys = {
            key: value
            for key, value in field_mapping.items()
            if key.startswith("__")
        }
        field_mapping = {
            **system_keys,
            **body.field_mapping,
        }
    effective_user_mapping = (
        body.field_mapping
        if body.field_mapping is not None
        else {
            key: value
            for key, value in field_mapping.items()
            if not str(key).startswith("__")
        }
    )
    effective_ignored = (
        body.ignored_fields
        if body.ignored_fields is not None
        else list(field_mapping.get("__ignored_fields__") or [])
    )
    rules.validate_user_field_mapping(
        effective_user_mapping,
        effective_ignored,
    )
    rules.assert_ignored_fields_do_not_hide_identity(
        effective_ignored,
        declared_pk,
    )
    rules.assert_mapping_types_compatible(
        db,
        mapping.curated_dataset_id,
        target_type,
        effective_user_mapping,
    )
    mapping.target_object_type_id = candidate_target_type_id
    if body.entity_class is not None:
        mapping.entity_class = body.entity_class
    if body.ignored_fields is not None:
        if effective_ignored:
            field_mapping["__ignored_fields__"] = sorted(
                set(effective_ignored)
            )
        else:
            field_mapping.pop("__ignored_fields__", None)

    # Always repair/read the canonical lake contract. ``primary_key_column`` is
    # retained in the request schema only for old clients and acts as an assert.
    field_mapping["__primary_key__"] = declared_pk
    field_mapping["__pk_source__"] = "lake"
    if body.auto_apply_on_review is not None:
        if body.auto_apply_on_review:
            field_mapping["__auto_apply_on_review__"] = True
        else:
            field_mapping.pop("__auto_apply_on_review__", None)
    if body.auto_apply_on_version is not None:
        if body.auto_apply_on_version:
            rules.validate_version_automation_policy(
                db,
                mapping.curated_dataset_id,
            )
            field_mapping["__auto_apply_on_version__"] = True
        else:
            field_mapping.pop("__auto_apply_on_version__", None)
    if {
        "entity_class",
        "field_mapping",
        "ignored_fields",
        "target_object_type_id",
        "auto_apply_on_review",
        "auto_apply_on_version",
    } & provided:
        field_mapping["__client_definition__"] = {
            "entity_class": mapping.entity_class,
            "field_mapping": {
                key: value
                for key, value in field_mapping.items()
                if not str(key).startswith("__")
            },
            "ignored_fields": sorted(
                field_mapping.get("__ignored_fields__") or []
            ),
            "auto_apply_on_review": bool(
                field_mapping.get("__auto_apply_on_review__")
            ),
            "auto_apply_on_version": bool(
                field_mapping.get("__auto_apply_on_version__")
            ),
            "target_object_type_id": mapping.target_object_type_id,
        }
    projection_changed = bool(
        {
            "entity_class",
            "field_mapping",
            "ignored_fields",
            "primary_key_column",
            "target_object_type_id",
        }
        & provided
    ) or previous_pk != declared_pk
    if projection_changed:
        # Any definition change invalidates the previous apply attestation. The
        # old marker must never be reused by the release gate for new semantics.
        for key in list(field_mapping):
            if key.startswith("__applied_") or key == "__last_apply_error__":
                field_mapping.pop(key, None)
        mapping.status = "draft"
    mapping.field_mapping = field_mapping
    db.commit()
    db.refresh(mapping)
    return {
        "mapping_id": mapping.id,
        "status": mapping.status,
        "target_object_type_id": mapping.target_object_type_id,
        "auto_apply_on_review": bool(
            field_mapping.get("__auto_apply_on_review__")
        ),
        "auto_apply_on_version": bool(
            field_mapping.get("__auto_apply_on_version__")
        ),
    }


@_projection_locked_writer
def delete_mapping(
    db: Session,
    ontology_id: str,
    mapping_id: str,
    *,
    rules: EntityMappingRules,
) -> None:
    """删除映射并撤销其当前态投影；不可变事实历史通过墓碑保留。"""
    from app.ontologies.mappings.models import OntologyMapping
    from app.ontologies.mappings.mapping_service import (
        MappingApplyError,
        MappingService,
    )

    rules.require_draft_ontology(db, ontology_id)
    mapping = (
        db.query(OntologyMapping)
        .filter(
            OntologyMapping.id == mapping_id,
            OntologyMapping.ontology_id == ontology_id,
        )
        .first()
    )
    if not mapping:
        raise HTTPException(404, "Mapping not found")
    service = MappingService(db)
    try:
        stale_ids = service.remove_mapping_projection(mapping)
        db.delete(mapping)
        from app.ontologies.projection_state import mark_projecting
        mark_projecting(db, ontology_id)
        db.commit()
    except MappingApplyError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    # Neo4j is reconciled only through a validated full rebuild. A partial
    # node delete could otherwise expose a half-updated graph.
    del stale_ids
    from app.ontologies.projection_state import (
        ProjectionRebuildError,
        rebuild_after_commit,
    )
    try:
        rebuild_after_commit(db, ontology_id)
    except ProjectionRebuildError as exc:
        raise HTTPException(503, detail={
            "code": "ontology_projection_failed",
            "message": (
                "Mapping 已从关系型真相删除，但 Neo4j 对账失败；"
                "图读取已阻断，请执行图修复"
            ),
            "ontology_id": ontology_id,
        }) from exc


def reject_raw_apply(
    db: Session,
    ontology_id: str,
    mapping_id: str,
    data: list[dict],
) -> None:
    """Reject data that bypasses the versioned dataset source contract."""
    from app.ontologies.mappings.models import OntologyMapping

    # Keep the request parameter in the workflow contract even though the
    # bypass is intentionally rejected after ownership validation.
    del data
    mapping = (
        db.query(OntologyMapping)
        .filter(
            OntologyMapping.id == mapping_id,
            OntologyMapping.ontology_id == ontology_id,
        )
        .first()
    )
    if not mapping:
        raise HTTPException(404, "Mapping not found in this ontology")
    raise HTTPException(
        409,
        "禁止直接提交原始数据执行映射；请使用 apply-from-dataset，"
        "由服务端读取已绑定且通过校验的数据版本",
    )


def apply_mapping_from_dataset(
    db: Session,
    ontology_id: str,
    mapping_id: str,
) -> dict:
    from app.ontologies.mappings.models import OntologyMapping
    from app.ontologies.mappings.mapping_service import (
        MappingApplyError,
        MappingReleaseScopeError,
        MappingService,
        MappingSourceError,
    )

    mapping = (
        db.query(OntologyMapping)
        .filter(
            OntologyMapping.id == mapping_id,
            OntologyMapping.ontology_id == ontology_id,
        )
        .first()
    )
    if not mapping:
        raise HTTPException(404, "Mapping not found")
    if not mapping.curated_dataset_id:
        raise HTTPException(400, "Mapping has no curated_dataset_id")

    service = MappingService(db)
    try:
        result = service.build_all(ontology_id, require_approved=True)
        result["trigger_mapping_id"] = mapping_id
        return result
    except MappingSourceError as exc:
        raise HTTPException(422, str(exc))
    except MappingReleaseScopeError as exc:
        raise HTTPException(
            409,
            detail={
                "code": "mapping_not_in_current_release",
                "message": str(exc),
            },
        )
    except MappingApplyError as exc:
        raise HTTPException(500, str(exc))


def build_all_mappings(db: Session, ontology_id: str) -> dict:
    from app.ontologies.mappings.models import OntologyLinkMapping
    from app.ontologies.mappings.mapping_service import (
        MappingApplyError,
        MappingReleaseScopeError,
        MappingService,
        MappingSourceError,
    )

    service = MappingService(db)
    try:
        result = service.build_all(ontology_id, require_approved=True)
        active_links = (
            db.query(OntologyLinkMapping)
            .filter(
                OntologyLinkMapping.ontology_id == ontology_id,
                OntologyLinkMapping.status == "active",
            )
            .count()
        )
        inferred_links = (
            db.query(OntologyLinkMapping)
            .filter(
                OntologyLinkMapping.ontology_id == ontology_id,
                OntologyLinkMapping.status == "inferred",
            )
            .count()
        )
        result["link_mappings_configured"] = active_links
        result["link_mappings_inferred"] = inferred_links
        return result
    except MappingSourceError as exc:
        raise HTTPException(422, detail=str(exc))
    except MappingReleaseScopeError as exc:
        raise HTTPException(
            409,
            detail={
                "code": "mapping_not_in_current_release",
                "message": str(exc),
            },
        )
    except MappingApplyError as exc:
        raise HTTPException(500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))
