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
    SuperAssistantReflectionCandidate,
    SuperAssistantReflectionRun,
    SuperAssistantSkill,
    SuperAssistantToolRun,
)


def test_user_can_manage_conversations_folder_skills_and_mcp(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_skill_root", str(tmp_path / "skills"))
    monkeypatch.setattr(settings, "environment", "development")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'router.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine, tables=[
        User.__table__, ModelConfig.__table__,
        SuperAssistantConversation.__table__, SuperAssistantSkill.__table__,
        SuperAssistantMcpServer.__table__, SuperAssistantMessage.__table__,
        SuperAssistantToolRun.__table__,
        SuperAssistantReflectionRun.__table__, SuperAssistantReflectionCandidate.__table__,
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
        "name": "folder-skill",
        "description": "Uses companion files",
        "content": "Read references when needed.",
        "enabled": True,
    })
    assert created.status_code == 201, created.text
    skill = created.json()
    assert skill["name"] == "folder-skill"
    assert skill["enabled"] is True
    assert "display_name" not in skill
    assert "triggers" not in skill
    assert [entry["path"] for entry in skill["manifest"]] == ["SKILL.md"]

    disabled = client.patch(
        f"/api/v2/super-assistant/skills/{skill['id']}",
        json={"enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    listed_skill = next(
        item for item in client.get("/api/v2/super-assistant/skills").json()
        if item["id"] == skill["id"]
    )
    assert listed_skill["enabled"] is False

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
    assert payload["transport"] == "streamable_http"
    assert payload["header_names"] == ["Authorization"]
    assert "secret" not in server.text
    assert payload["require_confirmation"] is True

    stdio = client.post("/api/v2/super-assistant/mcp-servers", json={
        "name": "local_stdio",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@example/mcp-server"],
        "env": {"API_KEY": "stdio-secret"},
    })
    assert stdio.status_code == 201, stdio.text
    stdio_payload = stdio.json()
    assert stdio_payload["transport"] == "stdio"
    assert stdio_payload["command"] == "npx"
    assert stdio_payload["args"] == ["-y", "@example/mcp-server"]
    assert stdio_payload["env_names"] == ["API_KEY"]
    assert "stdio-secret" not in stdio.text

    assert client.delete(
        f"/api/v2/super-assistant/conversations/{conversation['id']}"
    ).status_code == 204


def test_conversation_archive_and_restore(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'archive.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine, tables=[
        User.__table__, SuperAssistantConversation.__table__,
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
    assert conversation["status"] == "active"
    patch_url = f"/api/v2/super-assistant/conversations/{conversation['id']}"

    archived = client.patch(patch_url, json={"status": "archived"})
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"

    # 归档不隐藏：列表语义不变，分组/展示由前端负责
    listed = client.get("/api/v2/super-assistant/conversations").json()
    assert [item["status"] for item in listed] == ["archived"]

    restored = client.patch(patch_url, json={"status": "active"})
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "active"

    assert client.patch(patch_url, json={"status": "deleted"}).status_code == 422
    assert client.patch(patch_url, json={"status": "bogus"}).status_code == 422

    # 既有字段行为回归：仅改标题不影响归档状态
    client.patch(patch_url, json={"status": "archived"})
    renamed = client.patch(patch_url, json={"title": "改名后"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "改名后"
    assert renamed.json()["status"] == "archived"
