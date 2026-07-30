import uuid

from app.model_configs.models import ModelConfig


BASE = "/api/v2/exploration"


def test_tool_budget_exhaustion_reserves_a_tool_free_final_summary(
        client, auth_headers, admin_user, db, monkeypatch):
    config = ModelConfig(
        id=str(uuid.uuid4()),
        name="final-summary-fake",
        config_type="llm",
        provider="compatible",
        api_key_encrypted="",
        api_base="https://example.invalid",
        models=["fake"],
        options={},
        enabled=True,
        is_default=True,
        created_by=admin_user.id,
    )
    db.add(config)
    db.commit()
    created = client.post(f"{BASE}/sessions", headers=auth_headers, json={})
    session_id = created.json()["data"]["id"]
    calls = {"count": 0}

    def fake_chat(call_kwargs, messages, tools):
        calls["count"] += 1
        if calls["count"] <= 8:
            assert tools
            return {
                "content": None,
                "tool_calls": [{
                    "id": f"read-{calls['count']}",
                    "name": "get_canvas_elements",
                    "arguments": {"kind": "object"},
                }],
                "usage": {"inputTokens": 1, "outputTokens": 1},
            }
        assert tools == []
        assert "工具调用预算已用尽" in messages[-1]["content"]
        assert '"canvasVersion"' in messages[-1]["content"]
        return {
            "content": "已完成 8 次权威画布读取；本轮没有画布写入，下一步请先确认业务范围。",
            "tool_calls": [],
            "usage": {"inputTokens": 2, "outputTokens": 3},
        }

    from app.ontologies.agent_runtime import llm_bridge
    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    response = client.post(
        f"{BASE}/sessions/{session_id}/chat",
        headers=auth_headers,
        json={"message": "持续读取画布直到预算耗尽", "stream": False},
    )
    data = response.json()["data"]

    assert response.status_code == 200
    assert calls["count"] == 9
    assert len(data["steps"]) == 8
    assert "没有画布写入" in data["content"]
    assert data["usage"] == {"inputTokens": 10, "outputTokens": 11}
