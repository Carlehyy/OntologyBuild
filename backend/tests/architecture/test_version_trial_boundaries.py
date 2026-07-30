"""Protect trial single-flight service extraction and patch compatibility."""

from __future__ import annotations

import ast
from pathlib import Path

from app.ontologies.versions import router as version_router
from app.ontologies.versions import trial_service


_TRIAL_EXPORTS = (
    "_active_trial_run",
    "_as_utc",
    "_finalize_trial_candidate",
    "_load_trial_after_claim_loss",
    "_raise_trial_already_running",
    "_recover_expired_trial_runs",
    "_stale_previous_trials",
    "_terminal_trial_result",
    "_terminalize_running_trial",
    "_trial_claim_lost_error",
    "_trial_lease_deadline",
    "_trial_materialization_candidate",
    "_trial_payload",
)

_TRIAL_ENDPOINTS = (
    "list_trial_runs",
    "get_trial_run",
    "create_trial_run",
)


def test_versions_router_reexports_trial_helpers_by_identity():
    for name in _TRIAL_EXPORTS:
        assert getattr(version_router, name) is getattr(trial_service, name)


def test_trial_service_never_imports_versions_router():
    service_path = Path(trial_service.__file__).resolve()
    tree = ast.parse(
        service_path.read_text(encoding="utf-8"),
        filename=str(service_path),
    )
    forbidden = "app.ontologies.versions.router"
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    assert forbidden not in imported


def test_trial_http_endpoints_remain_thin_named_adapters():
    router_path = Path(version_router.__file__).resolve()
    tree = ast.parse(
        router_path.read_text(encoding="utf-8"),
        filename=str(router_path),
    )
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }

    for name in _TRIAL_ENDPOINTS:
        endpoint = getattr(version_router, name)
        assert endpoint.__name__ == name
        node = functions[name]
        assert len(node.body) == 1, name
        assert isinstance(node.body[0], ast.Return), name
        call = node.body[0].value
        assert isinstance(call, ast.Call), name
        assert isinstance(call.func, ast.Attribute), name
        assert isinstance(call.func.value, ast.Name), name
        assert call.func.value.id == "trial_service", name
        assert call.func.attr == name, name


def test_create_trial_adapter_forwards_current_materializer(monkeypatch):
    marker = object()
    captured: dict = {}

    def fake_create(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"data": "patched"}

    monkeypatch.setattr(version_router, "materialize_trial", marker)
    monkeypatch.setattr(trial_service, "create_trial_run", fake_create)

    result = version_router.create_trial_run(
        "ontology-id",
        "version-id",
        {},
        object(),
        object(),
    )

    assert result == {"data": "patched"}
    assert captured["kwargs"]["materialize_trial"] is marker
