"""Protect the pipeline contracts/services and thin HTTP-adapter boundary."""
from __future__ import annotations

import ast
from pathlib import Path

from app.data_channel.pipelines import (
    contracts,
    execution_service,
    management_service,
    router as pipeline_router,
    validation_service,
)


BACKEND_DIR = Path(__file__).resolve().parents[2]
ROUTER_PATH = BACKEND_DIR / "app" / "data_channel" / "pipelines" / "router.py"
CANONICAL_SERVICE_PATHS = (
    BACKEND_DIR
    / "app"
    / "data_channel"
    / "pipelines"
    / "execution_service.py",
    BACKEND_DIR
    / "app"
    / "data_channel"
    / "pipelines"
    / "validation_service.py",
    BACKEND_DIR
    / "app"
    / "data_channel"
    / "pipelines"
    / "management_service.py",
)

CONTRACT_NAMES = (
    "PipelineCreate",
    "PipelineUpdate",
    "PipelineResponse",
    "ValidateResult",
    "ValidateDefinitionsBody",
    "ValidateDefinitionsError",
    "ValidateDefinitionsResult",
    "PublishBody",
    "EnabledBody",
    "PreviewStepBody",
)

VALIDATION_HELPERS = {
    "_is_n8n_pipeline": "is_n8n_pipeline",
    "_column_definitions_hash": "column_definitions_hash",
    "_pipeline_execution_hash": "pipeline_execution_hash",
    "_current_execution_hash": "current_execution_hash",
    "_invalidate_canvas_attestation": "invalidate_canvas_attestation",
    "_require_canvas_publish_attestation": (
        "require_canvas_publish_attestation"
    ),
    "_require_production_executable": "require_production_executable",
}


