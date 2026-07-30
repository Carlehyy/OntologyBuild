import uuid
from types import SimpleNamespace

from app.data_channel.steward.models import StewardConversation, StewardMessage


BASE = "/api/v2/steward"


def _add_model(db, admin_user, name: str):
    from app.models.model_config import ModelConfig

    db.add(ModelConfig(
        id=str(uuid.uuid4()),
        name=name,
        provider="openai",
        config_type="llm",
        models=["fake-model"],
        enabled=True,
        created_by=admin_user.id,
    ))
    db.commit()


def test_steward_chat_with_web_search_exposes_tool_and_persists_sources(
    client, auth_headers, db, admin_user, monkeypatch,
):
    from app.data_channel.steward import orchestrator
    from app.ontologies.agent_runtime import llm_bridge

    _add_model(db, admin_user, "fake-steward-search-model")
    conversation = client.post(
        f"{BASE}/conversations",
        headers=auth_headers,
        json={"title": "联网采集"},
    ).json()["data"]
    sources = [{
        "title": "公开数据接口说明",
        "url": "https://example.com/open-data",
        "snippet": "公开接口字段与更新频率说明",
    }]
    monkeypatch.setattr(orchestrator, "search_web", lambda query: sources)

    captured = {"calls": 0}

    def fake_chat(_kwargs, messages, tools):
        captured["calls"] += 1
        if captured["calls"] == 1:
            assert [tool["name"] for tool in tools].count("web_search") == 1
            assert "用户已为本回合开启联网检索" in messages[0]["content"]
            return {
                "content": None,
                "tool_calls": [{
                    "id": "search-1",
                    "name": "web_search",
                    "arguments": {"query": "公开 数据接口 更新频率"},
                }],
                "usage": None,
            }
        assert messages[-1]["role"] == "tool"
        assert "https://example.com/open-data" in messages[-1]["content"]
        assert "untrustedExternalContent" in messages[-1]["content"]
        return {
            "content": "已核对公开资料，可继续设计采集链路。",
            "tool_calls": [],
            "usage": None,
        }

    monkeypatch.setattr(llm_bridge, "chat", fake_chat)
    response = client.post(
        f"{BASE}/chat",
        headers=auth_headers,
        json={
            "message": "联网查一下这个公开数据源",
            "conversationId": conversation["id"],
            "webSearch": True,
            "stream": False,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["steps"][0]["tool"] == "web_search"
    assert data["steps"][0]["searchResults"] == sources
    saved = db.query(StewardMessage).filter(
        StewardMessage.conversation_id == conversation["id"],
        StewardMessage.role == "assistant",
    ).one()
    assert saved.steps[0]["searchResults"] == sources


def test_steward_chat_without_web_search_does_not_expose_tool(
    client, auth_headers, db, admin_user, monkeypatch,
):
    from app.data_channel.steward import orchestrator
    from app.ontologies.agent_runtime import llm_bridge

    _add_model(db, admin_user, "fake-steward-offline-model")

    monkeypatch.setattr(
        orchestrator,
        "search_web",
        lambda _query: (_ for _ in ()).throw(AssertionError("离线回合不得搜索")),
    )

    def fake_chat(_kwargs, messages, tools):
        assert all(tool["name"] != "web_search" for tool in tools)
        assert "本回合未开启联网检索" in messages[0]["content"]
        return {"content": "本回合按离线模式继续。", "tool_calls": [], "usage": None}

    monkeypatch.setattr(llm_bridge, "chat", fake_chat)
    response = client.post(
        f"{BASE}/chat",
        headers=auth_headers,
        json={"message": "继续编排", "webSearch": False, "stream": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["steps"] == []


def test_steward_turn_rejects_another_users_conversation(db):
    from app.data_channel.steward.orchestrator import run_steward_turn

    conversation = StewardConversation(user_id="owner-user", title="他人的会话")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    events = list(run_steward_turn(
        db,
        SimpleNamespace(id="another-user", role="editor"),
        "继续处理",
        conversation_id=conversation.id,
    ))

    assert events[0] == {"type": "error", "message": "无权访问他人会话"}
    assert events[-1] == {"type": "done"}
    assert db.query(StewardMessage).filter(
        StewardMessage.conversation_id == conversation.id,
    ).count() == 0
