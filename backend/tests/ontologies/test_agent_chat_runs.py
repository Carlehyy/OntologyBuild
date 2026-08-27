"""后台回合状态（chat run registry）与「断开不中断回合」语义（MYW-71）。"""
from __future__ import annotations

import time
import uuid

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.ontologies.agent_runtime.chat_runs import (
    FINISHED_TTL_SECONDS,
    ChatRunRegistry,
    chat_run_registry,
)
from app.ontologies.agent_runtime.chat_service import stream_events
from app.ontologies.agent_runtime.orchestrator import run_agent_turn
from app.ontologies.agent_runtime.schemas import ChatRequest


def _fo(ontology_id: str) -> str:
    return f"/api/v2/formal/ontologies/{ontology_id}"


def _add_llm_config(db, admin_user, name: str) -> None:
    from app.models.model_config import ModelConfig
    db.add(ModelConfig(id=str(uuid.uuid4()), name=name, provider="openai",
                       config_type="llm", models=[f"{name}-model"],
                       created_by=admin_user.id))
    db.commit()


def _patch_stream_session(db, monkeypatch) -> None:
    """流式路径由后台线程自建 session；测试里把它指到 fixture 同一引擎，
    否则断言读不到线程写入的数据（应用 SessionLocal 指向独立的测试库）。"""
    import app.database as app_database
    monkeypatch.setattr(app_database, "SessionLocal", sessionmaker(bind=db.get_bind()))


