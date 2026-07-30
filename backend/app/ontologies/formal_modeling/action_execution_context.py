"""Typed state passed between the action engine's transactional phases.

The records deliberately contain data only.  They do not own a database
session or execute effects, which keeps the lock and transaction boundaries in
the canonical action engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ActionDefinitionResolution:
    start: float
    preview_only: bool
    expected_release_id: str | None
    match_state_id: str | None
    definition_context: dict | None
    action: Any
    project: Any
    ontology_version: str | None
    ontology_release_id: str | None


@dataclass(frozen=True, slots=True)
class PreparedActionExecution:
    target_snapshot: dict | None
    instance_release_id: str | None
    params: dict
    rules: list[dict]
    idempotency_key: str | None
    target_props: dict | None
    target_instance: Any


@dataclass(frozen=True, slots=True)
class ExecutedActionEffects:
    execution_log_id: str
    effects: list[dict]
    pending_links: list[dict]
    deferred_webhooks: list[tuple[int, dict, str, dict]]
    target_props: dict | None
    source: str
    causal_fact_id: str
