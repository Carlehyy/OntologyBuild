"""Protect Super Assistant HTTP, patch, and transaction boundaries."""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.params import Depends, File

from app.settings.object_storage import service as object_storage_service
from app.super_assistant import (
    conversation_service,
    mcp_server_service,
    memory_service,
    reflection_service,
    router as assistant_router,
    runtime,
    schemas,
    skill_service,
    skill_store,
)


BACKEND_DIR = Path(__file__).resolve().parents[2]
ASSISTANT_DIR = BACKEND_DIR / "app" / "super_assistant"

ROUTE_PARAMETERS = {
    "list_conversations": ("db", "current_user"),
    "create_conversation": ("body", "db", "current_user"),
    "update_conversation": (
        "conversation_id",
        "body",
        "db",
        "current_user",
    ),
    "delete_conversation": (
        "conversation_id",
        "db",
        "current_user",
    ),
    "list_messages": (
        "conversation_id",
        "db",
        "current_user",
    ),
    "chat": (
        "conversation_id",
        "body",
        "db",
        "current_user",
    ),
    "cancel_chat": (
        "conversation_id",
        "db",
        "current_user",
    ),
    "decide_tool_run": (
        "tool_run_id",
        "body",
        "db",
        "current_user",
    ),
    "list_skills": ("db", "current_user"),
    "create_skill": ("body", "db", "current_user"),
    "import_skill": ("archive", "db", "current_user"),
    "update_skill": (
        "skill_id",
        "body",
        "db",
        "current_user",
    ),
    "remove_skill": ("skill_id", "db", "current_user"),
    "list_skill_files": (
        "skill_id",
        "db",
        "current_user",
    ),
    "get_skill_file": (
        "skill_id",
        "file_path",
        "db",
        "current_user",
    ),
    "put_skill_file": (
        "skill_id",
        "file_path",
        "body",
        "db",
        "current_user",
    ),
    "remove_skill_file": (
        "skill_id",
        "file_path",
        "db",
        "current_user",
    ),
    "export_skill": ("skill_id", "db", "current_user"),
    "list_mcp_servers": ("db", "current_user"),
    "create_mcp_server": ("body", "db", "current_user"),
    "update_mcp_server": (
        "server_id",
        "body",
        "db",
        "current_user",
    ),
    "remove_mcp_server": (
        "server_id",
        "db",
        "current_user",
    ),
    "test_mcp_server": (
        "server_id",
        "db",
        "current_user",
    ),
    "install_platform_minio_mcp": ("db", "current_user"),
    "list_memories": (
        "zone",
        "include_superseded",
        "db",
        "current_user",
    ),
    "create_memory": ("body", "db", "current_user"),
    "update_memory": (
        "memory_id",
        "body",
        "db",
        "current_user",
    ),
    "delete_memory": (
        "memory_id",
        "db",
        "current_user",
    ),
    "get_memory_distill_report": ("db", "current_user"),
    "distill_memories": ("body", "db", "current_user"),
    "list_reflection_candidates": (
        "status",
        "db",
        "current_user",
    ),
    "decide_reflection_candidate": (
        "candidate_id",
        "body",
        "db",
        "current_user",
    ),
    "request_full_reflection": (
        "body",
        "db",
        "current_user",
    ),
    "get_reflection_settings": ("db", "current_user"),
    "update_reflection_settings": (
        "body",
        "db",
        "current_user",
    ),
}

