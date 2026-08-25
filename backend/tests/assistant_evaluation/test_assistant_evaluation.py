"""助手评估（assistant_evaluation）单元与接口测试。

覆盖：轨迹归一化适配器、循环检测算法、维度归一化与根因归类、
降级引擎 JSON 解析、任务创建/执行（内联线程）到报告汇总的全链路。
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.assistant_evaluation import service as eval_service
from app.assistant_evaluation.adapters import Trace, _extract_steps
from app.assistant_evaluation.dimensions import normalize, root_cause_of
from app.assistant_evaluation.engine import detect_action_loop
from app.super_assistant.models import (
    SuperAssistantConversation,
    SuperAssistantMessage,
)


# ---------------------------------------------------------------- 纯函数


class TestExtractSteps:
    def test_super_assistant_shape(self):
        class Row:
            def __init__(self, role, content, steps):
                self.role, self.content, self.steps = role, content, steps

        rows = [
            Row("user", "帮我查一下销量", []),
            Row("assistant", "", [
                {"toolName": "sql_query", "status": "success", "arguments": {"sql": "select 1"},
                 "preview": "[{count: 1}]"},
                {"toolName": "sql_query", "status": "error", "arguments": {"sql": "select bad"},
                 "preview": ""},
            ]),
            Row("assistant", "销量是 1。", []),
        ]
        actions, msgs, errors = _extract_steps(rows, "toolName", "status", ("preview",))
        assert errors == 1
        assert [a["name"] for a in actions] == ["sql_query", "sql_query"]
        assert [a["failed"] for a in actions] == [False, True]
        # 轨迹含全部消息角色，且最终答复可取到
        assert msgs[0]["role"] == "user" and msgs[-1]["content"] == "销量是 1。"

    def test_agent_marker_step_skipped_but_counted(self):
        class Row:
            def __init__(self, role, content, steps):
                self.role, self.content, self.steps = role, content, steps

        rows = [
            Row("user", "做点事", []),
            Row("assistant", "没做完", [{"toolName": "agent", "status": "failed"}]),
        ]
        actions, msgs, errors = _extract_steps(rows, "toolName", "status", ("preview",))
        assert actions == []
        assert errors == 1


class TestActionLoop:
    def test_no_loop_for_distinct_actions(self):
        score = detect_action_loop([
            {"name": "a", "arguments": {"x": 1}},
            {"name": "b", "arguments": {}},
        ])
        assert score == 1.0

    def test_loop_detected_for_repeats(self):
        action = {"name": "query", "arguments": {"sql": "select * from t"}}
        score = detect_action_loop([action, dict(action), dict(action)])
        assert score < 0.5

    def test_short_sequence_is_clean(self):
        assert detect_action_loop([]) == 1.0


class TestDimensions:
    def test_normalize_llm_scale(self):
        dim_keys = __import__("app.assistant_evaluation.dimensions", fromlist=["DIMENSIONS"]).DIMENSIONS
        relevance = dim_keys["relevance"]
        assert normalize(relevance, 1) == 0.0
        assert normalize(relevance, 5) == 100.0
        assert normalize(relevance, 3) == 50.0

    def test_root_cause_buckets(self):
        scores = {"hallucination": 40.0, "relevance": 90.0}
        assert root_cause_of(scores, {}) .startswith("模型问题")
        assert root_cause_of({"relevance": 95.0}, {"loop_detected": True}).startswith("工具问题")
        assert root_cause_of({"relevance": 90.0}, {}) == "整体良好"


# ---------------------------------------------------------------- 引擎解析


class TestFallbackParsing:
    def test_parse_tolerates_dirty_json(self):
        from app.assistant_evaluation.engine import _parse_judge_json

        assert _parse_judge_json('前缀 {"score": 4, "reason": "r"} 后缀')["score"] == 4
        assert _parse_judge_json(None) == {}
        assert _parse_judge_json("完全不是 JSON") == {}


@pytest.mark.asyncio
async def test_fallback_engine_scores_trace(monkeypatch):
    """降级引擎走平台 llm_gateway 的桩，验证维度评分装配。"""
    import app.assistant_evaluation.engine as eng

    engine = eng.FallbackEngine.__new__(eng.FallbackEngine)
    engine._kwargs = {}

    def fake_gateway_judge(llm_kwargs, system, user):
        if "Agent 行为分析" in system:
            payload = {"score": 1, "reason": "路径高效"}
        else:
            payload = {"score": 5, "reason": "无问题"}
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(eng, "_gateway_judge", fake_gateway_judge)
    trace = Trace(query="q", response="ans",
                  openai_messages=[{"role": "user", "content": "q"},
                                   {"role": "assistant", "content": "ans"}],
                  actions=[{"name": "t", "arguments": {}}, {"name": "u", "arguments": {}}],
                  tool_error_count=0)
    out = await engine.evaluate(["relevance", "trajectory", "action_loop"], trace)
    assert out["relevance"]["raw"] == 5
    assert out["trajectory"]["raw"] == 1
    assert out["action_loop"]["raw"] == 1.0


@pytest.mark.skipif(
    not __import__("app.assistant_evaluation.engine",
                   fromlist=["openjudge_available"]).openjudge_available(),
    reason="py-openjudge 未安装（CI 环境）",
)
@pytest.mark.asyncio
async def test_openjudge_engine_with_stub_model(monkeypatch):
    """OpenJudge 官方评分器链路：以桩模型替代真实 LLM，验证分数与理由产出。"""
    import asyncio

    import app.assistant_evaluation.engine as eng

    captured = {}

    class StubModel:
        model = "stub"
        stream = False

        async def achat(self, messages=None, structured_model=None, callback=None, **kw):
            captured["structured"] = structured_model is not None
            if structured_model is not None:
                payload = {"score": 4, "reason": "基本切题"}
                return ChatResponse(content=json.dumps(payload),
                                    parsed=dict(payload))
            return ChatResponse(content="ok")

        async def generate(self, *args, **kwargs):  # 兼容不同调用面
            return await self.achat(*args, **kwargs)

    from openjudge.models.schema.oai.response import ChatResponse

    monkeypatch.setattr(eng, "OpenAIChatModel", lambda **kw: StubModel())
    fake_cfg = SimpleNamespace(id="cfg", name="judge", provider="openai",
                               api_base="https://example.invalid/v1",
                               models=["stub-model"], api_key_encrypted=None, options={})
    engine = eng.OpenJudgeEngine(fake_cfg)
    trace = Trace(query="什么是本体建模？", response="把业务概念抽象成类、属性和关系。",
                  openai_messages=[{"role": "user", "content": "什么是本体建模？"},
                                   {"role": "assistant", "content": "把业务概念抽象成类、属性和关系。"}],
                  actions=[{"name": "t", "arguments": {}}, {"name": "u", "arguments": {}}],
                  tool_error_count=0)
    out = await engine.evaluate(["relevance", "action_loop"], trace)
    assert out["relevance"]["raw"] == 4
    assert out["action_loop"]["raw"] == 1.0


@pytest.mark.skipif(
    not __import__("app.assistant_evaluation.engine",
                   fromlist=["openjudge_available"]).openjudge_available(),
    reason="py-openjudge 未安装（CI 环境）",
)
@pytest.mark.asyncio
async def test_openjudge_engine_falls_back_on_grader_error(monkeypatch):
    """官方评分器返回 GraderError（如端点不支持结构化输出）→ 自动走网关降级。"""
    import asyncio
    from types import SimpleNamespace

    import app.assistant_evaluation.engine as eng

    class AlwaysErrorGrader:
        async def aevaluate(self, **kwargs):
            return GraderError(name="relevance", error="structured output unsupported",
                               reason="endpoint rejected response_format")

    from openjudge.graders.schema import GraderError

    fake_cfg = SimpleNamespace(id="cfg", name="judge", provider="compatible",
                               api_base="https://example.invalid/v1",
                               models=["stub-model"], api_key_encrypted=None, options={})

    engine = eng.OpenJudgeEngine.__new__(eng.OpenJudgeEngine)
    engine._kwargs = {"provider": "compatible"}
    engine._llm_graders = {"relevance": AlwaysErrorGrader()}
    engine._trajectory_grader = None

    def fake_gateway_judge(llm_kwargs, system, user):
        return json.dumps({"score": 3, "reason": "降级评判"})

    monkeypatch.setattr(eng, "_gateway_judge", fake_gateway_judge)

    trace = Trace(query="q", response="ans",
                  openai_messages=[{"role": "user", "content": "q"},
                                   {"role": "assistant", "content": "ans"}],
                  actions=[], tool_error_count=0)
    out = await engine.evaluate(["relevance"], trace)
    assert out["relevance"]["raw"] == 3
    assert "内置评判" in out["relevance"]["reason"]


# ---------------------------------------------------------------- 服务 / API


@pytest.fixture
def inline_worker(monkeypatch):
    """把后台线程改为内联执行、SessionLocal 指向测试库，保证确定性断言。"""
    from tests.conftest import TestSession

    monkeypatch.setattr(eval_service, "SessionLocal", TestSession)

    class InlineThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            self._target, self._args = target, args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(eval_service.threading, "Thread", InlineThread)


def _seed_super_assistant_conversation(db, user_id: str, title: str = "销量分析") -> str:
    conv = SuperAssistantConversation(owner_id=user_id, title=title)
    db.add(conv)
    db.flush()
    db.add(SuperAssistantMessage(conversation_id=conv.id, role="user", content="帮我看看上月销量",
                                 status="complete"))
    db.add(SuperAssistantMessage(
        conversation_id=conv.id, role="assistant",
        content="上月销量 1200 件，环比上升 8%。",
        status="complete",
        steps=[
            {"toolName": "sql_query", "status": "success",
             "arguments": {"sql": "select sum(qty) from orders"}, "preview": "1200"},
            {"toolName": "chart", "status": "success",
             "arguments": {"kind": "bar"}, "preview": "ok"},
        ],
    ))
    db.commit()
    return conv.id


def test_meta_endpoint(client, auth_headers):
    r = client.get("/api/v1/assistant-evaluation/meta", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()["data"]
    keys = {a["key"] for a in body["assistants"]}
    assert {"ontology_agent", "super_assistant", "exploration", "steward",
            "scene_assistant"} <= keys
    assert len(body["dimension_catalog"]) >= 5
    assert body["engine"] in {"openjudge", "builtin"}


def test_meta_requires_admin(client, editor_token):
    r = client.get("/api/v1/assistant-evaluation/meta",
                   headers={"Authorization": f"Bearer {editor_token}"})
    assert r.status_code == 403


@pytest.fixture
def editor_token(client, editor_user):
    r = client.post("/api/v1/auth/login", json={"username": "editor", "password": "editor123"})
    return r.json()["data"]["access_token"]


def test_full_task_flow_code_only(client, auth_headers, db, admin_user, inline_worker):
    conv_id = _seed_super_assistant_conversation(db, admin_user.id)

    r = client.post("/api/v1/assistant-evaluation/tasks", json={
        "assistant_key": "super_assistant",
        "conversation_ids": [conv_id],
        "dimension_keys": ["action_loop"],
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    task = r.json()["data"]
    assert task["status"] == "success"
    assert task["conversation_count"] == 1

    detail = client.get(f"/api/v1/assistant-evaluation/tasks/{task['id']}",
                        headers=auth_headers).json()["data"]
    assert len(detail["items"]) == 1
    item = detail["items"][0]
    assert item["scores"]["action_loop"] == 100.0   # 两个不同动作，无循环
    assert item["overall_score"] == 100.0
    assert item["root_cause"] == "整体良好"

    export = client.get(f"/api/v1/assistant-evaluation/tasks/{task['id']}/export",
                        headers=auth_headers)
    assert export.status_code == 200
    assert "助手质量报告" in export.text

    # 会话列表接口可用（供前端选择器）
    listing = client.get(
        "/api/v1/assistant-evaluation/super_assistant/conversations?limit=10",
        headers=auth_headers,
    ).json()["data"]
    assert listing["total"] >= 1
    assert any(c["id"] == conv_id for c in listing["items"])

    # 清理
    deleted = client.delete(f"/api/v1/assistant-evaluation/tasks/{task['id']}",
                            headers=auth_headers)
    assert deleted.status_code == 200


def test_create_task_validation_errors(client, auth_headers, db, admin_user, inline_worker):
    _seed_super_assistant_conversation(db, admin_user.id, title="另一会话")
    # 未知助手
    r = client.post("/api/v1/assistant-evaluation/tasks", json={
        "assistant_key": "nonexistent", "conversation_ids": ["x"], "dimension_keys": [],
    }, headers=auth_headers)
    assert r.status_code == 400
    # 未知维度
    r = client.post("/api/v1/assistant-evaluation/tasks", json={
        "assistant_key": "super_assistant", "conversation_ids": [], "sample_size": 5,
        "dimension_keys": ["not_a_dim"],
    }, headers=auth_headers)
    assert r.status_code == 400


def test_summary_aggregation():
    from app.assistant_evaluation.models import AssistantEvalItem

    items = [
        AssistantEvalItem(task_id="t", conversation_id="c1", overall_score=90,
                          scores={"relevance": 90}, reasons={}, flags={}),
        AssistantEvalItem(task_id="t", conversation_id="c2", overall_score=50,
                          scores={"relevance": 50}, reasons={}, flags={}),
        # 无适用维度：不计入失败，记为跳过
        AssistantEvalItem(task_id="t", conversation_id="c3", overall_score=None,
                          scores={}, reasons={}, flags={"loop_detected": False,
                                                       "tool_error_count": 0, "low_dims": []}),
        # 评分执行异常：计入失败
        AssistantEvalItem(task_id="t", conversation_id="c4", overall_score=None,
                          scores={}, reasons={}, flags={"engine_error": "boom"}),
    ]
    summary = eval_service._build_summary(items, engine_name="builtin")
    assert summary["overall"] == 70.0
    assert summary["dimensions"]["relevance"]["avg"] == 70.0
    assert summary["badcase_conversation_ids"] == ["c2"]
    assert summary["evaluated"] == 2
    assert summary["failed"] == 1
    assert summary["skipped"] == 1
