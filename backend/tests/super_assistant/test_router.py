from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.model_configs.models import ModelConfig
from app.shared.config import settings
from app.shared.database import Base
from app.super_assistant import router
from app.super_assistant.models import (
    SuperAssistantConversation,
    SuperAssistantMcpServer,
    SuperAssistantMessage,
    SuperAssistantSkill,
    SuperAssistantToolRun,
)


def test_user_can_manage_conversations_folder_skills_and_mcp(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_skill_root", str(tmp_path / "skills"))
    monkeypatch.setattr(settings, "super_assistant_mcp_allowed_hosts", "localhost")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'router.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine, tables=[
        User.__table__, ModelConfig.__table__,
        SuperAssistantConversation.__table__, SuperAssistantSkill.__table__,
        SuperAssistantMcpServer.__table__, SuperAssistantMessage.__table__,
        SuperAssistantToolRun.__table__,
    ])
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with TestingSession() as db:
        db.add(User(
            id="user-1", username="owner", email="owner@example.com",
            password_hash="unused", role="editor",
        ))
        db.commit()

    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router.router, prefix="/api/v2/super-assistant")
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id="user-1", username="owner", email="owner@example.com",
        password_hash="unused", role="editor",
    )
    client = TestClient(app)

    conversation = client.post("/api/v2/super-assistant/conversations", json={}).json()
    assert conversation["title"] == "新会话"
    assert client.get(
        f"/api/v2/super-assistant/conversations/{conversation['id']}/messages"
    ).json() == []

    created = client.post("/api/v2/super-assistant/skills", json={
        "name": "folder_skill",
        "display_name": "目录技能",
        "description": "Uses companion files",
        "triggers": ["folder"],
        "instructions": "Read references when needed.",
        "enabled": True,
    })
    assert created.status_code == 201, created.text
    skill = created.json()
    assert [entry["path"] for entry in skill["manifest"]] == ["SKILL.md"]

    added = client.put(
        f"/api/v2/super-assistant/skills/{skill['id']}/files/references/guide.md",
        json={"content": "companion content"},
    )
    assert added.status_code == 200, added.text
    assert {entry["path"] for entry in added.json()["manifest"]} == {
        "SKILL.md", "references/guide.md",
    }
    assert client.get(
        f"/api/v2/super-assistant/skills/{skill['id']}/files/references/guide.md"
    ).json()["content"] == "companion content"

    server = client.post("/api/v2/super-assistant/mcp-servers", json={
        "name": "local_tools",
        "url": "http://localhost:9999/mcp",
        "headers": {"Authorization": "Bearer secret"},
        "enabled": True,
        "require_confirmation": True,
    })
    assert server.status_code == 201, server.text
    payload = server.json()
    assert payload["header_names"] == ["Authorization"]
    assert "secret" not in server.text
    assert payload["require_confirmation"] is True

    assert client.delete(
        f"/api/v2/super-assistant/conversations/{conversation['id']}"
    ).status_code == 204
