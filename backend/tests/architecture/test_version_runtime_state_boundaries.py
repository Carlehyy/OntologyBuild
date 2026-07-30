"""Protect the versions runtime-state service extraction boundary."""

from __future__ import annotations

import ast
from pathlib import Path

from app.ontologies.versions import gate_contract
from app.ontologies.versions import router as version_router
from app.ontologies.versions import runtime_state_service


_RUNTIME_STATE_EXPORTS = (
    "_RUNTIME_FACT_QUERY_CHUNK",
    "_RUNTIME_FACT_QUERY_POSTGRES_CHUNK",
    "_RUNTIME_STATE_ACCESS_TOKEN",
    "_RUNTIME_STATE_CONFLICT_LIMIT",
    "_RUNTIME_STATE_INLINE_SECRET",
    "_RUNTIME_STATE_JWT",
    "_RUNTIME_STATE_MASK",
    "_RUNTIME_STATE_SENSITIVE_FIELD",
    "_dynamic_sentinel_id_conflict_errors",
    "_empty_runtime_state_conflicts",
    "_is_lake_projection_fact_source",
    "_redact_runtime_state_value",
    "_release_ancestor_context",
    "_release_readiness",
    "_runtime_coordinate_facts",
    "_runtime_existence_facts",
    "_runtime_fact_chunks",
    "_runtime_fact_query_chunk_size",
    "_runtime_latest_by_scope",
    "_runtime_state_conflicts",
    "_safe_runtime_fact_source",
    "_verify_trial_dataset_pins",
)


def test_versions_router_reexports_runtime_state_objects_by_identity():
    for name in _RUNTIME_STATE_EXPORTS:
        assert getattr(version_router, name) is getattr(
            runtime_state_service,
            name,
        )
    assert version_router._gate_error is gate_contract.gate_error


def test_versions_runtime_services_never_import_the_router():
    versions_dir = Path(runtime_state_service.__file__).resolve().parent
    forbidden = "app.ontologies.versions.router"
    violations: list[str] = []

    for path in sorted(versions_dir.glob("*service.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if forbidden in modules:
                violations.append(
                    f"{path.name}:{node.lineno} imports {forbidden}",
                )

    assert not violations, "\n".join(violations)
