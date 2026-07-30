"""Authorized graph-query workflows for the ontology assistant."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.ontologies.agent_runtime import schemas as S


def workspace_graph(
    db: Session,
    ontology_id: str,
    release_id: str | None,
    *,
    depth: int,
    query: str | None,
    object_type: str | None,
    focus_instance_id: str | None,
    limit_per_type: int,
    build_scope_fn: Callable[..., tuple[Any, Any, Any]],
    graph_fn: Callable[..., dict],
) -> dict:
    _, _, scope = build_scope_fn(
        db,
        ontology_id,
        release_id=release_id,
    )
    return graph_fn(
        scope,
        depth=depth,
        query=query,
        object_type_ref=object_type,
        focus_instance_id=focus_instance_id,
        limit_per_type=limit_per_type,
    )


def instance_detail(
    db: Session,
    ontology_id: str,
    release_id: str | None,
    instance_id: str,
    *,
    build_scope_fn: Callable[..., tuple[Any, Any, Any]],
    detail_fn: Callable[..., dict],
) -> dict:
    _, _, scope = build_scope_fn(
        db,
        ontology_id,
        release_id=release_id,
    )
    return detail_fn(scope, instance_id)


def paths(
    db: Session,
    ontology_id: str,
    body: S.GraphPathRequest,
    *,
    build_scope_fn: Callable[..., tuple[Any, Any, Any]],
    paths_fn: Callable[..., dict],
) -> dict:
    _, _, scope = build_scope_fn(
        db,
        ontology_id,
        release_id=body.release_id,
    )
    return paths_fn(
        scope,
        body.source_instance_id,
        body.target_instance_id,
        direction=body.direction,
        max_depth=body.max_depth,
        max_paths=body.max_paths,
    )


def impact(
    db: Session,
    ontology_id: str,
    body: S.GraphImpactRequest,
    *,
    build_scope_fn: Callable[..., tuple[Any, Any, Any]],
    impact_fn: Callable[..., dict],
) -> dict:
    _, _, scope = build_scope_fn(
        db,
        ontology_id,
        release_id=body.release_id,
    )
    return impact_fn(
        scope,
        body.instance_id,
        body.property,
        body.proposed_value,
        direction=body.direction,
        max_depth=body.max_depth,
    )
