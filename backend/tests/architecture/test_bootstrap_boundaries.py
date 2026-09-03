"""Keep process bootstrap implementation out of the HTTP composition root."""
from __future__ import annotations

import asyncio
import ast
from contextlib import asynccontextmanager
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[2]
MAIN_PATH = BACKEND_DIR / "app" / "main.py"


def _top_level_functions(tree: ast.Module) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    }


def test_main_keeps_bootstrap_implementation_out_of_composition_root():
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"), filename=str(MAIN_PATH))
    functions = _top_level_functions(tree)

    assert "_seed_db" not in functions
    assert "_probe_http_service" not in functions

    health = functions["health"]
    assert len(health.body) == 1
    assert isinstance(health.body[0], ast.Return)

    lifespan = functions["lifespan"]
    assert len(lifespan.body) == 1
    assert isinstance(lifespan.body[0], ast.AsyncWith)

    forbidden_imports = {
        "asyncio",
        "tempfile",
        "app.shared.schema_compat",
    }
    imported_modules = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imported_modules.update(
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert forbidden_imports.isdisjoint(imported_modules)
    assert {
        "app.bootstrap.lifecycle",
        "app.bootstrap.seeding",
    }.issubset(imported_modules)


def test_main_bootstrap_compatibility_aliases_keep_object_identity():
    from app import main
    from app.bootstrap import health, seeding

    assert main._seed_db is seeding.seed_database
    assert main._probe_http_service is health.probe_http_service
    assert main.urllib is health.urllib
    assert main.urllib.request is health.urllib.request


def test_health_wrapper_resolves_legacy_probe_alias_at_call_time(monkeypatch):
    from app import main

    patched_probe = object()
    db = object()
    received = {}

    def fake_readiness(current_db, *, probe):
        received["db"] = current_db
        received["probe"] = probe
        return "ready"

    monkeypatch.setattr(main, "_probe_http_service", patched_probe)
    monkeypatch.setattr(
        main.bootstrap_health,
        "readiness_response",
        fake_readiness,
    )

    assert main.health(db) == "ready"
    assert received == {"db": db, "probe": patched_probe}


@pytest.mark.asyncio
async def test_lifespan_wrapper_resolves_legacy_seed_alias_at_call_time(
    monkeypatch,
):
    from app import main

    patched_seed = object()
    received = {}

    @asynccontextmanager
    async def fake_lifespan(current_app, *, seed_database):
        received["app"] = current_app
        received["seed_database"] = seed_database
        yield

    monkeypatch.setattr(main, "_seed_db", patched_seed)
    monkeypatch.setattr(main, "application_lifespan", fake_lifespan)

    async with main.lifespan(main.app):
        received["entered"] = True

    assert received == {
        "app": main.app,
        "seed_database": patched_seed,
        "entered": True,
    }


@pytest.mark.asyncio
async def test_canonical_lifecycle_preserves_startup_and_shutdown_order(
    monkeypatch,
):
    from app.api_hub import db as api_hub_db
    from app.bootstrap import lifecycle
    from app.data_channel.file_assets import service as file_asset_service
    from app.data_channel.steward import browser_runtime
    from app.services import sentinel
    from app.services.v2 import sync_scheduler
    from app.services.v2.graph import index_setup
    from app.shared import dependency_probe
    from app.ontologies import projection_state

    events: list[str] = []

    class Manager:
        def __init__(self, name: str):
            self.name = name

        def run(self):
            return self

        async def __aenter__(self):
            events.append(f"{self.name}.enter")

        async def __aexit__(self, *_args):
            events.append(f"{self.name}.exit")

    class Scheduler:
        healthy = True
        last_error = None

        def start(self):
            events.append("data_scheduler.start")

        def shutdown(self):
            events.append("data_scheduler.shutdown")

    scheduler = Scheduler()

    monkeypatch.setattr(lifecycle.settings, "environment", "development")
    monkeypatch.setattr(
        dependency_probe,
        "probe_startup_dependencies",
        lambda: events.append("dependencies.probe"),
    )
    monkeypatch.setattr(
        api_hub_db,
        "init_db",
        lambda: events.append("api_hub_db.init"),
    )
    monkeypatch.setattr(
        sentinel,
        "register_cdc",
        lambda *, start_worker: events.append(
            f"sentinel_cdc.register:{start_worker}"
        ),
    )
    monkeypatch.setattr(
        sentinel,
        "start_scan_worker",
        lambda: events.append("sentinel_scan.start") or True,
    )
    monkeypatch.setattr(
        sentinel,
        "stop_scan_worker",
        lambda: events.append("sentinel_scan.stop"),
    )
    monkeypatch.setattr(
        sentinel,
        "stop_cdc_worker",
        lambda: events.append("sentinel_cdc.stop"),
    )
    monkeypatch.setattr(
        index_setup,
        "setup_indexes",
        lambda: events.append("neo4j_indexes.setup")
        or {"status": "done", "results": []},
    )
    monkeypatch.setattr(
        projection_state,
        "repair_unready_projections",
        lambda: events.append("neo4j_projections.repair") or 0,
    )
    monkeypatch.setattr(
        sync_scheduler,
        "get_sync_scheduler",
        lambda: scheduler,
    )
    async def cleanup_loop():
        await asyncio.Future()

    monkeypatch.setattr(
        file_asset_service,
        "file_asset_cleanup_loop",
        cleanup_loop,
    )
    real_create_task = asyncio.create_task
    task_calls = 0

    def create_task(coro):
        nonlocal task_calls
        task_calls += 1
        events.append(f"task.create.{task_calls}")
        return real_create_task(coro)

    monkeypatch.setattr(lifecycle.asyncio, "create_task", create_task)
    monkeypatch.setattr(
        browser_runtime.browser_manager,
        "close_all",
        lambda: events.append("browser.close_all"),
    )

    async with lifecycle.application_lifespan(
        object(),
        seed_database=lambda: events.append("database.seed"),
    ):
        events.append("application.ready")

    assert events == [
        "database.seed",
        "dependencies.probe",
        "neo4j_indexes.setup",
        "neo4j_projections.repair",
        "api_hub_db.init",
        "sentinel_cdc.register:True",
        "sentinel_scan.start",
        "data_scheduler.start",
        "task.create.1",
        "task.create.2",
        "application.ready",
        "browser.close_all",
        "data_scheduler.shutdown",
        "sentinel_scan.stop",
        "sentinel_cdc.stop",
    ]


@pytest.mark.asyncio
async def test_lifecycle_dependency_failure_starts_no_background_resources(
    monkeypatch,
):
    from app.bootstrap import lifecycle
    from app.services import sentinel
    from app.shared import dependency_probe

    events: list[str] = []

    monkeypatch.setattr(lifecycle.settings, "environment", "development")

    def reject_dependencies():
        events.append("dependencies.probe")
        raise RuntimeError("unavailable")

    monkeypatch.setattr(
        dependency_probe,
        "probe_startup_dependencies",
        reject_dependencies,
    )
    monkeypatch.setattr(
        sentinel,
        "register_cdc",
        lambda **_kwargs: events.append("sentinel.start"),
    )

    with pytest.raises(RuntimeError, match="unavailable"):
        async with lifecycle.application_lifespan(
            object(),
            seed_database=lambda: events.append("database.seed"),
        ):
            pytest.fail("lifespan must not enter")

    assert events == ["database.seed", "dependencies.probe"]


@pytest.mark.asyncio
async def test_lifecycle_partial_startup_failure_cleans_started_resources(
    monkeypatch,
):
    from app.api_hub import db as api_hub_db
    from app.bootstrap import lifecycle
    from app.ontologies import projection_state
    from app.services import sentinel
    from app.services.v2.graph import index_setup
    from app.shared import dependency_probe

    events: list[str] = []

    monkeypatch.setattr(lifecycle.settings, "environment", "production")
    monkeypatch.setattr(
        dependency_probe,
        "probe_startup_dependencies",
        lambda: events.append("dependencies.probe"),
    )
    monkeypatch.setattr(
        index_setup,
        "setup_indexes",
        lambda: events.append("neo4j_indexes.setup")
        or {"status": "done", "results": []},
    )
    monkeypatch.setattr(
        projection_state,
        "repair_unready_projections",
        lambda: events.append("neo4j_projections.repair") or 0,
    )
    monkeypatch.setattr(
        api_hub_db,
        "init_db",
        lambda: events.append("api_hub_db.init"),
    )

    def fail_sentinel(*, start_worker):
        events.append(f"sentinel.register:{start_worker}")
        raise RuntimeError("injected sentinel startup failure")

    monkeypatch.setattr(sentinel, "register_cdc", fail_sentinel)
    monkeypatch.setattr(
        sentinel,
        "stop_scan_worker",
        lambda: events.append("sentinel_scan.stop"),
    )
    monkeypatch.setattr(
        sentinel,
        "stop_cdc_worker",
        lambda: events.append("sentinel_cdc.stop"),
    )

    with pytest.raises(RuntimeError, match="Sentinel engine"):
        async with lifecycle.application_lifespan(
            object(),
            seed_database=lambda: events.append("database.seed"),
        ):
            pytest.fail("lifespan must not enter")

    assert events == [
        "database.seed",
        "dependencies.probe",
        "neo4j_indexes.setup",
        "neo4j_projections.repair",
        "api_hub_db.init",
        "sentinel.register:True",
        "sentinel_scan.stop",
        "sentinel_cdc.stop",
    ]
