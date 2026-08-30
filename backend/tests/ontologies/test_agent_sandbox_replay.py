"""沙箱试跑（M2 双臂实验的 agent_runtime 侧）语义测试。

覆盖：profile_override 换用草稿提示词而生产配置零改动、sandbox 会话
打标且用户侧列表不可见、model_swap 经草稿 default_model_id 生效、
delete_conversation 连消息一起清理。
"""
from __future__ import annotations

import uuid

from app.ontologies.agent_runtime import llm_bridge
from app.ontologies.agent_runtime.conversation_service import (
    delete_conversation,
    list_conversations,
)
from app.ontologies.agent_runtime.models import (
    AgentConversation,
    AgentMessage,
    AgentProfile,
)
from app.ontologies.agent_runtime.orchestrator import run_agent_turn


def _add_llm_config(db, admin_user, name: str, model: str | None = None):
    from app.models.model_config import ModelConfig

    config = ModelConfig(
        id=str(uuid.uuid4()), name=name, provider="openai", config_type="llm",
        models=[model or f"{name}-model"], created_by=admin_user.id,
    )
    db.add(config)
    db.commit()
    return config


def _draft_clone(profile: AgentProfile, **overrides) -> AgentProfile:
    """复制边界字段的只读草稿 profile（不落库），仅覆盖指定字段。"""
    fields = {
        "ontology_id": profile.ontology_id,
        "enabled": True,
        "allowed_object_type_ids": profile.allowed_object_type_ids,
        "allowed_link_type_ids": profile.allowed_link_type_ids,
        "allowed_action_ids": profile.allowed_action_ids,
        "allow_action_proposals": profile.allow_action_proposals,
        "max_rows_per_query": profile.max_rows_per_query,
        "max_steps": profile.max_steps,
        "system_prompt_extra": profile.system_prompt_extra,
        "default_model_id": profile.default_model_id,
    }
    fields.update(overrides)
    return AgentProfile(**fields)


def _turn(db, ontology, admin_user, question: str, **kwargs) -> list[dict]:
    return list(run_agent_turn(db, ontology["id"], admin_user, question, **kwargs))


def _conversation_id(events: list[dict]) -> str:
    return next(e["conversationId"] for e in events if e["type"] == "meta")


def test_profile_override_changes_prompt_and_marks_sandbox(
        client, auth_headers, ontology, db, admin_user, monkeypatch):
    _add_llm_config(db, admin_user, "sandbox-fake")
    captured: dict = {}

    def fake_chat(call_kwargs, messages, tools):
        captured["system"] = messages[0]["content"]
        return {"content": "好的", "tool_calls": [],
                "usage": {"inputTokens": 1, "outputTokens": 1}}

    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    # 生产回合：初始化 profile 并留下可见会话
    prod_events = _turn(db, ontology, admin_user, "生产问题")
    assert any(e["type"] == "answer" for e in prod_events)
    profile = db.query(AgentProfile).filter_by(ontology_id=ontology["id"]).first()
    assert profile is not None

    # 沙箱回合：换用草稿提示词
    draft = _draft_clone(profile, system_prompt_extra="【草稿标记】回答必须简洁。")
    sandbox_events = _turn(db, ontology, admin_user, "沙箱问题",
                           profile_override=draft, sandbox=True)
    assert any(e["type"] == "answer" for e in sandbox_events)
    assert "【草稿标记】" in captured["system"]                      # 草稿生效
    db.refresh(profile)
    assert "【草稿标记】" not in (profile.system_prompt_extra or "")  # 生产零改动

    # 沙箱会话打标，用户侧列表不可见；生产会话可见
    sandbox_conv_id = _conversation_id(sandbox_events)
    conv = db.query(AgentConversation).filter_by(id=sandbox_conv_id).first()
    assert conv.is_sandbox is True
    assert conv.title.startswith("[评估沙箱]")
    visible_ids = {c["id"] for c in
                   list_conversations(db, ontology["id"], None, admin_user)}
    assert sandbox_conv_id not in visible_ids
    assert _conversation_id(prod_events) in visible_ids

    # 清理：消息 + 会话一起删
    delete_conversation(db, conv)
    assert db.query(AgentMessage).filter_by(conversation_id=sandbox_conv_id).count() == 0
    assert db.query(AgentConversation).filter_by(id=sandbox_conv_id).first() is None


def test_model_swap_uses_draft_default_model(
        client, auth_headers, ontology, db, admin_user, monkeypatch):
    cfg_a = _add_llm_config(db, admin_user, "swap-a", model="model-a")
    cfg_b = _add_llm_config(db, admin_user, "swap-b", model="model-b")
    captured: dict = {}

    def fake_chat(call_kwargs, messages, tools):
        captured["model"] = call_kwargs.get("model")
        return {"content": "好的", "tool_calls": [],
                "usage": {"inputTokens": 1, "outputTokens": 1}}

    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    profile = db.query(AgentProfile).filter_by(ontology_id=ontology["id"]).first()
    if profile is None:
        profile = AgentProfile(ontology_id=ontology["id"], allowed_action_ids=[])
        db.add(profile)
    profile.default_model_id = str(cfg_a.id)
    db.commit()

    prod_events = _turn(db, ontology, admin_user, "生产问题")
    assert captured["model"] == "model-a"

    draft = _draft_clone(profile, default_model_id=str(cfg_b.id))
    sandbox_events = _turn(db, ontology, admin_user, "沙箱问题",
                           profile_override=draft, sandbox=True)
    assert any(e["type"] == "answer" for e in sandbox_events)
    assert captured["model"] == "model-b"        # 换模型杠杆经草稿 default_model_id 生效
    db.refresh(profile)
    assert profile.default_model_id == str(cfg_a.id)  # 生产配置零改动