def _top_level_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def _non_docstring_body(function: ast.FunctionDef) -> list[ast.stmt]:
    body = list(function.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body


def test_pipeline_router_reexports_compatibility_contracts_by_identity():
    for name in CONTRACT_NAMES:
        assert getattr(pipeline_router, name) is getattr(contracts, name)


def test_pipeline_router_reexports_helper_aliases_by_identity():
    for router_name, service_name in VALIDATION_HELPERS.items():
        assert getattr(pipeline_router, router_name) is getattr(
            validation_service,
            service_name,
        )

    assert (
        pipeline_router._dry_run_uri
        is execution_service.dry_run_uri
    )
    assert (
        pipeline_router._ensure_broker_reachable
        is execution_service.ensure_broker_reachable
    )
    assert (
        pipeline_router._format_pipeline
        is execution_service.format_pipeline
    )
    assert (
        pipeline_router._DRY_RUN_BUCKET
        == execution_service.DRY_RUN_BUCKET
    )


def test_publish_wrapper_resolves_router_validate_patch_at_call_time(
    monkeypatch,
):
    patched_validate = object()
    expected = {"id": "pipeline-1", "status": "published"}
    received = {}

    def fake_publish(
        pipeline_id,
        body,
        db,
        current_user,
        *,
        validate_pipeline_fn,
    ):
        received["args"] = (
            pipeline_id,
            body,
            db,
            current_user,
        )
        received["validate_pipeline_fn"] = validate_pipeline_fn
        return expected

    monkeypatch.setattr(
        pipeline_router,
        "validate_pipeline",
        patched_validate,
    )
    monkeypatch.setattr(
        validation_service,
        "publish_pipeline_release",
        fake_publish,
    )
    body = contracts.PublishBody(enable=True)
    database = object()
    actor = object()

    result = pipeline_router.publish_pipeline(
        "pipeline-1",
        body,
        database,
        actor,
    )

    assert result is expected
    assert received["args"] == (
        "pipeline-1",
        body,
        database,
        actor,
    )
    assert received["validate_pipeline_fn"] is patched_validate


def test_execution_wrappers_resolve_router_aliases_at_call_time(
    monkeypatch,
):
    require_executable = object()
    broker_check = object()
    task_refs = object()
    is_n8n = object()
    formatter = object()
    dry_run_uri = object()
    received = {}

    monkeypatch.setattr(
        pipeline_router,
        "_require_production_executable",
        require_executable,
    )
    monkeypatch.setattr(
        pipeline_router,
        "_ensure_broker_reachable",
        broker_check,
    )
    monkeypatch.setattr(
        pipeline_router,
        "_pipeline_task_refs",
        task_refs,
    )
    monkeypatch.setattr(
        pipeline_router,
        "_is_n8n_pipeline",
        is_n8n,
    )
    monkeypatch.setattr(
        pipeline_router,
        "_format_pipeline",
        formatter,
    )
    monkeypatch.setattr(
        pipeline_router,
        "_dry_run_uri",
        dry_run_uri,
    )
    monkeypatch.setattr(
        pipeline_router,
        "_DRY_RUN_BUCKET",
        "patched-bucket",
    )

    def fake_enqueue(pipeline_id, db, **kwargs):
        received["enqueue"] = (pipeline_id, db, kwargs)
        return "enqueued"

    def fake_sync(pipeline_id, db, **kwargs):
        received["sync"] = (pipeline_id, db, kwargs)
        return "synced"

    def fake_enabled(pipeline_id, body, db, **kwargs):
        received["enabled"] = (pipeline_id, body, db, kwargs)
        return "enabled"

    def fake_dry_run(pipeline_id, db, max_rows, **kwargs):
        received["dry_run"] = (
            pipeline_id,
            db,
            max_rows,
            kwargs,
        )
        return "previewed"

    def fake_rows(
        pipeline_id,
        dry_run_id,
        output_index,
        page,
        page_size,
        db,
        **kwargs,
    ):
        received["rows"] = (
            pipeline_id,
            dry_run_id,
            output_index,
            page,
            page_size,
            db,
            kwargs,
        )
        return "rows"

    monkeypatch.setattr(
        execution_service,
        "enqueue_pipeline_run",
        fake_enqueue,
    )
    monkeypatch.setattr(
        execution_service,
        "run_pipeline_synchronously",
        fake_sync,
    )
    monkeypatch.setattr(
        execution_service,
        "set_pipeline_enabled",
        fake_enabled,
    )
    monkeypatch.setattr(
        execution_service,
        "dry_run_pipeline",
        fake_dry_run,
    )
    monkeypatch.setattr(
        execution_service,
        "dry_run_rows",
        fake_rows,
    )

    database = object()
    enabled_body = contracts.EnabledBody(enabled=True)

    assert pipeline_router.run_pipeline("pipeline-1", database) == "enqueued"
    assert (
        pipeline_router.run_pipeline_sync("pipeline-1", database)
        == "synced"
    )
    assert (
        pipeline_router.set_pipeline_enabled(
            "pipeline-1",
            enabled_body,
            database,
        )
        == "enabled"
    )
    assert (
        pipeline_router.dry_run_pipeline(
            "pipeline-1",
            database,
            25,
        )
        == "previewed"
    )
    assert (
        pipeline_router.dry_run_rows(
            "pipeline-1",
            "dry-run-1",
            2,
            3,
            40,
            database,
        )
        == "rows"
    )

    assert received["enqueue"][2] == {
        "require_production_executable_fn": require_executable,
        "broker_check_fn": broker_check,
    }
    assert received["sync"][2] == {
        "require_production_executable_fn": require_executable,
    }
    assert received["enabled"][3] == {
        "task_refs_fn": task_refs,
        "is_n8n_pipeline_fn": is_n8n,
        "format_pipeline_fn": formatter,
    }
    assert received["dry_run"][3] == {
        "is_n8n_pipeline_fn": is_n8n,
        "dry_run_bucket": "patched-bucket",
    }
    assert received["rows"][6] == {
        "dry_run_uri_fn": dry_run_uri,
    }


def test_remaining_execution_entries_delegate_to_canonical_service(
    monkeypatch,
):
    expected = object()
    calls = []

    monkeypatch.setattr(
        execution_service,
        "list_pipeline_runs",
        lambda pipeline_id, db, limit=50: calls.append(
            ("list", pipeline_id, db, limit)
        )
        or expected,
    )
    monkeypatch.setattr(
        execution_service,
        "get_pipeline_run",
        lambda run_id, db: calls.append(("get", run_id, db))
        or expected,
    )
    monkeypatch.setattr(
        execution_service,
        "reject_dry_run_commit",
        lambda: calls.append(("commit",)) or expected,
    )
    monkeypatch.setattr(
        execution_service,
        "preview_pipeline_step",
        lambda body: calls.append(("preview", body)) or expected,
    )
    database = object()
    body = contracts.PreviewStepBody(op="schema_inference")

    assert pipeline_router.list_runs("pipeline-1", database) is expected
    assert pipeline_router.get_run("run-1", database) is expected
    assert (
        pipeline_router.commit_dry_run("pipeline-1", "dry-run-1")
        is expected
    )
    assert pipeline_router.preview_step(body) is expected
    assert calls == [
        ("list", "pipeline-1", database, 50),
        ("get", "run-1", database),
        ("commit",),
        ("preview", body),
    ]


def test_pipeline_router_keeps_business_workflows_as_thin_adapters():
    source = ROUTER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ROUTER_PATH))
    functions = _top_level_functions(tree)

    assert len(source.splitlines()) < 750
    assert not {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }.intersection(CONTRACT_NAMES)

    for name in (
        "create_pipeline",
        "list_pipelines",
        "get_pipeline",
        "update_pipeline",
        "delete_pipeline",
        "validate_pipeline",
        "validate_column_definitions",
        "publish_pipeline",
        "unpublish_pipeline",
        "list_versions",
        "run_pipeline",
        "list_runs",
        "get_run",
        "run_pipeline_sync",
        "set_pipeline_enabled",
        "dry_run_pipeline",
        "dry_run_rows",
        "commit_dry_run",
        "preview_step",
    ):
        body = _non_docstring_body(functions[name])
        assert len(body) == 1
        assert isinstance(body[0], ast.Return)


