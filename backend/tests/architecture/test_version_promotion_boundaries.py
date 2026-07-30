"""Protect atomic promotion extraction and legacy patch compatibility."""

from __future__ import annotations

import ast
from contextlib import contextmanager
import inspect
from pathlib import Path

from app.ontologies.formal_modeling import derived, facts
from app.ontologies.mappings import mapping_service
from app.ontologies.versions import promotion_service
from app.ontologies.versions import router as version_router


_ROUTER_DEPENDENCIES = (
    "settings",
    "complete_snapshot",
    "impact_report",
    "snapshot_hash",
    "validate_builtin_sentinel_contract",
    "validate_manual_mapping_trial_contract",
    "validate_release_mapping_contract",
    "_current_release",
    "_diff_formal",
    "_dynamic_sentinel_id_conflict_errors",
    "_invalidate_dynamic_sentinels_for_release",
    "_json_safe",
    "_next_release_activation_number",
    "_raise_publish_errors",
    "_rebuild_required_query_projections",
    "_release_errors",
    "_restore_formal_snapshot",
    "_runtime_state_conflicts",
    "_snapshot_formal",
    "_verify_trial_dataset_pins",
    "_version_payload",
)


def _functions(path: Path) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def test_router_uses_canonical_promotion_service_by_identity():
    assert version_router.promotion_service is promotion_service
    assert promotion_service.promote_draft.__module__ == (
        "app.ontologies.versions.promotion_service"
    )
    assert promotion_service._promote_draft_locked.__module__ == (
        "app.ontologies.versions.promotion_service"
    )


def test_promotion_service_never_imports_versions_router():
    service_path = Path(promotion_service.__file__).resolve()
    tree = ast.parse(
        service_path.read_text(encoding="utf-8"),
        filename=str(service_path),
    )
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    assert "app.ontologies.versions.router" not in imported


def test_promotion_http_endpoint_is_a_thin_named_adapter():
    router_path = Path(version_router.__file__).resolve()
    node = _functions(router_path)["promote_draft"]
    executable = [
        statement
        for statement in node.body
        if not isinstance(statement, ast.ImportFrom)
    ]

    assert version_router.promote_draft.__name__ == "promote_draft"
    assert len(executable) == 1
    assert isinstance(executable[0], ast.Return)
    call = executable[0].value
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Attribute)
    assert isinstance(call.func.value, ast.Name)
    assert call.func.value.id == "promotion_service"
    assert call.func.attr == "promote_draft"


def test_locked_compatibility_adapter_keeps_original_signature_and_is_thin():
    assert list(inspect.signature(
        version_router._promote_draft_locked,
    ).parameters) == [
        "ontology_id",
        "version_id",
        "body",
        "db",
        "current_user",
    ]

    router_path = Path(version_router.__file__).resolve()
    node = _functions(router_path)["_promote_draft_locked"]
    executable = [
        statement
        for statement in node.body
        if not isinstance(statement, ast.ImportFrom)
    ]
    assert len(executable) == 1
    assert isinstance(executable[0], ast.Return)
    call = executable[0].value
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Attribute)
    assert isinstance(call.func.value, ast.Name)
    assert call.func.value.id == "promotion_service"
    assert call.func.attr == "_promote_draft_locked"
    injected = {keyword.arg for keyword in call.keywords}
    service_parameters = inspect.signature(
        promotion_service._promote_draft_locked,
    ).parameters
    required_injections = set(service_parameters) - {
        "ontology_id",
        "version_id",
        "body",
        "db",
        "current_user",
    }
    assert injected == required_injections


def test_promote_adapter_forwards_current_lock_and_locked_patch(monkeypatch):
    lock_marker = object()
    locked_marker = object()
    captured: dict = {}

    def fake_promote(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"data": "patched"}

    monkeypatch.setattr(
        mapping_service,
        "_ontology_build_lock",
        lock_marker,
    )
    monkeypatch.setattr(
        version_router,
        "_promote_draft_locked",
        locked_marker,
    )
    monkeypatch.setattr(promotion_service, "promote_draft", fake_promote)

    result = version_router.promote_draft(
        "ontology-id",
        "version-id",
        {},
        object(),
        object(),
    )

    assert result == {"data": "patched"}
    assert captured["kwargs"]["_ontology_build_lock"] is lock_marker
    assert captured["kwargs"]["_promote_draft_locked"] is locked_marker


def test_service_keeps_projection_lock_around_legacy_locked_call():
    events: list[str] = []

    @contextmanager
    def fake_lock(db, ontology_id):
        events.append(f"lock-enter:{id(db)}:{ontology_id}")
        try:
            yield
        finally:
            events.append("lock-exit")

    def fake_locked(ontology_id, version_id, body, db, current_user):
        events.append(
            f"locked:{ontology_id}:{version_id}:{id(body)}:"
            f"{id(db)}:{id(current_user)}"
        )
        return {"data": "locked"}

    body = {}
    db = object()
    current_user = object()
    result = promotion_service.promote_draft(
        "ontology-id",
        "version-id",
        body,
        db,
        current_user,
        _ontology_build_lock=fake_lock,
        _promote_draft_locked=fake_locked,
    )

    assert result == {"data": "locked"}
    assert events == [
        f"lock-enter:{id(db)}:ontology-id",
        (
            f"locked:ontology-id:version-id:{id(body)}:"
            f"{id(db)}:{id(current_user)}"
        ),
        "lock-exit",
    ]


def test_locked_adapter_forwards_every_current_dependency(monkeypatch):
    captured: dict = {}
    markers = {name: object() for name in _ROUTER_DEPENDENCIES}
    fact_markers = {
        "record_link_fact": object(),
        "record_object_presence": object(),
        "record_object_tombstone": object(),
        "record_property_facts": object(),
    }
    derived_marker = object()

    def fake_locked(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"data": "injected"}

    for name, marker in markers.items():
        monkeypatch.setattr(version_router, name, marker)
    for name, marker in fact_markers.items():
        monkeypatch.setattr(facts, name, marker)
    monkeypatch.setattr(
        derived,
        "recompute_instance_derived",
        derived_marker,
    )
    monkeypatch.setattr(
        promotion_service,
        "_promote_draft_locked",
        fake_locked,
    )

    positional = (
        "ontology-id",
        "version-id",
        {},
        object(),
        object(),
    )
    result = version_router._promote_draft_locked(*positional)

    assert result == {"data": "injected"}
    assert captured["args"] == positional
    for name, marker in markers.items():
        assert captured["kwargs"][name] is marker
    for name, marker in fact_markers.items():
        assert captured["kwargs"][name] is marker
    assert captured["kwargs"]["recompute_instance_derived"] is derived_marker
