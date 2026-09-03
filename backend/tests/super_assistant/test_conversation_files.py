from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.shared.config import settings
from app.shared.database import Base
from app.super_assistant import conversation_service, files_workspace, router, runtime
from app.super_assistant.models import (
    SuperAssistantConversation,
    SuperAssistantMessage,
    SuperAssistantReflectionCandidate,
    SuperAssistantReflectionRun,
    SuperAssistantToolRun,
)

_TABLES = [
    User.__table__, SuperAssistantConversation.__table__,
    SuperAssistantMessage.__table__, SuperAssistantToolRun.__table__,
    SuperAssistantReflectionRun.__table__, SuperAssistantReflectionCandidate.__table__,
]

_PREFIX = "/api/v2/super-assistant"


def _user(user_id: str, username: str) -> User:
    return User(
        id=user_id, username=username, email=f"{username}@example.com",
        password_hash="unused", role="editor",
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_workspace_root", str(tmp_path / "sessions"))
    engine = create_engine(
        f"sqlite:///{tmp_path / 'files.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine, tables=_TABLES)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with TestingSession() as db:
        db.add(_user("user-1", "owner"))
        db.add(_user("user-2", "other"))
        db.commit()

    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    def make_client(user: User):
        app = FastAPI()
        app.include_router(router.router, prefix=_PREFIX)
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    return SimpleNamespace(
        client=make_client(_user("user-1", "owner")),
        make_client=make_client,
        session=TestingSession,
        root=tmp_path / "sessions",
    )


def _create_conversation(client: TestClient) -> str:
    response = client.post(f"{_PREFIX}/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _upload(client: TestClient, conversation_id: str, name: str, content: bytes, mime: str):
    return client.post(
        f"{_PREFIX}/conversations/{conversation_id}/files",
        files={"file": (name, content, mime)},
    )


def test_upload_list_preview_download_delete_roundtrip(env):
    conversation_id = _create_conversation(env.client)

    rejected = _upload(env.client, conversation_id, "evil.exe", b"MZ", "application/octet-stream")
    assert rejected.status_code == 400

    created = _upload(env.client, conversation_id, "notes.md", "# 会议纪要\n重点：周五上线\n".encode(), "text/markdown")
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["filename"] == "notes.md"
    assert row["source"] == "upload"
    assert row["extractedChars"] > 0
    assert _upload(env.client, conversation_id, "data.csv", b"name,qty\na,2\n", "text/csv").status_code == 201

    listed = env.client.get(f"{_PREFIX}/conversations/{conversation_id}/files")
    assert listed.status_code == 200
    assert [item["filename"] for item in listed.json()] == ["notes.md", "data.csv"]

    preview = env.client.get(
        f"{_PREFIX}/conversations/{conversation_id}/files/{row['id']}/preview"
    )
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload["file"]["id"] == row["id"]
    assert "会议纪要" in payload["content"]
    assert payload["previewable"] is True
    assert payload["truncated"] is False

    downloaded = env.client.get(f"{_PREFIX}/conversations/{conversation_id}/files/{row['id']}")
    assert downloaded.status_code == 200
    assert downloaded.content == "# 会议纪要\n重点：周五上线\n".encode()

    deleted = env.client.delete(f"{_PREFIX}/conversations/{conversation_id}/files/{row['id']}")
    assert deleted.status_code == 204
    remaining = env.client.get(f"{_PREFIX}/conversations/{conversation_id}/files").json()
    assert [item["filename"] for item in remaining] == ["data.csv"]
    assert env.client.delete(
        f"{_PREFIX}/conversations/{conversation_id}/files/{row['id']}"
    ).status_code == 404


def test_files_are_scoped_to_conversation_and_owner(env):
    first = _create_conversation(env.client)
    second = _create_conversation(env.client)
    artifact = _upload(env.client, first, "notes.md", b"secret", "text/markdown").json()

    # 同一用户的另一会话：清单为空，跨会话点名 artifact 返回 404
    assert env.client.get(f"{_PREFIX}/conversations/{second}/files").json() == []
    assert env.client.get(
        f"{_PREFIX}/conversations/{second}/files/{artifact['id']}"
    ).status_code == 404

    # 不存在的会话与他人的会话：一律 404
    assert env.client.get(f"{_PREFIX}/conversations/{uuid.uuid4()}/files").status_code == 404
    other_client = env.make_client(_user("user-2", "other"))
    assert other_client.get(f"{_PREFIX}/conversations/{first}/files").status_code == 404
    assert _upload(other_client, first, "x.md", b"x", "text/markdown").status_code == 404


def test_delete_conversation_removes_session_directory(env):
    conversation_id = _create_conversation(env.client)
    assert _upload(env.client, conversation_id, "notes.md", b"hi", "text/markdown").status_code == 201
    assert (env.root / conversation_id).exists()

    with env.session() as db:
        conversation_service.delete_conversation(
            conversation_id, db, _user("user-1", "owner"),
        )
    assert not (env.root / conversation_id).exists()


def test_file_context_section_reflects_session_files(env):
    conversation_id = str(uuid.uuid4())
    assert files_workspace.file_context_section(conversation_id, query="需求") == ""

    session = files_workspace.session_workspace()
    session.save_bytes(
        conversation_id, "需求说明.md", "需求：附件要能被助手读到".encode(),
        source="upload", mime_type="text/markdown",
    )
    section = files_workspace.file_context_section(conversation_id, query="需求")
    assert "需求说明.md" in section
    assert "当前会话文件目录" in section


def test_agent_file_tools_are_read_only_and_session_scoped(env):
    conversation_id = str(uuid.uuid4())
    session = files_workspace.session_workspace()
    row = session.save_bytes(
        conversation_id, "需求说明.md", "第一行\n第二行\n".encode(),
        source="upload", mime_type="text/markdown",
    )
    context = {
        "owner_id": "user-1",
        "conversation_id": conversation_id,
        "assistant_message_id": "assistant-1",
        "call_kwargs": {},
    }

    tools = runtime._builtin_tools()
    names = [tool["name"] for tool in tools]
    assert {"list_session_files", "read_session_file"} <= set(names)
    assert {"list_session_files", "read_session_file"} <= runtime._READ_ONLY_BUILTIN_TOOLS

    listed = json.loads(runtime._execute_builtin_tool(
        None, name="list_session_files", arguments={}, **context,
    ))
    assert [item["filename"] for item in listed] == ["需求说明.md"]
    assert "relativePath" not in listed[0]

    read = json.loads(runtime._execute_builtin_tool(
        None, name="read_session_file",
        arguments={"artifact_id": row["id"], "offset": 0, "max_chars": 4}, **context,
    ))
    assert len(read["content"]) == 4
    assert read["offset"] == 0
    assert read["next_offset"] == 4
    assert read["truncated"] is True
    full = json.loads(runtime._execute_builtin_tool(
        None, name="read_session_file", arguments={"artifact_id": row["id"]}, **context,
    ))
    assert "第二行" in full["content"]
    assert full["truncated"] is False

    missing = json.loads(runtime._execute_builtin_tool(
        None, name="read_session_file",
        arguments={"artifact_id": str(uuid.uuid4())}, **context,
    ))
    assert "error" in missing
    empty = json.loads(runtime._execute_builtin_tool(
        None, name="read_session_file", arguments={"artifact_id": " "}, **context,
    ))
    assert "error" in empty


def test_system_prompt_appends_file_section():
    prompt = runtime._system_prompt([], memory_section="MEM", file_section="FILES")
    assert prompt.index("MEM") < prompt.index("FILES")
    assert "FILES" not in runtime._system_prompt([], memory_section="MEM")
