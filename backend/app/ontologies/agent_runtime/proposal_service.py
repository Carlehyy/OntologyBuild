"""Governed execution workflow for user-confirmed assistant proposals."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.ontologies.agent_runtime import schemas as S
from app.ontologies.agent_runtime.boundary import ToolError


def authorize(
    db: Session,
    ontology_id: str,
    body: S.ExecuteProposalRequest,
    *,
    current_release_fn: Callable[..., Any],
    build_scope_fn: Callable[..., tuple[Any, Any, Any]],
):
    release = current_release_fn(
        db,
        ontology_id,
        expected_release_id=body.release_id,
    )
    _, profile, scope = build_scope_fn(
        db,
        ontology_id,
        release_id=release.id,
    )
    if not profile.enabled:
        raise ToolError("该本体的智能体已停用")
    action = scope.require_action(body.action_id)
    if body.target_instance_id:
        from app.models.ontology_formal import ObjectInstance

        target = db.query(ObjectInstance).filter(
            ObjectInstance.id == body.target_instance_id,
        ).first()
        scope.visible_instance(target)
    return release, action


def execute(
    db: Session,
    ontology_id: str,
    body: S.ExecuteProposalRequest,
    current_user: Any,
    release: Any,
    action: Any,
):
    from app.ontologies.formal_modeling.schemas import RunActionRequest
    from app.services.formal.action_engine import execute_action

    return execute_action(
        db,
        ontology_id,
        RunActionRequest(
            action_id=action.id,
            parameters=body.parameters,
            target_instance_id=body.target_instance_id,
            dry_run=False,
            release_id=release.id,
        ),
        actor_id=getattr(current_user, "id", None),
        expected_release_id=release.id,
    )