DELEGATES = {
    "list_conversations": (
        "_conversation_service",
        "list_conversations",
    ),
    "create_conversation": (
        "_conversation_service",
        "create_conversation",
    ),
    "update_conversation": (
        "_conversation_service",
        "update_conversation",
    ),
    "delete_conversation": (
        "_conversation_service",
        "delete_conversation",
    ),
    "list_messages": (
        "_conversation_service",
        "list_messages",
    ),
    "chat": ("_conversation_service", "chat"),
    "cancel_chat": ("_conversation_service", "cancel_chat"),
    "decide_tool_run": (
        "_conversation_service",
        "decide_tool_run",
    ),
    "list_skills": ("_skill_service", "list_skills"),
    "create_skill": ("_skill_service", "create_skill"),
    "import_skill": ("_skill_service", "import_skill"),
    "update_skill": ("_skill_service", "update_skill"),
    "remove_skill": ("_skill_service", "remove_skill"),
    "list_skill_files": (
        "_skill_service",
        "list_skill_files",
    ),
    "get_skill_file": ("_skill_service", "get_skill_file"),
    "put_skill_file": ("_skill_service", "put_skill_file"),
    "remove_skill_file": (
        "_skill_service",
        "remove_skill_file",
    ),
    "export_skill": ("_skill_service", "export_skill"),
    "list_mcp_servers": (
        "mcp_server_service",
        "list_mcp_servers",
    ),
    "create_mcp_server": (
        "mcp_server_service",
        "create_mcp_server",
    ),
    "update_mcp_server": (
        "mcp_server_service",
        "update_mcp_server",
    ),
    "remove_mcp_server": (
        "mcp_server_service",
        "remove_mcp_server",
    ),
    "test_mcp_server": (
        "mcp_server_service",
        "test_mcp_server",
    ),
    "install_platform_minio_mcp": (
        "mcp_server_service",
        "install_platform_minio_mcp",
    ),
    "list_memories": ("memory_service", "list_memories"),
    "create_memory": ("memory_service", "create_memory"),
    "update_memory": ("memory_service", "update_memory"),
    "delete_memory": ("memory_service", "delete_memory"),
    "get_memory_distill_report": (
        "memory_service",
        "find_distill_clusters",
    ),
    "distill_memories": ("memory_service", "apply_distill"),
    "list_reflection_candidates": (
        "reflection_service",
        "list_candidates",
    ),
    "decide_reflection_candidate": (
        "reflection_service",
        "decide_candidate",
    ),
    "request_full_reflection": (
        "reflection_service",
        "request_full_reflection",
    ),
    "get_reflection_settings": (
        "reflection_service",
        "get_reflection_settings",
    ),
    "update_reflection_settings": (
        "reflection_service",
        "update_reflection_settings",
    ),
}

BODY_TYPES = {
    "create_conversation": schemas.ConversationCreate,
    "update_conversation": schemas.ConversationUpdate,
    "chat": schemas.ChatRequest,
    "decide_tool_run": schemas.ApprovalRequest,
    "create_skill": schemas.SkillCreate,
    "update_skill": schemas.SkillUpdate,
    "put_skill_file": schemas.SkillFileContent,
    "create_mcp_server": schemas.McpServerCreate,
    "update_mcp_server": schemas.McpServerUpdate,
    "create_memory": schemas.MemoryCreate,
    "update_memory": schemas.MemoryUpdate,
    "distill_memories": schemas.MemoryDistillRequest,
    "decide_reflection_candidate": schemas.ReflectionDecisionRequest,
    "request_full_reflection": schemas.ReflectionFullRequest,
    "update_reflection_settings": schemas.ReflectionSettingsUpdate,
}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(
        path.read_text(encoding="utf-8"),
        filename=str(path),
    )
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    return imports


def test_super_assistant_router_preserves_contract_and_helper_aliases():
    for name in (
        "ApprovalRequest",
        "ChatRequest",
        "ConversationCreate",
        "ConversationOut",
        "ConversationUpdate",
        "McpServerCreate",
        "McpServerOut",
        "McpServerUpdate",
        "McpTestOut",
        "MemoryCreate",
        "MemoryOut",
        "MemoryUpdate",
        "MessageOut",
        "SkillCreate",
        "SkillFileContent",
        "SkillOut",
        "SkillUpdate",
    ):
        assert getattr(assistant_router, name) is getattr(schemas, name)

    assert (
        assistant_router._conversation
        is conversation_service._conversation
    )
    assert assistant_router._skill is skill_service._skill
    assert (
        assistant_router._storage_error
        is skill_service._storage_error
    )
    assert assistant_router.stream_chat is runtime.stream_chat
    assert (
        assistant_router.get_workspace_minio_service
        is object_storage_service.get_workspace_minio_service
    )
    for name in (
        "SkillStoreError",
        "build_manifest",
        "create_skill_folder",
        "delete_file",
        "delete_skill_folder",
        "export_skill_archive",
        "import_skill_archive",
        "parse_skill_markdown",
        "read_text_file",
        "render_skill_markdown",
        "skill_directory",
        "write_text_file",
    ):
        assert getattr(assistant_router, name) is getattr(
            skill_store,
            name,
        )


def test_super_assistant_route_signatures_remain_stable():
    for name, expected_parameters in ROUTE_PARAMETERS.items():
        parameters = inspect.signature(
            getattr(assistant_router, name),
            eval_str=True,
        ).parameters
        assert tuple(parameters) == expected_parameters
        assert isinstance(parameters["db"].default, Depends)
        assert isinstance(
            parameters["current_user"].default,
            Depends,
        )
        if name in BODY_TYPES:
            assert parameters["body"].annotation is BODY_TYPES[name]

    archive = inspect.signature(
        assistant_router.import_skill,
        eval_str=True,
    ).parameters["archive"]
    assert archive.annotation is assistant_router.UploadFile
    assert isinstance(archive.default, File)


