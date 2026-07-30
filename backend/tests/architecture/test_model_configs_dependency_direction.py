import ast
from pathlib import Path

from app.model_configs import (
    config_service,
    connectivity_service,
    presentation,
    router as model_router,
    usage_query_service,
)


BACKEND_DIR = Path(__file__).resolve().parents[2]
MODEL_CONFIGS_DIR = BACKEND_DIR / "app" / "model_configs"
ROUTER_PATH = MODEL_CONFIGS_DIR / "router.py"


def _absolute_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append((node.lineno, node.module))
    return imports


def test_model_configs_does_not_depend_on_ontologies():
    violations: list[str] = []
    for path in sorted(MODEL_CONFIGS_DIR.rglob("*.py")):
        for line, imported_module in _absolute_imports(path):
            if (
                imported_module == "app.ontologies"
                or imported_module.startswith("app.ontologies.")
            ):
                violations.append(
                    f"{path.relative_to(BACKEND_DIR)}:{line} imports "
                    f"{imported_module}"
                )

    assert not violations, (
        "The model-config domain owns the provider gateway and must not depend "
        "on the ontology business domain:\n"
        + "\n".join(violations)
    )


def _attribute_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _endpoint_calls() -> dict[str, set[str]]:
    tree = ast.parse(
        ROUTER_PATH.read_text(encoding="utf-8"),
        filename=str(ROUTER_PATH),
    )
    calls: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(
            isinstance(decorator, ast.Call)
            and (
                (_attribute_name(decorator.func) or "").split(".")[-1]
                in {"get", "post", "put", "patch", "delete"}
            )
            for decorator in node.decorator_list
        ):
            continue
        calls[node.name] = {
            name
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and (name := _attribute_name(call.func))
        }
    return calls


def test_model_config_router_is_a_thin_http_adapter():
    source = ROUTER_PATH.read_text(encoding="utf-8")
    for forbidden in (
        ".query(",
        ".commit(",
        ".rollback(",
        ".flush(",
        "IntegrityError",
        "ModelCallLog",
        "ExtractionTask",
        "llm_call_kwargs",
        "easyocr",
        "paddleocr",
    ):
        assert forbidden not in source

    calls = _endpoint_calls()
    expected = {
        "list_models": "config_service.list_models",
        "create_model": "config_service.create_model",
        "import_models": "config_service.import_models",
        "get_model": "config_service.get_model",
        "update_model": "config_service.update_model",
        "delete_model": "config_service.delete_model",
        "set_default_model": "config_service.select_default",
        "set_model_enabled": "config_service.set_enabled",
        "test_model": "connectivity_service.test_model",
        "get_model_stats": "usage_query_service.get_model_stats",
        "list_model_calls": "usage_query_service.list_model_calls",
    }
    assert set(calls) == set(expected)
    for endpoint, service_call in expected.items():
        assert service_call in calls[endpoint]


def test_model_config_router_keeps_historical_helper_objects():
    expected = {
        "_set_default": config_service.set_default,
        "_ensure_default": config_service.ensure_default,
        "_name_exists": config_service.name_exists,
        "_require_tested": config_service.require_tested,
        "_safe_test_error": connectivity_service.safe_test_error,
        "_safe_log_error": presentation.safe_log_error,
        "_utc_naive": presentation.utc_naive,
        "_save_test_result": connectivity_service.save_test_result,
        "_commit_config_change": config_service.commit_config_change,
        "_model_out": presentation.model_out,
        "_iso_utc": presentation.iso_utc,
    }
    for name, implementation in expected.items():
        assert getattr(model_router, name) is implementation