def test_execution_service_keeps_patchable_runtime_imports_local():
    tree = ast.parse(
        CANONICAL_SERVICE_PATHS[0].read_text(encoding="utf-8"),
        filename=str(CANONICAL_SERVICE_PATHS[0]),
    )
    functions = _top_level_functions(tree)

    expected_local_imports = {
        "enqueue_pipeline_run": {
            "app.tasks.v2.pipeline_run",
        },
        "run_pipeline_synchronously": {
            "app.tasks.v2.pipeline_run",
        },
        "set_pipeline_enabled": {
            "app.data_channel.steward",
        },
        "dry_run_pipeline": {
            "app.data_channel.steward.runner",
            "app.tasks.v2.pipeline_run",
        },
    }
    for function_name, expected_modules in expected_local_imports.items():
        imported_modules = {
            node.module
            for node in ast.walk(functions[function_name])
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert expected_modules.issubset(imported_modules)


def test_pipeline_services_never_import_the_http_router():
    violations: list[str] = []
    for path in CANONICAL_SERVICE_PATHS:
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app.data_channel.pipelines.router":
                        violations.append(
                            f"{path.relative_to(BACKEND_DIR)}:{node.lineno}"
                        )
            if module == "app.data_channel.pipelines.router":
                violations.append(
                    f"{path.relative_to(BACKEND_DIR)}:{node.lineno}"
                )

    assert not violations, (
        "Canonical pipeline services must not depend on the HTTP adapter:\n"
        + "\n".join(violations)
    )


def test_pipeline_management_implementation_is_outside_http_router():
    source = ROUTER_PATH.read_text(encoding="utf-8")
    service_source = CANONICAL_SERVICE_PATHS[2].read_text(encoding="utf-8")

    assert "db.query(PipelineRun)" not in source
    assert "db.query(PipelineVersion)" not in source
    assert "validation_attestation" not in source
    assert "def update_pipeline(" in service_source
    assert "def delete_pipeline(" in service_source
    assert pipeline_router.management_service is management_service