def test_super_assistant_handlers_delegate_without_orm_or_transactions():
    path = ASSISTANT_DIR / "router.py"
    tree = ast.parse(
        path.read_text(encoding="utf-8"),
        filename=str(path),
    )
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name, (module_name, function_name) in DELEGATES.items():
        function = functions[name]
        service_calls = [
            node
            for statement in function.body
            for node in ast.walk(statement)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == module_name
                and node.func.attr == function_name
            )
        ]
        assert len(service_calls) == 1

        forbidden = [
            node.func.attr
            for statement in function.body
            for node in ast.walk(statement)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {
                    "query",
                    "execute",
                    "scalar",
                    "add",
                    "add_all",
                    "delete",
                    "refresh",
                    "flush",
                    "commit",
                    "rollback",
                }
            )
        ]
        assert not forbidden, f"{name} still owns {forbidden}"


def test_router_patch_seams_are_resolved_at_request_time(
    monkeypatch,
):
    stream = object()
    lookup = object()
    expected_chat = object()
    chat_call = {}

    def fake_chat(*args, **kwargs):
        chat_call["args"] = args
        chat_call["kwargs"] = kwargs
        return expected_chat

    monkeypatch.setattr(assistant_router, "stream_chat", stream)
    monkeypatch.setattr(assistant_router, "_conversation", lookup)
    monkeypatch.setattr(conversation_service, "chat", fake_chat)
    body = object()
    database = object()
    actor = object()
    assert assistant_router.chat(
        "conversation-1",
        body,
        database,
        actor,
    ) is expected_chat
    assert chat_call == {
        "args": (
            "conversation-1",
            body,
            database,
            actor,
        ),
        "kwargs": {
            "conversation_lookup_fn": lookup,
            "stream_chat_fn": stream,
        },
    }

    service_factory = object()
    manifest = object()
    expected_mcp = object()
    mcp_call = {}

    def fake_install(*args, **kwargs):
        mcp_call["args"] = args
        mcp_call["kwargs"] = kwargs
        return expected_mcp

    monkeypatch.setattr(
        assistant_router,
        "get_workspace_minio_service",
        service_factory,
    )
    monkeypatch.setattr(
        assistant_router,
        "minio_tool_manifest",
        manifest,
    )
    monkeypatch.setattr(
        mcp_server_service,
        "install_platform_minio_mcp",
        fake_install,
    )
    actor = SimpleNamespace(id="owner-1")
    assert assistant_router.install_platform_minio_mcp(
        database,
        actor,
    ) is expected_mcp
    assert mcp_call == {
        "args": (database, "owner-1"),
        "kwargs": {
            "workspace_minio_service_factory": service_factory,
            "minio_tool_manifest_fn": manifest,
        },
    }


def test_super_assistant_services_do_not_import_http_router():
    for module in (
        conversation_service,
        mcp_server_service,
        memory_service,
        reflection_service,
        skill_service,
    ):
        imports = _imports(Path(module.__file__))
        assert "app.super_assistant.router" not in imports
        assert "app.routers.super_assistant" not in imports


def test_super_assistant_router_and_services_stay_bounded():
    limits = {
        "router.py": 750,
        "conversation_service.py": 320,
        "skill_service.py": 380,
        "mcp_server_service.py": 340,
    }
    for filename, maximum in limits.items():
        line_count = len(
            (ASSISTANT_DIR / filename)
            .read_text(encoding="utf-8")
            .splitlines()
        )
        assert line_count < maximum


def test_super_assistant_openapi_matches_pre_extraction_baseline():
    from app.main import app

    prefix = "/api/v2/super-assistant"
    paths = {
        path: value
        for path, value in app.openapi()["paths"].items()
        if path.startswith(prefix)
    }
    payload = json.dumps(
        paths,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    # 基线随契约演进而更新：0068 技能治理为 Skill 契约新增
    # always_active/use_count/last_used_at 字段；memory_distill 特性新增
    # /memories/distill-report 与 /memories/distill 两个端点
    assert len(paths) == 24
    assert sum(len(item) for item in paths.values()) == 35
    assert hashlib.sha256(payload).hexdigest() == (
        "f0242f256442a3cb5f87d1fca8a7cbfc1652b2161362690659ed408481b4f653"
    )
