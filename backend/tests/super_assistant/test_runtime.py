from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.model_configs.models import ModelConfig
from app.shared.config import settings
from app.shared.database import Base
from app.super_assistant import runtime
from app.super_assistant.models import (
    SuperAssistantConversation,
    SuperAssistantMcpServer,
    SuperAssistantMessage,
    SuperAssistantSkill,
    SuperAssistantToolRun,
)
from app.super_assistant.skill_store import build_manifest, create_skill_folder, render_skill_markdown, skill_directory


def test_runtime_progressively_loads_folder_skill_and_persists_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_skill_root", str(tmp_path / "skills"))
    engine = create_engine(
        f"sqlite:///{tmp_path / 'runtime.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine, tables=[
        User.__table__, ModelConfig.__table__,
        SuperAssistantConversation.__table__, SuperAssistantSkill.__table__,
        SuperAssistantMcpServer.__table__, SuperAssistantMessage.__table__,
        SuperAssistantToolRun.__table__,
    ])
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(runtime, "SessionLocal", TestingSession)

    folder = skill_directory("user-1", "skill-1")
    create_skill_folder(folder, render_skill_markdown(
        name="qa-skill", description="Use this skill for QA work",
        content="Read references when necessary.",
    ))
    with TestingSession() as db:
        db.add(User(
            id="user-1", username="owner", email="owner@example.com",
            password_hash="unused", role="editor",
        ))
        model = ModelConfig(
            id="model-1", name="Fake", config_type="llm", provider="openai",
            models=["fake-model"], options={}, enabled=True, is_default=True,
            created_by="user-1",
        )
        conversation = SuperAssistantConversation(
            id="conversation-1", owner_id="user-1", title="QA",
            model_config_id="model-1",
        )
        user_message = SuperAssistantMessage(
            id="user-message-1", conversation_id=conversation.id,
            role="user", content="use qa", status="complete",
        )
        assistant_message = SuperAssistantMessage(
            id="assistant-message-1", conversation_id=conversation.id,
            role="assistant", content="", status="streaming",
        )
        skill = SuperAssistantSkill(
            id="skill-1", owner_id="user-1", name="qa-skill",
            display_name="qa-skill", description="Use this skill for QA work", triggers=[],
            folder_path=str(folder), manifest=build_manifest(folder), enabled=True,
        )
        db.add_all([model, conversation, user_message, assistant_message, skill])
        db.commit()

    responses = iter([
        {
            "content": None,
            "tool_calls": [{"id": "call-1", "name": "use_skill", "arguments": {"name": "qa-skill"}}],
            "usage": {"inputTokens": 10, "outputTokens": 2},
        },
        {
            "content": "已按目录 Skill 完成。",
            "tool_calls": [],
            "usage": {"inputTokens": 20, "outputTokens": 8},
        },
    ])
    monkeypatch.setattr(runtime.provider, "chat", lambda *_args, **_kwargs: next(responses))

    events = "".join(runtime.stream_chat(
        conversation_id="conversation-1",
        owner_id="user-1",
        assistant_message_id="assistant-message-1",
        requested_model_id="model-1",
    ))
    assert "event: tool_start" in events
    assert "event: text_delta" in events
    assert "已按目录 Skill 完成" in events

    with TestingSession() as db:
        saved = db.get(SuperAssistantMessage, "assistant-message-1")
        assert saved.status == "complete"
        assert saved.content == "已按目录 Skill 完成。"
        assert saved.token_usage == {
            "inputTokens": 30,
            "outputTokens": 10,
            "contextTokens": 20,
            "contextLimit": 64_000,
        }
        tool_run = db.query(SuperAssistantToolRun).one()
        assert tool_run.tool_name == "use_skill"
        assert tool_run.status == "success"


def test_runtime_executes_builtin_minio_mcp_without_network_or_credentials(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'minio-runtime.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine, tables=[
        User.__table__, ModelConfig.__table__,
        SuperAssistantConversation.__table__, SuperAssistantSkill.__table__,
        SuperAssistantMcpServer.__table__, SuperAssistantMessage.__table__,
        SuperAssistantToolRun.__table__,
    ])
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(runtime, "SessionLocal", TestingSession)
    with TestingSession() as db:
        db.add(User(
            id="user-minio", username="owner", email="minio@example.com",
            password_hash="unused", role="editor",
        ))
        db.add(ModelConfig(
            id="model-minio", name="Fake", config_type="llm", provider="openai",
            models=["fake-model"], options={}, enabled=True, is_default=True,
            created_by="user-minio",
        ))
        db.add(SuperAssistantConversation(
            id="conversation-minio", owner_id="user-minio", title="MinIO",
            model_config_id="model-minio",
        ))
        db.add_all([
            SuperAssistantMessage(
                id="user-message-minio", conversation_id="conversation-minio",
                role="user", content="上传文件到 MinIO", status="complete",
            ),
            SuperAssistantMessage(
                id="assistant-message-minio", conversation_id="conversation-minio",
                role="assistant", content="", status="streaming",
            ),
            SuperAssistantMcpServer(
                id="server-minio", owner_id="user-minio", name="platform_minio",
                builtin_key="minio", transport="streamable_http", url="builtin://minio",
                header_names=[], args=[], env_names=[], enabled=True,
                require_confirmation=False,
                tool_manifest=[{
                    "name": "minio_upload_text",
                    "description": "上传文本",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "bucket": {"type": "string"},
                            "key": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["bucket", "key", "content"],
                    },
                }],
            ),
        ])
        db.commit()

    calls = []
    monkeypatch.setattr(
        runtime,
        "execute_minio_tool",
        lambda db, name, arguments, **kwargs: calls.append((name, arguments, kwargs)) or
        '{"ok":true,"result":{"uri":"s3://openontology/note.txt"}}',
    )
    responses = iter([
        {
            "content": None,
            "tool_calls": [{
                "id": "call-minio",
                "name": "mcp__platform_minio__minio_upload_text",
                "arguments": {"bucket": "openontology", "key": "note.txt", "content": "hello"},
            }],
            "usage": {"inputTokens": 15, "outputTokens": 3},
        },
        {
            "content": "文件已上传到 s3://openontology/note.txt。",
            "tool_calls": [],
            "usage": {"inputTokens": 25, "outputTokens": 9},
        },
    ])
    monkeypatch.setattr(runtime.provider, "chat", lambda *_args, **_kwargs: next(responses))

    events = "".join(runtime.stream_chat(
        conversation_id="conversation-minio",
        owner_id="user-minio",
        assistant_message_id="assistant-message-minio",
        requested_model_id="model-minio",
    ))
    assert "s3://openontology/note.txt" in events
    assert calls == [(
        "minio_upload_text",
        {"bucket": "openontology", "key": "note.txt", "content": "hello"},
        {"actor_type": "super_assistant", "actor_id": "user-minio"},
    )]
    with TestingSession() as db:
        run = db.query(SuperAssistantToolRun).one()
        assert run.server_id == "server-minio"
        assert run.status == "success"
        assert "s3://openontology/note.txt" in run.result
