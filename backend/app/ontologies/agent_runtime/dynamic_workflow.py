"""Application orchestration for assistant-managed dynamic Sentinels."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.ontologies.agent_runtime import schemas as S


def context_scope(
    db: Session,
    ontology_id: str,
    release_id: str,
    *,
    dynamic_service_module: Any,
    build_scope_fn: Callable[..., tuple[Any, Any, Any]],
):
    context = dynamic_service_module.require_current_release(
        db,
        ontology_id,
        release_id,
    )
    _, _, scope = build_scope_fn(
        db,
        ontology_id,
        release_id=context.id,
    )
    return context, scope


def execute_proposal(
    db: Session,
    context: Any,
    scope: Any,
    body: S.DynamicSentinelProposalCommand,
    actor_id: str | None,
    *,
    dynamic_service_module: Any,
) -> dict:
    definition = (
        body.definition.model_dump(mode="json", by_alias=True)
        if body.definition is not None
        else None
    )
    if body.operation == "create":
        row = dynamic_service_module.create_dynamic(
            db,
            context,
            scope,
            definition or {},
            actor_id,
        )
        return dynamic_service_module.serialize_dynamic(row)
    if body.operation == "update":
        row = dynamic_service_module.update_dynamic(
            db,
            context,
            scope,
            body.sentinel_id or "",
            body.expected_revision or 0,
            definition or {},
        )
        return dynamic_service_module.serialize_dynamic(row)
    if body.operation in {"enable", "disable"}:
        row = dynamic_service_module.set_enabled(
            db,
            context,
            scope,
            body.sentinel_id or "",
            body.expected_revision or 0,
            body.operation == "enable",
        )
        return dynamic_service_module.serialize_dynamic(row)
    dynamic_service_module.retire_dynamic(
        db,
        context,
        body.sentinel_id or "",
        body.expected_revision,
    )
    return {"status": "retired", "id": body.sentinel_id}


def create(
    db: Session,
    context: Any,
    scope: Any,
    body: S.DynamicSentinelCreateRequest,
    actor_id: str | None,
    *,
    dynamic_service_module: Any,
) -> dict:
    row = dynamic_service_module.create_dynamic(
        db,
        context,
        scope,
        body.definition.model_dump(mode="json", by_alias=True),
        actor_id,
    )
    return dynamic_service_module.serialize_dynamic(row)


def update(
    db: Session,
    context: Any,
    scope: Any,
    sentinel_id: str,
    body: S.DynamicSentinelUpdateRequest,
    *,
    dynamic_service_module: Any,
) -> dict:
    row = dynamic_service_module.update_dynamic(
        db,
        context,
        scope,
        sentinel_id,
        body.expected_revision,
        body.definition.model_dump(mode="json", by_alias=True),
    )
    return dynamic_service_module.serialize_dynamic(row)


def trial(
    db: Session,
    context: Any,
    scope: Any,
    sentinel_id: str,
    *,
    dynamic_service_module: Any,
) -> dict:
    row = dynamic_service_module.run_trial(
        db,
        context,
        scope,
        sentinel_id,
    )
    return dynamic_service_module.serialize_dynamic(row)


def toggle(
    db: Session,
    context: Any,
    scope: Any,
    sentinel_id: str,
    body: S.DynamicSentinelToggleRequest,
    *,
    dynamic_service_module: Any,
) -> dict:
    row = dynamic_service_module.set_enabled(
        db,
        context,
        scope,
        sentinel_id,
        body.expected_revision,
        body.enabled,
    )
    return dynamic_service_module.serialize_dynamic(row)


def retire(
    db: Session,
    context: Any,
    sentinel_id: str,
    expected_revision: int,
    *,
    dynamic_service_module: Any,
) -> dict:
    dynamic_service_module.retire_dynamic(
        db,
        context,
        sentinel_id,
        expected_revision,
    )
    return {"status": "retired", "id": sentinel_id}