def _wait_terminal(run_id: str, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = chat_run_registry.get(run_id)
        if info is not None and info.status != "running":
            return info.status
        time.sleep(0.02)
    raise AssertionError(f"回合 {run_id} 未在 {timeout}s 内到达终态")


def _conversation_contents(db, conversation_id: str) -> list[tuple[str, str]]:
    rows = db.execute(
        text("SELECT role, content FROM fo_agent_messages "
             "WHERE conversation_id = :cid ORDER BY created_at"),
        {"cid": conversation_id}).fetchall()
    return [(role, content) for role, content in rows]


# ---------------------------------------------------------------- 注册表


def test_registry_lifecycle_running_to_terminal():
    registry = ChatRunRegistry()
    registry.register("run-1", "ontology-1")
    info = registry.get("run-1")
    assert info is not None and info.status == "running"
    assert info.conversation_id is None and info.finished_at is None

    registry.attach_conversation("run-1", "conv-1")
    registry.mark_finished("run-1", "succeeded")
    info = registry.get("run-1")
    assert info.status == "succeeded"
    assert info.conversation_id == "conv-1"
    assert info.finished_at is not None


def test_registry_first_terminal_status_wins():
    registry = ChatRunRegistry()
    registry.register("run-1", "ontology-1")
    registry.mark_finished("run-1", "succeeded")
    registry.mark_finished("run-1", "error")
    assert registry.get("run-1").status == "succeeded"


def test_registry_unknown_run_and_empty_run_id():
    registry = ChatRunRegistry()
    assert registry.get("missing") is None
    # 空 run_id 静默忽略（无 run_id 的回合不进入注册表）
    registry.register("", "ontology-1")
    registry.mark_finished("", "succeeded")
    registry.attach_conversation("", "conv")
    assert registry.get("") is None


def test_registry_expired_finished_entries_evicted():
    now = {"t": 1000.0}
    registry = ChatRunRegistry(now_fn=lambda: now["t"])
    registry.register("run-1", "ontology-1")
    registry.mark_finished("run-1", "error")
    now["t"] += FINISHED_TTL_SECONDS + 1
    assert registry.get("run-1") is None


def test_registry_running_entries_never_expire():
    now = {"t": 1000.0}
    registry = ChatRunRegistry(now_fn=lambda: now["t"])
    registry.register("run-1", "ontology-1")
    now["t"] += FINISHED_TTL_SECONDS * 10
    assert registry.get("run-1") is not None


# ---------------------------------------------------------------- 端点


def test_chat_run_endpoint_reports_unknown_for_missing_run(
        client, auth_headers, ontology):
    oid = ontology["id"]
    r = client.get(f"{_fo(oid)}/agent/chat/runs/run-nope", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "unknown"
    assert r.json()["data"]["conversationId"] is None


def test_chat_run_endpoint_reports_terminal_after_turn(
        client, auth_headers, ontology, db, admin_user, monkeypatch):
    """stream=false 的回合同样经 run_agent_turn 登记终态，可被轮询到。"""
    oid = ontology["id"]
    _add_llm_config(db, admin_user, "run-status-fake")

    from app.ontologies.agent_runtime import llm_bridge

    def fake_chat(call_kwargs, messages, tools):
        return {"content": "只有 SO-001。", "tool_calls": [],
                "usage": {"inputTokens": 5, "outputTokens": 3}}

    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    run_id = "run-status-terminal"
    r = client.post(f"{_fo(oid)}/agent/chat", headers=auth_headers,
                    json={"message": "查询订单", "stream": False, "runId": run_id})
    assert r.status_code == 200, r.text
    conv_id = r.json()["data"]["conversationId"]

    r = client.get(f"{_fo(oid)}/agent/chat/runs/{run_id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "succeeded"
    assert data["conversationId"] == conv_id
    assert data["startedAt"] and data["finishedAt"]


def test_chat_run_endpoint_rejects_unknown_ontology(client, auth_headers):
    r = client.get(f"{_fo('ontology-missing')}/agent/chat/runs/run-x",
                   headers=auth_headers)
    assert r.status_code == 404


# ---------------------------------------------- 断开连接不中断回合


def test_stream_survives_client_disconnect(
        ontology, db, admin_user, monkeypatch):
    """模拟浏览器断开：SSE 生成器仅消费到 meta 即被 close()，回合仍须在
    后台线程执行至终态并把完整回答落库（MYW-71 核心缺陷回归）。"""
    oid = ontology["id"]
    _add_llm_config(db, admin_user, "disconnect-fake")
    _patch_stream_session(db, monkeypatch)

    from app.ontologies.agent_runtime import llm_bridge

    calls = {"n": 0}

    def fake_chat(call_kwargs, messages, tools):
        calls["n"] += 1
        # 模型调用放慢，保证 close() 发生在回合执行中途
        time.sleep(0.3)
        return {"content": "断开后仍在执行的回答。", "tool_calls": [],
                "usage": {"inputTokens": 5, "outputTokens": 5}}

    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    run_id = "run-disconnect-survives"
    stream = stream_events(
        oid, admin_user, ChatRequest(message="哪些订单还没支付？", runId=run_id),
        run_turn_fn=run_agent_turn)

    first = next(stream)
    assert '"type": "meta"' in first
    stream.close()  # 模拟客户端断开：推送终止

    assert _wait_terminal(run_id) == "succeeded"
    assert calls["n"] == 1  # 回合没有被 GeneratorExit 带走

    conv_id = chat_run_registry.get(run_id).conversation_id
    assert conv_id
    rows = _conversation_contents(db, conv_id)
    assert [role for role, _ in rows] == ["user", "assistant"]
    assert rows[1][1] == "断开后仍在执行的回答。"


def test_cancel_still_works_with_decoupled_stream(
        ontology, db, admin_user, monkeypatch):
    """解耦后取消端点仍可停止后台回合（协作式检查点语义保持不变）。"""
    oid = ontology["id"]
    _add_llm_config(db, admin_user, "decouple-cancel-fake")
    _patch_stream_session(db, monkeypatch)

    from app.ontologies.agent_runtime import llm_bridge
    from app.ontologies.agent_runtime.chat_cancel import chat_cancel_registry

    calls = {"n": 0}

    def fake_chat(call_kwargs, messages, tools):
        calls["n"] += 1
        if calls["n"] == 2:
            assert chat_cancel_registry.request_cancel("run-decouple-cancel") is True
        return {"content": None, "usage": {"inputTokens": 5, "outputTokens": 5},
                "tool_calls": [{"id": f"tc{calls['n']}", "name": "search_objects",
                                "arguments": {"object_type": "Order"}}]}

    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    stream = stream_events(
        oid, admin_user,
        ChatRequest(message="会被取消的回合", runId="run-decouple-cancel"),
        run_turn_fn=run_agent_turn)
    next(stream)  # meta
    stream.close()

    assert _wait_terminal("run-decouple-cancel") == "cancelled"
    conv_id = chat_run_registry.get("run-decouple-cancel").conversation_id
    rows = _conversation_contents(db, conv_id)
    assert rows[-1][0] == "assistant" and rows[-1][1] == "[已取消]"
