"""Architecture contracts for the Scenes HTTP boundary."""
from __future__ import annotations

import ast
from pathlib import Path

from app.scenes import query_service, service
from app.scenes import router as scenes_router


ROUTER_PATH = Path(scenes_router.__file__).resolve()


def _router_tree() -> ast.Module:
    return ast.parse(
        ROUTER_PATH.read_text(encoding="utf-8"),
        filename=str(ROUTER_PATH),
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


def _endpoint_functions() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    endpoints = {}
    for node in _router_tree().body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(decorator, ast.Call)
            and (
                _attribute_name(decorator.func) or ""
            ).split(".")[-1] in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
            }
            for decorator in node.decorator_list
        ):
            endpoints[node.name] = node
    return endpoints


def _calls(node: ast.AST) -> set[str]:
    return {
        name
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and (name := _attribute_name(call.func))
    }


def test_scene_router_has_no_orm_transaction_or_model_implementation():
    source = ROUTER_PATH.read_text(encoding="utf-8")
    assert ".query(" not in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".flush(" not in source
    assert "ValidationError" not in source
    # 模型类只允许出现在 service / query_service，不得泄进路由层
    assert "SceneVersion" not in source.replace(
        "SceneDefinitionSave", ""
    )
    assert "SceneRuntimeLog" not in source
    assert "import models" not in source


def test_scene_endpoints_delegate_to_cohesive_domain_services():
    endpoints = _endpoint_functions()
    assert set(endpoints) == {
        "list_scenes",
        "create_scene",
        "get_scene",
        "update_scene",
        "delete_scene",
        "clone_scene",
        "save_scene_definition",
        "publish_scene",
        "list_scene_versions",
        "get_scene_version",
        "list_scene_runtime_logs",
        "append_scene_runtime_logs",
    }
    expected_calls = {
        "list_scenes": "query_service.list_scenes",
        "create_scene": "service.create_scene",
        "get_scene": "query_service.scene_detail",
        "update_scene": "service.update_scene_info",
        "delete_scene": "service.delete_scene",
        "clone_scene": "service.clone_scene",
        "save_scene_definition": "service.save_definition",
        "publish_scene": "service.publish_scene",
        "list_scene_versions": "query_service.list_versions",
        "get_scene_version": "query_service.get_version",
        "list_scene_runtime_logs": "query_service.list_runtime_logs",
        "append_scene_runtime_logs": "service.append_runtime_logs",
    }
    for endpoint_name, expected_call in expected_calls.items():
        assert expected_call in _calls(endpoints[endpoint_name])


def test_scene_router_imports_stay_inside_domain():
    """scenes 路由只依赖本域模块与平台基础依赖（deps/schemas）。"""
    tree = _router_tree()
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[1] if node.module.startswith("app.") else node.module)
    forbidden = {"ontologies", "world_model", "events", "data_channel", "exploration", "super_assistant"}
    assert not (imported_roots & forbidden), imported_roots & forbidden
