"""Architecture contracts for ontology runtime dependency direction."""
from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path

from app.data_channel.pipelines import engine as pipeline_engine_facade
from app.data_channel.pipelines import route_executor, trigger_service
from app.ontologies import runtime_fence
from app.ontologies.mappings import mapping_service
from app.ontologies.sentinels import engine as sentinel_engine
from app.ontologies.sentinels import evaluator as sentinel_evaluator
from app.ontologies.versions import evolution_service, snapshot_contract
from app.services.v2.pipeline import engine as legacy_pipeline_engine
from app.services.sentinel import engine as legacy_sentinel_engine
from app.services.sentinel import evaluator as legacy_sentinel_evaluator


APP_DIR = Path(__file__).resolve().parents[2] / "app"
EXPECTED_SENTINEL_ORCHESTRATION_SCC = frozenset({
    "app.ontologies.sentinels.cdc",
    "app.ontologies.sentinels.dynamic_service",
    "app.ontologies.sentinels.engine",
})


def _production_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in APP_DIR.rglob("*.py"):
        relative = path.relative_to(APP_DIR.parent).with_suffix("")
        module = ".".join(relative.parts)
        if module.endswith(".__init__"):
            module = module.removesuffix(".__init__")
        modules[module] = path
    return modules


def _local_import_graph(
    modules: dict[str, Path],
) -> dict[str, set[str]]:
    graph = {module: set() for module in modules}
    for module, path in modules.items():
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.Import):
                candidates.extend(alias.name for alias in node.names)
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module
            ):
                candidates.append(node.module)
                candidates.extend(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                )
            graph[module].update(
                candidate
                for candidate in candidates
                if candidate in modules and candidate != module
            )
    return graph


def _strongly_connected_components(
    graph: dict[str, set[str]],
) -> list[frozenset[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[frozenset[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph[node]:
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        component: set[str] = set()
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.add(member)
            if member == node:
                break
        components.append(frozenset(component))

    for node in graph:
        if node not in indices:
            visit(node)
    return components


def test_ontology_runtime_has_only_the_explicit_sentinel_orchestration_ring():
    modules = _production_modules()
    graph = _local_import_graph(modules)
    ontology_components = {
        component
        for component in _strongly_connected_components(graph)
        if len(component) > 1
        and any(
            module.startswith((
                "app.ontologies.",
                "app.services.formal.",
                "app.services.sentinel.",
            ))
            for module in component
        )
    }

    assert ontology_components == {
        EXPECTED_SENTINEL_ORCHESTRATION_SCC,
    }
    expected_edges = {
        (
            "app.ontologies.sentinels.cdc",
            "app.ontologies.sentinels.engine",
        ),
        (
            "app.ontologies.sentinels.engine",
            "app.ontologies.sentinels.cdc",
        ),
        (
            "app.ontologies.sentinels.engine",
            "app.ontologies.sentinels.dynamic_service",
        ),
        (
            "app.ontologies.sentinels.dynamic_service",
            "app.ontologies.sentinels.cdc",
        ),
    }
    actual_edges = {
        (source, target)
        for source in EXPECTED_SENTINEL_ORCHESTRATION_SCC
        for target in graph[source] & EXPECTED_SENTINEL_ORCHESTRATION_SCC
    }
    assert actual_edges == expected_edges


def test_application_runtime_has_no_unregistered_dependency_ring():
    modules = _production_modules()
    graph = _local_import_graph(modules)
    runtime_components = {
        component
        for component in _strongly_connected_components(graph)
        if len(component) > 1
    }

    assert runtime_components == {
        EXPECTED_SENTINEL_ORCHESTRATION_SCC,
    }


def test_pipeline_runtime_uses_canonical_engine_and_keeps_legacy_contract():
    production_callers = (
        APP_DIR / "tasks" / "v2" / "pipeline_run.py",
        APP_DIR / "data_channel" / "sync_tasks" / "engine.py",
    )
    for path in production_callers:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "app.data_channel.pipelines.engine" not in imported_modules
        assert "app.services.v2.pipeline.engine" not in imported_modules

    for name, canonical_module in (
        ("execute_route_a", route_executor),
        ("execute_route_b", route_executor),
        ("execute_route_c", route_executor),
        ("execute_pipeline", trigger_service),
    ):
        assert getattr(pipeline_engine_facade, name) is getattr(
            canonical_module,
            name,
        )
        assert getattr(legacy_pipeline_engine, name) is getattr(
            canonical_module,
            name,
        )


def test_snapshot_contract_is_a_leaf_and_evolution_reexports_exact_objects():
    path = Path(snapshot_contract.__file__).resolve()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    forbidden_prefixes = (
        "app.ontologies.mappings",
        "app.ontologies.release_context",
        "app.ontologies.sentinels",
        "app.ontologies.versions.evolution_service",
        "app.ontologies.formal_modeling.action_engine",
    )
    assert not {
        module
        for module in imported
        if module.startswith(forbidden_prefixes)
    }

    for name in (
        "json_safe",
        "complete_snapshot",
        "canonical_snapshot",
        "snapshot_hash",
        "next_draft_number",
        "next_release_number",
        "snapshot_models",
    ):
        assert getattr(evolution_service, name) is getattr(
            snapshot_contract,
            name,
        )
    assert evolution_service.SNAPSHOT_KEYS is snapshot_contract.SNAPSHOT_KEYS


def test_runtime_fence_keeps_identity_and_historical_mapping_patch(
    monkeypatch,
):
    assert (
        mapping_service._ontology_build_lock
        is runtime_fence._ontology_build_lock
    )
    events: list[str] = []

    @contextmanager
    def compatibility_lock(db, ontology_id):
        events.append(f"enter:{id(db)}:{ontology_id}")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr(
        mapping_service,
        "_ontology_build_lock",
        compatibility_lock,
    )
    db = object()
    with runtime_fence._ontology_build_lock(db, "ontology-id"):
        events.append("inside")
    assert events == [
        f"enter:{id(db)}:ontology-id",
        "inside",
        "exit",
    ]


def test_legacy_sentinel_modules_are_true_aliases_of_canonical_modules():
    assert legacy_sentinel_engine is sentinel_engine
    assert legacy_sentinel_evaluator is sentinel_evaluator
