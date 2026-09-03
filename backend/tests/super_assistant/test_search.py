from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.shared.database import Base
from app.super_assistant import router, search
from app.super_assistant.models import (
    SuperAssistantConversation,
    SuperAssistantMessage,
)

_TABLES = [
    User.__table__,
    SuperAssistantConversation.__table__,
    SuperAssistantMessage.__table__,
]

_PREFIX = "/api/v2/super-assistant"


def _user(user_id: str, username: str) -> User:
    return User(
        id=user_id, username=username, email=f"{username}@example.com",
        password_hash="unused", role="editor",
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def env(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'search.db'}",
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
        # 与 main.py 相同的前缀挂载搜索子路由
        app.include_router(search.router, prefix=_PREFIX)
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    return SimpleNamespace(
        client=make_client(_user("user-1", "owner")),
        make_client=make_client,
        session=TestingSession,
    )


def _conversation(owner_id: str, title: str, status: str = "active") -> SuperAssistantConversation:
    return SuperAssistantConversation(
        id=str(uuid.uuid4()), owner_id=owner_id, title=title,
        status=status, created_at=_now(), updated_at=_now(),
    )


def _message(conversation_id: str, role: str, content: str, status: str = "complete") -> SuperAssistantMessage:
    return SuperAssistantMessage(
        id=str(uuid.uuid4()), conversation_id=conversation_id, role=role,
        content=content, status=status, steps=[], token_usage={}, created_at=_now(),
    )


def _search(client: TestClient, q: str) -> dict:
    response = client.get(f"{_PREFIX}/search/conversations", params={"q": q})
    assert response.status_code == 200, response.text
    return response.json()


def test_search_matches_conversation_title(env):
    with env.session() as db:
        db.add(_conversation("user-1", "供应链需求梳理"))
        db.add(_conversation("user-1", "周报杂谈"))
        db.commit()

    result = _search(env.client, "需求")
    assert [item["title"] for item in result["conversations"]] == ["供应链需求梳理"]
    hit = result["conversations"][0]
    assert hit["titleMatched"] is True
    assert hit["messageHits"] == []
    assert hit["status"] == "active"


def test_search_matches_message_content_with_snippet(env):
    with env.session() as db:
        conversation = _conversation("user-1", "普通标题")
        db.add(conversation)
        db.flush()
        filler = "很长的上下文前缀" * 10
        db.add(_message(conversation.id, "user", f"{filler}库存周转率指标异常{filler}"))
        # streaming 占位消息不参与检索
        db.add(_message(conversation.id, "assistant", "库存周转率", status="streaming"))
        db.commit()

    result = _search(env.client, "库存周转率")
    assert len(result["conversations"]) == 1
    hit = result["conversations"][0]
    assert hit["titleMatched"] is False
    assert len(hit["messageHits"]) == 1
    message_hit = hit["messageHits"][0]
    assert "库存周转率" in message_hit["snippet"]
    assert message_hit["snippet"].startswith("…")
    assert message_hit["snippet"].endswith("…")
    assert message_hit["role"] == "user"


def test_search_is_scoped_to_current_user(env):
    with env.session() as db:
        mine = _conversation("user-1", "我的本体讨论")
        db.add(mine)
        db.flush()
        db.add(_message(mine.id, "user", "讨论本体建模"))
        theirs = _conversation("user-2", "别人的本体讨论")
        db.add(theirs)
        db.flush()
        db.add(_message(theirs.id, "user", "讨论本体建模"))
        db.commit()

    result = _search(env.client, "本体")
    assert [item["title"] for item in result["conversations"]] == ["我的本体讨论"]
    other_client = env.make_client(_user("user-2", "other"))
    other_result = _search(other_client, "本体")
    assert [item["title"] for item in other_result["conversations"]] == ["别人的本体讨论"]


def test_search_blank_query_returns_empty(env):
    with env.session() as db:
        db.add(_conversation("user-1", "供应链需求梳理"))
        db.commit()

    assert _search(env.client, "   ")["conversations"] == []
    assert _search(env.client, "")["conversations"] == []


def test_search_like_wildcards_are_literal(env):
    with env.session() as db:
        db.add(_conversation("user-1", "达成率 100% 的复盘"))
        db.add(_conversation("user-1", "达成率 1000 的复盘"))
        db.commit()

    result = _search(env.client, "100%")
    assert [item["title"] for item in result["conversations"]] == ["达成率 100% 的复盘"]


def test_search_includes_archived_conversations(env):
    with env.session() as db:
        db.add(_conversation("user-1", "归档的需求讨论", status="archived"))
        db.commit()

    result = _search(env.client, "需求")
    assert len(result["conversations"]) == 1
    assert result["conversations"][0]["status"] == "archived"
