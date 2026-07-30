"""Protect Data Steward HTTP, SSE, and module-boundary compatibility."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.params import Depends, File, Query

from app.data_channel.steward import (
    browser_session_service,
    contracts,
    lifecycle_service,
    query_service,
    router as steward_router,
    streaming_service,
)


BACKEND_DIR = Path(__file__).resolve().parents[2]
STEWARD_DIR = BACKEND_DIR / "app" / "data_channel" / "steward"

ROUTE_PARAMETERS = {
    "steward_status": ("db", "_"),
    "chat": ("body", "db", "current_user"),
    "create_conversation": ("body", "db", "current_user"),
    "list_conversations": ("db", "current_user"),
    "get_conversation": (
        "conversation_id",
        "db",
        "current_user",
    ),
    "export_conversation": (
        "conversation_id",
        "db",
        "current_user",
    ),
    "delete_conversation": (
        "conversation_id",
        "db",
        "current_user",
    ),
    "list_conversation_files": (
        "conversation_id",
        "db",
        "current_user",
    ),
    "preview_conversation_file": (
        "conversation_id",
        "artifact_id",
        "max_chars",
        "db",
        "current_user",
    ),
    "upload_conversation_file": (
        "conversation_id",
        "file",
        "db",
        "current_user",
    ),
    "download_conversation_file": (
        "conversation_id",
        "artifact_id",
        "db",
        "current_user",
    ),
    "delete_conversation_file": (
        "conversation_id",
        "artifact_id",
        "db",
        "current_user",
    ),
    "archive_conversation_files": (
        "conversation_id",
        "db",
        "current_user",
    ),
    "browser_status": ("_",),
    "list_browser_sources": ("db", "current_user"),
    "create_browser_source": ("body", "db", "current_user"),
    "update_browser_source": (
        "source_id",
        "body",
        "db",
        "current_user",
    ),
    "rotate_browser_source_token": (
        "source_id",
        "db",
        "current_user",
    ),
    "delete_browser_source": (
        "source_id",
        "db",
        "current_user",
    ),
    "test_browser_source": (
        "source_id",
        "db",
        "current_user",
    ),
    "download_browser_companion": ("_",),
    "bind_browser_source": (
        "conversation_id",
        "body",
        "db",
        "current_user",
    ),
    "start_browser": (
        "conversation_id",
        "body",
        "db",
        "current_user",
    ),
    "navigate_browser": (
        "conversation_id",
        "body",
        "db",
        "current_user",
    ),
    "browser_state": (
        "conversation_id",
        "db",
        "current_user",
    ),
    "browser_session": (
        "conversation_id",
        "db",
        "current_user",
    ),
    "browser_click": (
        "conversation_id",
        "body",
        "db",
        "current_user",
    ),
    "browser_type": (
        "conversation_id",
        "body",
        "db",
        "current_user",
    ),
    "browser_captures": (
        "conversation_id",
        "keyword",
        "limit",
        "db",
        "current_user",
    ),
    "browser_capture_download": (
        "conversation_id",
        "capture_id",
        "db",
        "current_user",
    ),
    "browser_live_ticket": (
        "conversation_id",
        "db",
        "current_user",
    ),
    "attach_browser_live_http": (
        "conversation_id",
        "db",
        "current_user",
    ),
    "browser_live_http_frame": (
        "conversation_id",
        "body",
        "response",
        "db",
        "current_user",
    ),
    "browser_live_http_input": (
        "conversation_id",
        "body",
        "db",
        "current_user",
    ),
    "browser_live_http_control": (
        "conversation_id",
        "body",
        "db",
        "current_user",
    ),
    "release_browser_live_http": (
        "conversation_id",
        "body",
        "db",
        "current_user",
    ),
    "list_pipeline_records": ("include_archived", "db", "_"),
    "bootstrap_pipeline": ("body", "db", "current_user"),
    "get_pipeline_record": ("record_id", "db", "_"),
}

DELEGATES = {
    "steward_status": ("_query_service", "steward_status"),
    "chat": ("_streaming_service", "chat"),
    "create_conversation": (
        "_lifecycle_service",
        "create_conversation",
    ),
    "list_conversations": (
        "_query_service",
        "list_conversations",
    ),
    "get_conversation": ("_query_service", "get_conversation"),
    "export_conversation": (
        "_query_service",
        "export_conversation",
    ),
    "delete_conversation": (
        "_lifecycle_service",
        "delete_conversation",
    ),
    "list_conversation_files": (
        "_query_service",
        "list_conversation_files",
    ),
    "preview_conversation_file": (
        "_query_service",
        "preview_conversation_file",
    ),
    "upload_conversation_file": (
        "_lifecycle_service",
        "upload_conversation_file",
    ),
    "download_conversation_file": (
        "_query_service",
        "download_conversation_file",
    ),
    "delete_conversation_file": (
        "_lifecycle_service",
        "delete_conversation_file",
    ),
    "archive_conversation_files": (
        "_query_service",
        "archive_conversation_files",
    ),
    "browser_status": (
        "_browser_source_service",
        "browser_status",
    ),
    "list_browser_sources": (
        "_browser_source_service",
        "list_browser_sources",
    ),
    "create_browser_source": (
        "_browser_source_service",
        "create_browser_source",
    ),
    "update_browser_source": (
        "_browser_source_service",
        "update_browser_source",
    ),
    "rotate_browser_source_token": (
        "_browser_source_service",
        "rotate_browser_source_token",
    ),
    "delete_browser_source": (
        "_browser_source_service",
        "delete_browser_source",
    ),
    "test_browser_source": (
        "_browser_source_service",
        "test_browser_source",
    ),
    "download_browser_companion": (
        "_browser_source_service",
        "download_browser_companion",
    ),
    "bind_browser_source": (
        "_browser_session_service",
        "bind_browser_source",
    ),
    "start_browser": (
        "_browser_session_service",
        "start_browser",
    ),
    "navigate_browser": (
        "_browser_session_service",
        "navigate_browser",
    ),
    "browser_state": (
        "_browser_session_service",
        "browser_state",
    ),
    "browser_session": (
        "_browser_session_service",
        "browser_session",
    ),
    "browser_click": (
        "_browser_session_service",
        "browser_click",
    ),
    "browser_type": (
        "_browser_session_service",
        "browser_type",
    ),
    "browser_captures": (
        "_browser_session_service",
        "browser_captures",
    ),
    "browser_capture_download": (
        "_browser_session_service",
        "browser_capture_download",
    ),
    "browser_live_ticket": (
        "_browser_session_service",
        "browser_live_ticket",
    ),
    "attach_browser_live_http": (
        "_browser_session_service",
        "attach_browser_live_http",
    ),
    "browser_live_http_frame": (
        "_browser_session_service",
        "browser_live_http_frame",
    ),
    "browser_live_http_input": (
        "_browser_session_service",
        "browser_live_http_input",
    ),
    "browser_live_http_control": (
        "_browser_session_service",
        "browser_live_http_control",
    ),
    "release_browser_live_http": (
        "_browser_session_service",
        "release_browser_live_http",
    ),
    "list_pipeline_records": (
        "_query_service",
        "list_pipeline_records",
    ),
    "bootstrap_pipeline": (
        "_lifecycle_service",
        "bootstrap_pipeline",
    ),
    "get_pipeline_record": (
        "_query_service",
        "get_pipeline_record",
    ),
}

BODY_TYPES = {
    "chat": contracts.ChatBody,
    "create_conversation": contracts.CreateConversationBody,
    "create_browser_source": contracts.CreateBrowserSourceBody,
    "update_browser_source": contracts.UpdateBrowserSourceBody,
    "bind_browser_source": contracts.BindBrowserSourceBody,
    "start_browser": contracts.BrowserUrlBody,
    "navigate_browser": contracts.BrowserUrlBody,
    "browser_click": contracts.BrowserClickBody,
    "browser_type": contracts.BrowserTypeBody,
    "browser_live_http_frame": contracts.BrowserLiveLeaseBody,
    "browser_live_http_input": contracts.BrowserLiveInputBody,
    "browser_live_http_control": contracts.BrowserLiveControlBody,
    "release_browser_live_http": contracts.BrowserLiveLeaseBody,
    "bootstrap_pipeline": contracts.BootstrapBody,
}


def test_steward_router_reexports_contracts_and_helpers_by_identity():
    for name in contracts.__all__:
        assert getattr(steward_router, name) is getattr(contracts, name)
    assert steward_router._ok is query_service._ok
    assert steward_router._conv_out is query_service._conv_out
    assert steward_router._msg_out is query_service._msg_out
    assert (
        steward_router._require_conversation
        is query_service._require_conversation
    )
    assert steward_router._handle is lifecycle_service._handle
    assert (
        steward_router._browser_error
        is browser_session_service._browser_error
    )


def test_steward_route_signatures_remain_stable():
    for name, expected_parameters in ROUTE_PARAMETERS.items():
        parameters = inspect.signature(
            getattr(steward_router, name),
            eval_str=True,
        ).parameters
        assert tuple(parameters) == expected_parameters
        for dependency_name in ("db", "current_user", "_"):
            if dependency_name in parameters:
                assert isinstance(
                    parameters[dependency_name].default,
                    Depends,
                )
        if name in BODY_TYPES:
            assert parameters["body"].annotation is BODY_TYPES[name]

    preview = inspect.signature(
        steward_router.preview_conversation_file
    ).parameters
    captures = inspect.signature(
        steward_router.browser_captures
    ).parameters
    pipelines = inspect.signature(
        steward_router.list_pipeline_records
    ).parameters
    upload = inspect.signature(
        steward_router.upload_conversation_file
    ).parameters
    assert isinstance(preview["max_chars"].default, Query)
    assert isinstance(captures["limit"].default, Query)
    assert isinstance(pipelines["include_archived"].default, Query)
    assert isinstance(upload["file"].default, File)


def test_steward_http_handlers_are_single_delegations():
    path = STEWARD_DIR / "router.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert set(functions) == set(ROUTE_PARAMETERS)
    for name, (module_name, function_name) in DELEGATES.items():
        function = functions[name]
        executable = [
            statement
            for statement in function.body
            if not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            )
        ]
        assert len(executable) == 1
        statement = executable[0]
        assert isinstance(statement, ast.Return)
        assert isinstance(statement.value, ast.Call)
        assert isinstance(statement.value.func, ast.Attribute)
        assert isinstance(statement.value.func.value, ast.Name)
        assert statement.value.func.value.id == module_name
        assert statement.value.func.attr == function_name


def test_chat_resolves_router_patch_at_request_time(monkeypatch):
    turn = object()
    expected = object()
    received = {}

    def fake_chat(body, db, current_user, *, run_turn_fn):
        received["args"] = (body, db, current_user)
        received["run_turn_fn"] = run_turn_fn
        return expected

    monkeypatch.setattr(steward_router, "run_steward_turn", turn)
    monkeypatch.setattr(streaming_service, "chat", fake_chat)
    body = contracts.ChatBody(message="检查 patch seam")
    db = object()
    user = object()

    assert steward_router.chat(body, db, user) is expected
    assert received == {
        "args": (body, db, user),
        "run_turn_fn": turn,
    }


def test_streaming_service_preserves_event_order_and_closes_session(
    monkeypatch,
):
    from app import database

    class FakeSession:
        closed = False

        def close(self):
            self.closed = True

    session = FakeSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    events = [
        {"type": "meta", "conversationId": "conv-1"},
        {"type": "step", "summary": "读取文件"},
        {"type": "answer", "content": "完成"},
        {"type": "done"},
    ]
    seen = {}

    def run_turn(db, user, message, **kwargs):
        seen.update({
            "db": db,
            "user": user,
            "message": message,
            "kwargs": kwargs,
        })
        yield from events

    body = contracts.ChatBody(
        message="处理数据",
        conversationId="conv-1",
        modelId="model-1",
        targetRecordId="record-1",
        webSearch=True,
    )
    user = object()
    response = streaming_service.chat(
        body,
        object(),
        user,
        run_turn_fn=run_turn,
    )

    async def consume():
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(consume())
    decoded = [
        json.loads(chunk.removeprefix("data: ").removesuffix("\n\n"))
        for chunk in chunks
    ]
    assert decoded == events
    assert seen == {
        "db": session,
        "user": user,
        "message": "处理数据",
        "kwargs": {
            "conversation_id": "conv-1",
            "model_id": "model-1",
            "target_record_id": "record-1",
            "web_search": True,
        },
    }
    assert session.closed is True
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


def test_conversation_guard_preserves_not_found_and_owner_errors():
    class QueryResult:
        def __init__(self, row):
            self.row = row

        def filter(self, *_args):
            return self

        def first(self):
            return self.row

    class Database:
        def __init__(self, row):
            self.row = row

        def query(self, _model):
            return QueryResult(self.row)

    user = SimpleNamespace(id="user-1", role="editor")
    with pytest.raises(HTTPException) as missing:
        query_service._require_conversation(
            Database(None), "missing", user
        )
    assert (missing.value.status_code, missing.value.detail) == (
        404,
        "会话不存在",
    )

    conversation = SimpleNamespace(id="conv-1", user_id="user-2")
    with pytest.raises(HTTPException) as forbidden:
        query_service._require_conversation(
            Database(conversation), "conv-1", user
        )
    assert (forbidden.value.status_code, forbidden.value.detail) == (
        403,
        "无权访问他人会话",
    )


def test_delete_conversation_keeps_commit_before_workspace_cleanup(
    monkeypatch,
):
    order = []
    conversation = SimpleNamespace(id="conv-1")

    class MessageQuery:
        def filter(self, *_args):
            return self

        def delete(self):
            order.append("messages")

    class Database:
        def query(self, _model):
            return MessageQuery()

        def delete(self, row):
            assert row is conversation
            order.append("conversation")

        def commit(self):
            order.append("commit")

    monkeypatch.setattr(
        lifecycle_service.browser_manager,
        "close",
        lambda conversation_id: order.append(
            f"browser:{conversation_id}"
        ),
    )
    monkeypatch.setattr(
        lifecycle_service.workspace,
        "remove_session",
        lambda conversation_id: order.append(
            f"workspace:{conversation_id}"
        ),
    )

    lifecycle_service.delete_conversation(
        "conv-1",
        Database(),
        object(),
        require_conversation_fn=lambda *_args: conversation,
    )

    assert order == [
        "browser:conv-1",
        "messages",
        "conversation",
        "commit",
        "workspace:conv-1",
    ]


def test_file_lifecycle_preserves_validation_and_not_found_text(
    monkeypatch,
):
    monkeypatch.setattr(
        lifecycle_service.settings,
        "allowed_upload_extensions",
        "csv,txt",
    )
    upload = SimpleNamespace(
        filename="payload.exe",
        file=object(),
        content_type="application/octet-stream",
    )
    with pytest.raises(HTTPException) as unsupported:
        lifecycle_service.upload_conversation_file(
            "conv-1",
            upload,
            object(),
            object(),
            require_conversation_fn=lambda *_args: object(),
        )
    assert (unsupported.value.status_code, unsupported.value.detail) == (
        400,
        "不支持的文件类型 .exe（允许: csv,txt）",
    )

    def missing_file(*_args):
        raise lifecycle_service.workspace.WorkspaceError("文件不存在")

    monkeypatch.setattr(
        lifecycle_service.workspace,
        "delete_file",
        missing_file,
    )
    with pytest.raises(HTTPException) as missing:
        lifecycle_service.delete_conversation_file(
            "conv-1",
            "artifact-1",
            object(),
            object(),
            require_conversation_fn=lambda *_args: object(),
        )
    assert (missing.value.status_code, missing.value.detail) == (
        404,
        "文件不存在",
    )


def test_steward_openapi_contract_is_stable():
    from app.main import app

    paths = {
        path: value
        for path, value in app.openapi()["paths"].items()
        if path.startswith("/api/v2/steward")
    }
    payload = json.dumps(
        paths,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(paths) == 33
    assert sum(
        method in {"get", "post", "put", "patch", "delete"}
        for operations in paths.values()
        for method in operations
    ) == 39
    assert hashlib.sha256(payload.encode()).hexdigest() == (
        "82bacefb8a4746f5edc1da13934ecee0b3f6c550d44c92c9c8a2c7977b9732a9"
    )


def test_steward_modules_stay_bounded():
    maximum_lines = {
        "router.py": 700,
        "contracts.py": 100,
        "streaming_service.py": 100,
        "lifecycle_service.py": 200,
        "query_service.py": 350,
        "browser_source_service.py": 250,
        "browser_session_service.py": 375,
    }
    for filename, maximum in maximum_lines.items():
        line_count = len(
            (STEWARD_DIR / filename)
            .read_text(encoding="utf-8")
            .splitlines()
        )
        assert line_count <= maximum, (
            f"{filename} grew to {line_count} lines; split by responsibility"
        )
