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
        name="qa_skill", display_name="QA Skill", description="Use this skill",
        triggers=["qa"], instructions="Read references when necessary.",
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
            id="skill-1", owner_id="user-1", name="qa_skill",
            display_name="QA Skill", description="Use this skill", triggers=["qa"],
            folder_path=str(folder), manifest=build_manifest(folder), enabled=True,
        )
        db.add_all([model, conversation, user_message, assistant_message, skill])
        db.commit()

    responses = iter([
        {
            "content": None,
            "tool_calls": [{"id": "call-1", "name": "use_skill", "arguments": {"name": "qa_skill"}}],
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
        assert saved.token_usage == {"inputTokens": 30, "outputTokens": 10}
        tool_run = db.query(SuperAssistantToolRun).one()
        assert tool_run.tool_name == "use_skill"
        assert tool_run.status == "success"
