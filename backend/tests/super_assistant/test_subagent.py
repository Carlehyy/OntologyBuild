from __future__ import annotations

import json
from unittest.mock import Mock

from app.shared.config import settings
from app.super_assistant import subagent


_CALL_KWARGS = {"provider": "openai", "model": "fake-model"}


def _tool_round(call_id, name, arguments):
    return {
        "content": None,
        "tool_calls": [{"id": call_id, "name": name, "arguments": arguments}],
        "usage": {},
    }


def _text_round(content):
    return {"content": content, "tool_calls": [], "usage": {}}


def test_subagent_runs_tool_loop_and_returns_final_text(monkeypatch):
    responses = iter([
        _tool_round("call-1", "think", {"thought": "先梳理任务"}),
        _text_round("最终结论"),
    ])
    seen: list = []
    monkeypatch.setattr(
        subagent.provider, "chat",
        lambda _kw, messages, tools: seen.append({"messages": list(messages), "tools": tools}) or next(responses),
    )
    result = subagent.run_subagent(Mock(), "user-1", _CALL_KWARGS, "调查某个主题")
    assert result == "最终结论"
    tool_names = {tool["name"] for tool in seen[0]["tools"]}
    assert tool_names == {"use_skill", "read_skill_file", "web_fetch", "think"}
    round_two_contents = [message.get("content") for message in seen[1]["messages"]]
    assert "已记录：先梳理任务" in round_two_contents


def test_subagent_includes_web_search_tool_only_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_web_search_backend", "tavily")
    responses = iter([_text_round("完成")])
    seen: list = []
    monkeypatch.setattr(
        subagent.provider, "chat",
        lambda _kw, _messages, tools: seen.append(tools) or next(responses),
    )
    subagent.run_subagent(Mock(), "user-1", _CALL_KWARGS, "任务")
    assert "web_search" in {tool["name"] for tool in seen[0]}


def test_subagent_rejects_tools_outside_whitelist(monkeypatch):
    responses = iter([
        _tool_round("call-1", "run_subagent", {"task": "嵌套"}),
        _tool_round("call-2", "mcp__evil__delete", {}),
        _text_round("已停止违规调用"),
    ])
    seen: list = []
    monkeypatch.setattr(
        subagent.provider, "chat",
        lambda _kw, messages, _tools: seen.append(list(messages)) or next(responses),
    )
    result = subagent.run_subagent(Mock(), "user-1", _CALL_KWARGS, "任务")
    assert result == "已停止违规调用"
    tool_outputs = [
        message["content"] for message in seen[-1] if message["role"] == "tool"
    ]
    assert all("不允许使用工具" in output for output in tool_outputs)


def test_subagent_stops_at_round_cap_with_latest_conclusion(monkeypatch):
    rounds = [
        {"content": f"中间结论{index}", "tool_calls": [
            {"id": f"call-{index}", "name": "think", "arguments": {"thought": "继续"}},
        ], "usage": {}}
        for index in (1, 2, 3)
    ]
    responses = iter(rounds)
    calls = {"count": 0}

    def fake_chat(*_args, **_kwargs):
        calls["count"] += 1
        return next(responses)

    monkeypatch.setattr(subagent.provider, "chat", fake_chat)
    result = subagent.run_subagent(Mock(), "user-1", _CALL_KWARGS, "任务", max_rounds=2)
    assert result == "子代理未能在限定轮次内完成：中间结论2"
    assert calls["count"] == 2


def test_subagent_uses_settings_round_cap_by_default(monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_subagent_max_rounds", 3)
    responses = iter([
        _tool_round(f"call-{index}", "think", {"thought": "t"}) for index in range(5)
    ])
    calls = {"count": 0}

    def fake_chat(*_args, **_kwargs):
        calls["count"] += 1
        return next(responses)

    monkeypatch.setattr(subagent.provider, "chat", fake_chat)
    result = subagent.run_subagent(Mock(), "user-1", _CALL_KWARGS, "任务")
    assert result.startswith("子代理未能在限定轮次内完成：")
    assert calls["count"] == 3


def test_subagent_executes_skill_tools_via_runtime_builtin(monkeypatch):
    executed: list = []

    def fake_builtin(db, owner_id, name, arguments):
        executed.append((owner_id, name, arguments))
        return json.dumps({"skill": arguments.get("name"), "skill_md": "步骤"}, ensure_ascii=False)

    monkeypatch.setattr(subagent, "_execute_builtin", fake_builtin)
    responses = iter([
        _tool_round("call-1", "use_skill", {"name": "qa-skill"}),
        _text_round("按 Skill 完成"),
    ])
    seen: list = []
    monkeypatch.setattr(
        subagent.provider, "chat",
        lambda _kw, messages, _tools: seen.append(list(messages)) or next(responses),
    )
    result = subagent.run_subagent(Mock(), "user-1", _CALL_KWARGS, "任务")
    assert result == "按 Skill 完成"
    assert executed == [("user-1", "use_skill", {"name": "qa-skill"})]
    tool_outputs = [
        message["content"] for message in seen[-1] if message["role"] == "tool"
    ]
    assert json.loads(tool_outputs[0])["skill_md"] == "步骤"


def test_subagent_executes_web_search_and_returns_json(monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_web_search_backend", "tavily")
    monkeypatch.setattr(
        subagent, "web_search",
        lambda query, max_results=5: [{"title": "t", "url": "u", "snippet": "s"}],
    )
    responses = iter([
        _tool_round("call-1", "web_search", {"query": "本体"}),
        _text_round("检索完成"),
    ])
    seen: list = []
    monkeypatch.setattr(
        subagent.provider, "chat",
        lambda _kw, messages, _tools: seen.append(list(messages)) or next(responses),
    )
    subagent.run_subagent(Mock(), "user-1", _CALL_KWARGS, "任务")
    tool_outputs = [
        message["content"] for message in seen[-1] if message["role"] == "tool"
    ]
    assert json.loads(tool_outputs[0]) == [{"title": "t", "url": "u", "snippet": "s"}]
