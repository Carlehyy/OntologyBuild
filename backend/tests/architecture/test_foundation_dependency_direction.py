import ast
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
APP_DIR = BACKEND_DIR / "app"


def _absolute_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append((node.lineno, node.module))
    return imports


def _forbidden_imports(
    paths: list[Path],
    forbidden_prefixes: tuple[str, ...],
) -> list[str]:
    violations: list[str] = []
    for path in paths:
        for line, imported_module in _absolute_imports(path):
            if any(
                imported_module == prefix
                or imported_module.startswith(f"{prefix}.")
                for prefix in forbidden_prefixes
            ):
                violations.append(
                    f"{path.relative_to(BACKEND_DIR)}:{line} imports "
                    f"{imported_module}"
                )
    return violations


def test_auth_does_not_depend_on_settings_or_shared_compatibility_facades():
    auth_files = sorted((APP_DIR / "auth").rglob("*.py"))
    violations = _forbidden_imports(
        auth_files,
        ("app.settings", "app.shared"),
    )

    assert not violations, (
        "The auth domain must own its schemas and consume canonical root "
        "infrastructure, never settings or shared compatibility facades:\n"
        + "\n".join(violations)
    )


def test_data_channel_does_not_depend_on_exploration():
    data_channel_files = sorted((APP_DIR / "data_channel").rglob("*.py"))
    violations = _forbidden_imports(data_channel_files, ("app.exploration",))

    assert not violations, (
        "Data-channel code may consume shared capabilities but must not depend "
        "on the exploration business domain:\n"
        + "\n".join(violations)
    )


def test_dataset_automation_policy_breaks_event_orchestrator_cycle():
    policy = (
        APP_DIR / "data_channel" / "datasets" / "automation_policy.py"
    )
    orchestrator = (
        APP_DIR
        / "data_channel"
        / "sync_tasks"
        / "incremental_orchestrator.py"
    )
    violations = _forbidden_imports(
        [policy],
        (
            "app.data_channel.datasets.version_events",
            "app.data_channel.sync_tasks.incremental_orchestrator",
        ),
    )
    violations.extend(_forbidden_imports(
        [orchestrator],
        ("app.data_channel.datasets.version_events",),
    ))

    assert not violations, (
        "Dataset automation policy must be the shared lower-level dependency; "
        "the incremental orchestrator must not import the event dispatcher:\n"
        + "\n".join(violations)
    )


def _assert_thin_facade(path: Path, canonical_module: str) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = _absolute_imports(path)
    assert imports == [(tree.body[1].lineno, canonical_module)]

    implementation_nodes = [
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.FunctionDef,
            ),
        )
    ]
    assert not implementation_nodes


def test_shared_deps_is_only_a_compatibility_facade():
    _assert_thin_facade(APP_DIR / "shared" / "deps.py", "app.deps")


def test_exploration_web_search_is_only_a_compatibility_facade():
    _assert_thin_facade(
        APP_DIR / "exploration" / "web_search.py",
        "app.shared.web_search",
    )
