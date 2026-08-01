import ast
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
APP_DIR = BACKEND_DIR / "app"

SCOPED_FORBIDDEN_IMPORTS = {
    APP_DIR / "model_configs": {
        "app.models.model_config",
        "app.routers.models",
        "app.schemas.model_config",
    },
}

def _absolute_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append((node.lineno, node.module))
    return imports


def _router_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                (node.lineno, alias.name)
                for alias in node.names
                if alias.name.endswith(".router")
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
        ):
            if node.module.endswith(".router"):
                imports.append((node.lineno, node.module))
            else:
                imports.extend(
                    (node.lineno, f"{node.module}.router")
                    for alias in node.names
                    if alias.name == "router"
                )
    return imports


def test_migrated_canonical_packages_do_not_import_their_legacy_facades():
    violations: list[str] = []
    for package_dir, forbidden_modules in SCOPED_FORBIDDEN_IMPORTS.items():
        for path in sorted(package_dir.rglob("*.py")):
            for line, imported_module in _absolute_imports(path):
                if any(
                    imported_module == forbidden
                    or imported_module.startswith(f"{forbidden}.")
                    for forbidden in forbidden_modules
                ):
                    violations.append(
                        f"{path.relative_to(BACKEND_DIR)}:{line} imports "
                        f"{imported_module}"
                    )

    assert not violations, (
        "Canonical packages must import their local model/schema/router modules; "
        "legacy facades are outward-facing compatibility only:\n"
        + "\n".join(violations)
    )


def test_canonical_router_modules_do_not_import_cross_domain_routers():
    violations: list[str] = []
    for path in sorted(APP_DIR.rglob("*router.py")):
        relative_path = path.relative_to(APP_DIR)
        source_domain = relative_path.parts[0]
        if source_domain == "routers":
            # Compatibility facades intentionally re-export canonical routers.
            continue
        for line, imported_module in _router_imports(path):
            module_parts = imported_module.split(".")
            if (
                len(module_parts) >= 3
                and module_parts[0] == "app"
                and module_parts[1] != source_domain
            ):
                violations.append(
                    f"{path.relative_to(BACKEND_DIR)}:{line} imports "
                    f"{imported_module}"
                )

    assert not violations, (
        "Canonical HTTP adapters must call application/domain services instead "
        "of importing another business domain's router. main.py remains the "
        "composition root and app/routers contains compatibility facades:\n"
        + "\n".join(violations)
    )


def test_release_consumers_do_not_import_versions_router():
    forbidden_module = "app.ontologies.versions.router"
    violations: list[str] = []
    for path in (
        APP_DIR / "exploration" / "router.py",
        APP_DIR / "ontologies" / "projects" / "router.py",
    ):
        for line, imported_module in _router_imports(path):
            if (
                imported_module == forbidden_module
                or imported_module.startswith(f"{forbidden_module}.")
            ):
                violations.append(
                    f"{path.relative_to(BACKEND_DIR)}:{line} imports "
                    f"{imported_module}"
                )

    assert not violations, (
        "Exploration and ontology-project HTTP adapters must consume the "
        "versions release service, never implementation helpers from the "
        "versions router:\n"
        + "\n".join(violations)
    )


def test_main_assembles_migrated_canonical_routers_directly():
    imports = _absolute_imports(APP_DIR / "main.py")
    imported_modules = {module for _, module in imports}

    assert "app.settings.prompts.router" not in imported_modules
    assert "app.model_configs.router" in imported_modules

    tree = ast.parse((APP_DIR / "main.py").read_text(encoding="utf-8"))
    legacy_router_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and node.module == "app.routers"
        for alias in node.names
    }
    assert "prompts" not in legacy_router_names
    assert "models" not in legacy_router_names


def test_unreferenced_compatibility_facades_stay_removed():
    removed_facades = (
        APP_DIR / "schemas" / "auth.py",
        APP_DIR / "schemas" / "user.py",
        APP_DIR / "models" / "v2" / "file_asset.py",
        APP_DIR / "services" / "compat" / "legacy_file_adapter.py",
        APP_DIR / "services" / "sentinel" / "cdc.py",
        APP_DIR / "services" / "sentinel" / "scan_worker.py",
    )

    assert not [path for path in removed_facades if path.exists()]
