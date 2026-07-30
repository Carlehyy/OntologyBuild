"""Protect the curated review read-model and thin HTTP-adapter boundary."""

from __future__ import annotations

import ast
from pathlib import Path

from app.data_channel.curated import router


APP_DIR = Path(__file__).resolve().parents[2] / "app"
ROUTER_PATH = APP_DIR / "data_channel" / "curated" / "router.py"
SERVICE_PATH = (
    APP_DIR / "data_channel" / "curated" / "review_query_service.py"
)
CURATED_DIR = APP_DIR / "data_channel" / "curated"


def _function_node(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path}")


def _assert_one_return_adapter(
    function_name: str,
    *,
    service_attribute: str,
) -> None:
    node = _function_node(ROUTER_PATH, function_name)
    executable = [
        statement
        for statement in node.body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
    ]

    assert len(executable) == 1
    assert isinstance(executable[0], ast.Return)
    call = executable[0].value
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Attribute)
    assert call.func.attr == service_attribute


def test_curated_http_endpoints_keep_extracted_workflows_thin():
    for function_name, service_attribute in {
        "list_curated": "list_curated",
        "delete_curated": "delete_curated",
        "get_curated": "get_curated",
        "preview_curated": "preview_curated",
        "export_curated": "export_curated",
        "review_diff": "build_review_diff",
    }.items():
        _assert_one_return_adapter(
            function_name,
            service_attribute=service_attribute,
        )


def test_review_diff_business_workflow_lives_in_query_service():
    service_source = SERVICE_PATH.read_text(encoding="utf-8")

    for business_symbol in (
        "apply_all_row_edits",
        "encode_row_pk",
        "compute_lake_impact",
        "DatasetService",
    ):
        assert business_symbol in service_source

    endpoint_names = {
        node.id
        for node in ast.walk(_function_node(ROUTER_PATH, "review_diff"))
        if isinstance(node, ast.Name)
    }
    assert not endpoint_names.intersection(
        {
            "apply_all_row_edits",
            "encode_row_pk",
            "compute_lake_impact",
            "DatasetService",
        }
    )
    assert router.review_query_service.build_review_diff is not None


def test_curated_modules_remain_cohesive_and_router_stays_bounded():
    expected_symbols = {
        "catalog_service.py": (
            "def list_curated(",
            "def get_curated(",
        ),
        "lifecycle_service.py": ("def delete_curated(",),
        "read_service.py": (
            "def preview_curated(",
            "def export_curated(",
        ),
        "review_query_service.py": ("def build_review_diff(",),
    }
    for filename, symbols in expected_symbols.items():
        source = (CURATED_DIR / filename).read_text(encoding="utf-8")
        for symbol in symbols:
            assert symbol in source

    assert len(ROUTER_PATH.read_text(encoding="utf-8").splitlines()) < 400
