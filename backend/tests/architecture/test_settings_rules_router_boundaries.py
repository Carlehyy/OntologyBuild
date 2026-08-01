"""Protect the remaining Settings Agent and workflow HTTP boundaries."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.settings.agents import schemas as agent_schemas
from app.settings.rules import (
    agent_config_service,
    router as settings_router,
    workflow_config_service,
)
from app.settings.workflows import schemas as workflow_schemas


ROUTER_PATH = Path(settings_router.__file__).resolve()
SERVICE_PATHS = (
    Path(agent_config_service.__file__).resolve(),
    Path(workflow_config_service.__file__).resolve(),
)

DELEGATES = {
    "get_agent_config": (
        "agent_config_service",
        "get_agent_config",
    ),
    "update_agent_config": (
        "agent_config_service",
        "update_agent_config",
    ),
    "test_agent_connection": (
        "agent_config_service",
        "test_agent_connection",
    ),
    "fetch_qwenpaw_agents": (
        "agent_config_service",
        "fetch_qwenpaw_agents",
    ),
    "get_workflow_config": (
        "workflow_config_service",
        "get_workflow_config",
    ),
    "update_workflow_config": (
        "workflow_config_service",
        "reject_direct_update",
    ),
    "test_workflow_connection": (
        "workflow_config_service",
        "test_workflow_connection",
    ),
}

ROUTE_CONTRACT = {
    "get_agent_config": (
        "/agent-config",
        {"GET"},
        agent_schemas.AgentConfigResponse,
        ("db", "_"),
    ),
    "update_agent_config": (
        "/agent-config",
        {"PUT"},
        None,
        ("body", "db", "_"),
    ),
    "test_agent_connection": (
        "/agent-config/test",
        {"POST"},
        agent_schemas.TestConnectionResponse,
        ("body", "db", "_"),
    ),
    "fetch_qwenpaw_agents": (
        "/agent-config/agents",
        {"POST"},
        agent_schemas.FetchAgentsResponse,
        ("body", "db", "_"),
    ),
    "get_workflow_config": (
        "/workflow-config",
        {"GET"},
        workflow_schemas.WorkflowConfigResponse,
        ("db", "_"),
    ),
    "update_workflow_config": (
        "/workflow-config",
        {"PUT"},
        None,
        ("_body", "_db", "_"),
    ),
    "test_workflow_connection": (
        "/workflow-config/test",
        {"POST"},
        workflow_schemas.WorkflowConnectionTestResponse,
        ("body", "db", "_"),
    ),
}


def _decorated_handlers(tree: ast.Module) -> dict[str, ast.AST]:
    handlers = {}
    for node in tree.body:
        if not isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        if any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id == "router"
            for decorator in node.decorator_list
        ):
            handlers[node.name] = node
    return handlers


def _single_return_call(node: ast.AST) -> ast.Call:
    assert len(node.body) == 1
    statement = node.body[0]
    assert isinstance(statement, ast.Return)
    assert isinstance(statement.value, ast.Call)
    return statement.value


def test_settings_handlers_are_thin_named_service_adapters():
    source = ROUTER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ROUTER_PATH))
    handlers = _decorated_handlers(tree)

    assert set(handlers) == set(DELEGATES)
    for name, (module_name, function_name) in DELEGATES.items():
        call = _single_return_call(handlers[name])
        assert isinstance(call.func, ast.Attribute)
        assert isinstance(call.func.value, ast.Name)
        assert (
            call.func.value.id,
            call.func.attr,
        ) == (module_name, function_name)
        assert not any(
            isinstance(descendant, (ast.If, ast.For, ast.While, ast.Try))
            for descendant in ast.walk(handlers[name])
        )

    for forbidden in (
        ".query(",
        ".commit(",
        ".rollback(",
        ".flush(",
        "httpx.Client(",
        "encrypt(",
        "decrypt(",
        "test_n8n_connection(",
    ):
        assert forbidden not in source
    assert len(source.splitlines()) <= 220


def test_remaining_settings_route_contract_is_explicit():
    routes = {route.name: route for route in settings_router.router.routes}
    assert set(routes) == set(ROUTE_CONTRACT)

    for name, (
        path,
        methods,
        response_model,
        parameters,
    ) in ROUTE_CONTRACT.items():
        route = routes[name]
        assert route.path == path
        assert route.methods == methods
        assert route.status_code is None
        assert route.response_model is response_model
        assert tuple(inspect.signature(route.endpoint).parameters) == (
            parameters
        )
        assert inspect.getdoc(route.endpoint) is None


def test_services_use_canonical_dependencies_and_never_import_router():
    for path in SERVICE_PATHS:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "app.settings.rules.router" not in imported
        assert "app.models" not in source
        assert "app.schemas" not in source
        assert "app.services.encryption_service" not in source


def test_router_keeps_historical_helper_objects():
    assert (
        settings_router._get_agent_config
        is agent_config_service._get_agent_config
    )
    assert (
        settings_router._normalize_base_url
        is agent_config_service._normalize_base_url
    )
    assert (
        settings_router._build_qwenpaw_api_base
        is agent_config_service._build_qwenpaw_api_base
    )
    assert (
        settings_router._login_qwenpaw
        is agent_config_service._login_qwenpaw
    )
    assert (
        settings_router._get_workflow_config
        is workflow_config_service._get_workflow_config
    )


def test_agent_dependencies_are_resolved_from_router_at_request_time(
    monkeypatch,
):
    marker_result = object()
    database = object()
    body = object()
    seams = {
        "_get_agent_config": object(),
        "_normalize_base_url": object(),
        "_build_qwenpaw_api_base": object(),
        "_login_qwenpaw": object(),
        "encrypt": object(),
        "decrypt": object(),
        "httpx": object(),
        "logger": object(),
    }
    for name, marker in seams.items():
        monkeypatch.setattr(settings_router, name, marker)

    captured: dict[str, object] = {}

    def delegate(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return marker_result

    monkeypatch.setattr(
        agent_config_service,
        "test_agent_connection",
        delegate,
    )
    assert (
        settings_router.test_agent_connection(body, database, object())
        is marker_result
    )
    assert captured["args"] == (body, database)
    assert captured["kwargs"] == {
        "get_agent_config_fn": seams["_get_agent_config"],
        "normalize_base_url_fn": seams["_normalize_base_url"],
        "build_qwenpaw_api_base_fn": seams[
            "_build_qwenpaw_api_base"
        ],
        "login_qwenpaw_fn": seams["_login_qwenpaw"],
        "encrypt_fn": seams["encrypt"],
        "httpx_module": seams["httpx"],
        "log": seams["logger"],
    }

    monkeypatch.setattr(
        agent_config_service,
        "fetch_qwenpaw_agents",
        delegate,
    )
    assert (
        settings_router.fetch_qwenpaw_agents(body, database, object())
        is marker_result
    )
    assert captured["kwargs"]["decrypt_fn"] is seams["decrypt"]
    assert (
        captured["kwargs"]["login_qwenpaw_fn"]
        is seams["_login_qwenpaw"]
    )


def test_workflow_dependencies_are_resolved_at_request_time(
    monkeypatch,
):
    marker_result = object()
    database = object()
    body = object()
    seams = {
        "_get_workflow_config": object(),
        "enforce_n8n_url_policy": object(),
        "test_n8n_connection": object(),
        "decrypt": object(),
        "encrypt": object(),
        "N8nApiError": object(),
        "httpx": object(),
        "logger": object(),
    }
    for name, marker in seams.items():
        monkeypatch.setattr(settings_router, name, marker)

    captured: dict[str, object] = {}

    def delegate(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return marker_result

    monkeypatch.setattr(
        workflow_config_service,
        "test_workflow_connection",
        delegate,
    )
    assert (
        settings_router.test_workflow_connection(
            body,
            database,
            object(),
        )
        is marker_result
    )
    assert captured["args"] == (body, database)
    assert captured["kwargs"] == {
        "environment": settings_router.settings.environment,
        "get_workflow_config_fn": seams[
            "_get_workflow_config"
        ],
        "enforce_url_policy_fn": seams[
            "enforce_n8n_url_policy"
        ],
        "test_connection_fn": seams["test_n8n_connection"],
        "decrypt_fn": seams["decrypt"],
        "encrypt_fn": seams["encrypt"],
        "n8n_api_error_type": seams["N8nApiError"],
        "httpx_module": seams["httpx"],
        "log": seams["logger"],
    }
