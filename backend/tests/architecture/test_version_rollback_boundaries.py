"""Protect rollback extraction, atomic locking, and legacy patch paths."""

from __future__ import annotations

import ast
from contextlib import contextmanager
import inspect
from pathlib import Path

from app.ontologies.formal_modeling import derived
from app.ontologies.mappings import mapping_service
from app.ontologies.versions import rollback_service
from app.ontologies.versions import router as version_router


_LOCKED_ROUTER_DEPENDENCIES = (
    "settings",
    "snapshot_hash",
    "_current_release",
    "_diff_formal",
    "_gate_error",
    "_invalidate_dynamic_sentinels_for_release",
    "_json_safe",
    "_next_release_activation_number",
    "_rebuild_required_query_projections",
    "_release_errors",
    "_restore_formal_snapshot",
    "_snapshot_formal",
    "_version_payload",
)


def _functions(path: Path) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def _single_return_call(
    node: ast.FunctionDef,
    *,
    module: str,
    function: str,
) -> ast.Call:
    executable = [
        statement
        for statement in node.body
        if not (
            isinstance(statement, ast.ImportFrom)
            or (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            )
        )
    ]
    assert len(executable) == 1
    assert isinstance(executable[0], ast.Return)
    call = executable[0].value
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Attribute)
    assert isinstance(call.func.value, ast.Name)
    assert call.func.value.id == module
    assert call.func.attr == function
    return call


def test_router_uses_canonical_rollback_service_by_identity():
    assert version_router.rollback_service is rollback_service
    for name in (
        "_restore_formal_snapshot",
        "rollback_version",
        "_rollback_version_locked",
    ):
        assert getattr(rollback_service, name).__module__ == (
            "app.ontologies.versions.rollback_service"
        )


def test_rollback_service_never_imports_versions_router_or_compat_models():
    service_path = Path(rollback_service.__file__).resolve()
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
    assert all(not module.startswith("app.models") for module in imported)


def test_restore_compatibility_adapter_keeps_signature_and_is_thin():
    assert list(inspect.signature(
        version_router._restore_formal_snapshot,
    ).parameters) == ["db", "ontology_id", "snap"]

    router_path = Path(version_router.__file__).resolve()
    call = _single_return_call(
        _functions(router_path)["_restore_formal_snapshot"],
        module="rollback_service",
        function="_restore_formal_snapshot",
    )
    injected = {keyword.arg for keyword in call.keywords}
    service_parameters = inspect.signature(
        rollback_service._restore_formal_snapshot,
    ).parameters
    assert injected == set(service_parameters) - {
        "db",
        "ontology_id",
        "snap",
    }


def test_rollback_http_endpoint_is_a_thin_named_adapter():
    router_path = Path(version_router.__file__).resolve()
    _single_return_call(
        _functions(router_path)["rollback_version"],
        module="rollback_service",
        function="rollback_version",
    )
    assert version_router.rollback_version.__name__ == "rollback_version"


def test_locked_compatibility_adapter_keeps_signature_and_is_thin():
    assert list(inspect.signature(
        version_router._rollback_version_locked,
    ).parameters) == [
        "ontology_id",
        "version_id",
        "db",
        "current_user",
    ]

    router_path = Path(version_router.__file__).resolve()
    call = _single_return_call(
        _functions(router_path)["_rollback_version_locked"],
        module="rollback_service",
        function="_rollback_version_locked",
    )
    injected = {keyword.arg for keyword in call.keywords}
    service_parameters = inspect.signature(
        rollback_service._rollback_version_locked,
    ).parameters
    assert injected == set(service_parameters) - {
        "ontology_id",
        "version_id",
        "db",
        "current_user",
    }


def test_rollback_adapter_forwards_current_lock_and_locked_patch(monkeypatch):
    lock_marker = object()
    locked_marker = object()
    captured: dict = {}

    def fake_rollback(*args, **kwargs):
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
        "_rollback_version_locked",
        locked_marker,
    )
    monkeypatch.setattr(
        rollback_service,
        "rollback_version",
        fake_rollback,
    )

    result = version_router.rollback_version(
        "ontology-id",
        "version-id",
        object(),
        object(),
    )

    assert result == {"data": "patched"}
    assert captured["kwargs"]["_ontology_build_lock"] is lock_marker
    assert captured["kwargs"]["_rollback_version_locked"] is locked_marker


def test_service_keeps_projection_lock_around_legacy_locked_call():
    events: list[str] = []

    @contextmanager
    def fake_lock(db, ontology_id):
        events.append(f"lock-enter:{id(db)}:{ontology_id}")
        try:
            yield
        finally:
            events.append("lock-exit")

    def fake_locked(ontology_id, version_id, db, current_user):
        events.append(
            f"locked:{ontology_id}:{version_id}:"
            f"{id(db)}:{id(current_user)}"
        )
        return {"data": "locked"}

    db = object()
    current_user = object()
    result = rollback_service.rollback_version(
        "ontology-id",
        "version-id",
        db,
        current_user,
        _ontology_build_lock=fake_lock,
        _rollback_version_locked=fake_locked,
    )

    assert result == {"data": "locked"}
    assert events == [
        f"lock-enter:{id(db)}:ontology-id",
        (
            f"locked:ontology-id:version-id:"
            f"{id(db)}:{id(current_user)}"
        ),
        "lock-exit",
    ]


def test_restore_adapter_forwards_current_schema_and_json_patch(monkeypatch):
    schema_marker = object()
    json_marker = object()
    captured: dict = {}

    def fake_restore(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"restored": True}

    monkeypatch.setattr(version_router, "FS", schema_marker)
    monkeypatch.setattr(version_router, "_json_safe", json_marker)
    monkeypatch.setattr(
        rollback_service,
        "_restore_formal_snapshot",
        fake_restore,
    )
    positional = (object(), "ontology-id", {})

    result = version_router._restore_formal_snapshot(*positional)

    assert result == {"restored": True}
    assert captured["args"] == positional
    assert captured["kwargs"] == {
        "FS": schema_marker,
        "_json_safe": json_marker,
    }


def test_locked_adapter_forwards_every_current_dependency(monkeypatch):
    captured: dict = {}
    markers = {
        name: object()
        for name in _LOCKED_ROUTER_DEPENDENCIES
    }
    derived_marker = object()

    def fake_locked(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"data": "injected"}

    for name, marker in markers.items():
        monkeypatch.setattr(version_router, name, marker)
    monkeypatch.setattr(
        derived,
        "recompute_instance_derived",
        derived_marker,
    )
    monkeypatch.setattr(
        rollback_service,
        "_rollback_version_locked",
        fake_locked,
    )

    positional = (
        "ontology-id",
        "version-id",
        object(),
        object(),
    )
    result = version_router._rollback_version_locked(*positional)

    assert result == {"data": "injected"}
    assert captured["args"] == positional
    for name, marker in markers.items():
        assert captured["kwargs"][name] is marker
    assert captured["kwargs"]["recompute_instance_derived"] is derived_marker
