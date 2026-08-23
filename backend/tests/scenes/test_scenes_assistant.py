"""场景建模助手测试：会话、SSE 事件契约、set_definition 应用与失败路径。

LLM seam 在 app.scenes.assistant_service 模块命名空间打补丁，
不触网；SSE 文本按 event/data 帧解析后断言事件序列。
"""
import json

from app.scenes import assistant_service

VALID_DEFINITION = {
    "meta": {"id": "assistant-park", "name": "助手园区", "version": "0.1.0"},
    "objects": [
        {"id": "office", "label": "办公楼", "type": "office",
         "layout": {"x": -20, "z": 0, "w": 12, "d": 10, "h": 16}},
    ],
}


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split(chr(10) * 2):
        if not block.strip():
            continue
        event = None
        data = None
        for line in block.split(chr(10)):
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if event is not None:
            events.append((event, data))
    return events


def _patch_llm(monkeypatch, payload=None, *, config=None, call_kwargs=None):
    monkeypatch.setattr(
        assistant_service, "select_llm_model_config",
        lambda db, model_id=None: config or object())
    monkeypatch.setattr(
        assistant_service, "llm_call_kwargs",
        lambda cfg: call_kwargs or {
            "provider": "openai", "api_key": "k",
            "api_base": None, "model": "test-model"})
    if payload is not None:
        monkeypatch.setattr(
            assistant_service, "_call_llm",
            lambda **kwargs: json.dumps(payload, ensure_ascii=False))


def _create_conversation(client, auth_headers, body=None):
    resp = client.post("/api/v2/scenes/conversations",
                       json=body or {}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _chat(client, auth_headers, conversation_id, content, model_id=None):
    body = {"content": content}
    if model_id:
        body["model_config_id"] = model_id
    return client.post(
        f"/api/v2/scenes/conversations/{conversation_id}/chat",
        json=body, headers=auth_headers)


def test_create_conversation_without_scene_starts_unbound(client, auth_headers):
    conversation = _create_conversation(client, auth_headers)
    assert conversation["scene_id"] is None
    listing = client.get("/api/v2/scenes/conversations",
                         headers=auth_headers).json()["data"]
    assert listing["total"] == 1


def test_create_conversation_rejects_unknown_scene(client, auth_headers):
    resp = client.post("/api/v2/scenes/conversations",
                       json={"scene_id": "no-such"}, headers=auth_headers)
    assert resp.status_code == 404


def test_chat_set_definition_creates_bound_scene_and_frozen_version(
    client, auth_headers, monkeypatch,
):
    _patch_llm(monkeypatch, {
        "action": "set_definition",
        "definition": VALID_DEFINITION,
        "note": "初版布局",
    })
    conversation = _create_conversation(client, auth_headers)
    resp = _chat(client, auth_headers, conversation["id"], "帮我建一个园区场景")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    names = [name for name, _ in events]
    assert names == ["meta", "scene_updated", "done"]
    updated = events[1][1]
    assert updated["name"] == "助手园区"
    assert updated["version_no"] == 1
    assert updated["status"] == "draft"

    # 会话已绑定到新建场景，且版本来源为 assistant
    refreshed = client.get("/api/v2/scenes/conversations",
                           headers=auth_headers).json()["data"]["items"][0]
    assert refreshed["scene_id"] == updated["scene_id"]
    versions = client.get(
        f"/api/v2/scenes/{updated['scene_id']}/versions",
        headers=auth_headers).json()["data"]
    assert versions["items"][0]["source"] == "assistant"

    messages = client.get(
        f"/api/v2/scenes/conversations/{conversation['id']}/messages",
        headers=auth_headers).json()["data"]
    assert [m["role"] for m in messages["items"]] == ["user", "assistant"]
    assert messages["items"][1]["version_no"] == 1


def test_chat_incremental_update_bumps_version_on_existing_scene(
    client, auth_headers, monkeypatch,
):
    scene = client.post("/api/v2/scenes", json={"name": "既有场景"},
                        headers=auth_headers).json()["data"]
    conversation = _create_conversation(
        client, auth_headers, {"scene_id": scene["id"]})
    updated_definition = {
        **VALID_DEFINITION,
        "meta": {**VALID_DEFINITION["meta"], "version": "0.2.0"},
    }
    _patch_llm(monkeypatch, {
        "action": "set_definition",
        "definition": updated_definition,
        "note": "追加对象",
    })
    resp = _chat(client, auth_headers, conversation["id"], "再加一栋楼")
    events = _parse_sse(resp.text)
    applied = [data for name, data in events if name == "scene_updated"][0]
    assert applied["scene_id"] == scene["id"]
    assert applied["version_no"] == 1


def test_chat_reply_action_returns_text_only(client, auth_headers, monkeypatch):
    _patch_llm(monkeypatch, {"action": "reply", "message": "请说明建筑数量"})
    conversation = _create_conversation(client, auth_headers)
    resp = _chat(client, auth_headers, conversation["id"], "建个场景")
    events = _parse_sse(resp.text)
    names = [name for name, _ in events]
    assert names == ["meta", "text", "done"]
    assert events[1][1]["content"] == "请说明建筑数量"


def test_chat_invalid_definition_yields_structured_error(
    client, auth_headers, monkeypatch,
):
    bad = {**VALID_DEFINITION, "objects": []}
    _patch_llm(monkeypatch, {"action": "set_definition", "definition": bad})
    conversation = _create_conversation(client, auth_headers)
    resp = _chat(client, auth_headers, conversation["id"], "空场景")
    events = _parse_sse(resp.text)
    errors = [data for name, data in events if name == "error"]
    assert errors and errors[0]["code"] == "invalid_definition"
    assert errors[0]["issues"]
    assert events[-1][0] == "done"
    # 失败不产生任何场景
    listing = client.get("/api/v2/scenes", headers=auth_headers).json()["data"]
    assert listing["total"] == 0


def test_chat_without_model_reports_model_unavailable(
    client, auth_headers, monkeypatch,
):
    monkeypatch.setattr(assistant_service, "select_llm_model_config",
                        lambda db, model_id=None: None)
    conversation = _create_conversation(client, auth_headers)
    resp = _chat(client, auth_headers, conversation["id"], "你好")
    events = _parse_sse(resp.text)
    errors = [data for name, data in events if name == "error"]
    assert errors and errors[0]["code"] == "model_unavailable"


def test_chat_unknown_conversation_returns_404(client, auth_headers):
    resp = _chat(client, auth_headers, "no-such", "你好")
    assert resp.status_code == 404
