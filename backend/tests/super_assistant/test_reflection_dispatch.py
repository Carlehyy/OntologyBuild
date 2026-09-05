"""超级助手反思任务的 NATS 派发与消费注册测试（不起真实 NATS）。"""
from __future__ import annotations

import pytest

from app.data_channel.pipeline_tasks import dispatch as dispatch_module
from app.data_channel.pipeline_tasks import nats_executor
from app.data_channel.pipeline_tasks.dispatch import (
    PIPELINE_STREAM_SUBJECTS,
    SUPER_ASSISTANT_REFLECT_FOCUSED_SUBJECT,
    SUPER_ASSISTANT_REFLECT_FULL_SUBJECT,
    SUPER_ASSISTANT_REFLECT_MICRO_SUBJECT,
    dispatch_super_assistant_reflection,
)
from app.super_assistant import reflection_tasks


def test_reflect_subjects_appended_to_stream_subjects():
    # 扩容只能追加：三条反思 subject 紧随既有主题，旧 subject 不受影响
    # （Celery 退役新增的 3 个 subject 插在第 3 位起，旧主题相对顺序不变）
    assert PIPELINE_STREAM_SUBJECTS[:3] == (
        "pipeline.task.execute",
        "task.pipeline.run",
        "task.dataset.import",
    )
    assert PIPELINE_STREAM_SUBJECTS[6:9] == (
        SUPER_ASSISTANT_REFLECT_MICRO_SUBJECT,
        SUPER_ASSISTANT_REFLECT_FULL_SUBJECT,
        SUPER_ASSISTANT_REFLECT_FOCUSED_SUBJECT,
    )
    assert SUPER_ASSISTANT_REFLECT_MICRO_SUBJECT == "super_assistant.reflect.micro"
    assert SUPER_ASSISTANT_REFLECT_FULL_SUBJECT == "super_assistant.reflect.full"
    assert SUPER_ASSISTANT_REFLECT_FOCUSED_SUBJECT == "super_assistant.reflect.focused"


def test_handler_registry_registers_reflect_durables():
    registry = nats_executor._handler_registry()
    entries = {
        subject: (durable, handler)
        for subject, durable, handler in registry
    }
    assert entries[SUPER_ASSISTANT_REFLECT_MICRO_SUBJECT] == (
        "super-assistant-reflect-micro",
        reflection_tasks.run_micro_reflection_message,
    )
    assert entries[SUPER_ASSISTANT_REFLECT_FULL_SUBJECT] == (
        "super-assistant-reflect-full",
        reflection_tasks.run_full_reflection_message,
    )
    assert entries[SUPER_ASSISTANT_REFLECT_FOCUSED_SUBJECT] == (
        "super-assistant-reflect-focused",
        reflection_tasks.run_focused_reflection_message,
    )


def test_dispatch_super_assistant_reflection_routes_kind_to_subject(monkeypatch):
    sent = []
    monkeypatch.setattr(
        dispatch_module,
        "dispatch_task",
        lambda subject, payload: sent.append((subject, payload)),
    )

    micro_payload = {"owner_id": "u1", "conversation_id": "c1", "message_id": "m1"}
    dispatch_super_assistant_reflection("micro", micro_payload)
    assert sent == [("super_assistant.reflect.micro", micro_payload)]

    full_payload = {"owner_id": "u1", "conversation_id": "c1"}
    dispatch_super_assistant_reflection("full", full_payload)
    assert sent[-1] == ("super_assistant.reflect.full", full_payload)

    focused_payload = {
        "owner_id": "u1",
        "conversation_id": "c1",
        "message_id": "m1",
        "hint": "沉淀流程",
    }
    dispatch_super_assistant_reflection("focused", focused_payload)
    assert sent[-1] == ("super_assistant.reflect.focused", focused_payload)

    with pytest.raises(ValueError, match="未知的反思任务类型"):
        dispatch_super_assistant_reflection("hourly", {})


class _FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _stub_session_local(monkeypatch) -> list:
    sessions = []

    def fake_session_local():
        session = _FakeSession()
        sessions.append(session)
        return session

    monkeypatch.setattr("app.database.SessionLocal", fake_session_local)
    return sessions


@pytest.mark.asyncio
async def test_micro_handler_opens_session_and_calls_service(monkeypatch):
    sessions = _stub_session_local(monkeypatch)
    calls = []
    monkeypatch.setattr(
        "app.super_assistant.reflection_service.run_micro_reflection",
        lambda db, owner_id, conversation_id, message_id: calls.append(
            (owner_id, conversation_id, message_id)
        ),
    )

    await reflection_tasks.run_micro_reflection_message({
        "owner_id": "u1",
        "conversation_id": "c1",
        "message_id": "m1",
    })

    assert calls == [("u1", "c1", "m1")]
    assert len(sessions) == 1
    assert sessions[0].closed is True


@pytest.mark.asyncio
async def test_full_handler_swallows_business_exceptions(monkeypatch):
    sessions = _stub_session_local(monkeypatch)

    def failing_run(*_args):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "app.super_assistant.reflection_service.run_full_reflection",
        failing_run,
    )

    # 业务异常只在 handler 内记日志：不向外抛（避免 executor nak 重投）
    await reflection_tasks.run_full_reflection_message({
        "owner_id": "u1",
        "conversation_id": "c1",
    })

    assert sessions[0].closed is True


@pytest.mark.asyncio
async def test_focused_handler_passes_hint_and_closes_session(monkeypatch):
    sessions = _stub_session_local(monkeypatch)
    calls = []
    monkeypatch.setattr(
        "app.super_assistant.reflection_service.run_focused_reflection",
        lambda db, owner_id, conversation_id, message_id, hint: calls.append(
            (owner_id, conversation_id, message_id, hint)
        ),
    )

    await reflection_tasks.run_focused_reflection_message({
        "owner_id": "u1",
        "conversation_id": "c1",
        "message_id": "m1",
        "hint": "沉淀流程",
    })

    assert calls == [("u1", "c1", "m1", "沉淀流程")]
    assert sessions[0].closed is True
