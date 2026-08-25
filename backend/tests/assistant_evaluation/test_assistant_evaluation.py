"""助手评估（assistant_evaluation）单元与接口测试。

覆盖：轨迹归一化适配器（OpenAI 完整轨迹重建）、循环/重复度检测算法、
维度归一化与根因归类、降级引擎 JSON 解析与新增维度、rubric 生成与评分、
任务创建/执行（内联线程、并发）到报告汇总的全链路、趋势与轨迹下钻、
启动恢复、迁移。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.assistant_evaluation import service as eval_service
from app.assistant_evaluation.adapters import Trace, _extract_steps
from app.assistant_evaluation.dimensions import (
    DIMENSIONS,
    RUBRIC_DIM_KEY,
    normalize,
    root_cause_of,
    rubric_dimension,
)
from app.assistant_evaluation.engine import detect_action_loop, detect_ngram_repetition
from app.assistant_evaluation.models import AssistantEvalRubric, AssistantEvalTask
from app.super_assistant.models import (
    SuperAssistantConversation,
    SuperAssistantMessage,
)


# ---------------------------------------------------------------- 纯函数


class Row:
    def __init__(self, role, content, steps):
        self.role, self.content, self.steps = role, content, steps


class TestExtractSteps:
    def test_super_assistant_shape(self):
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
        assert msgs[0]["role"] == "user"
        assert msgs[-1]["content"] == "销量是 1。"
        # 助手消息带 tool_calls，紧随 tool 消息（OpenAI 完整轨迹）
        assistant_msg = msgs[1]
        assert assistant_msg["role"] == "assistant"
        assert len(assistant_msg["tool_calls"]) == 2
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "sql_query"
        assert assistant_msg["tool_calls"][0]["id"] == "call_1_0"
        assert json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"]) == {"sql": "select 1"}
        assert msgs[2]["role"] == "tool"
        assert msgs[2]["content"] == "[{count: 1}]"
        assert msgs[3]["role"] == "tool"
        assert msgs[3]["content"] == "调用失败：error"
        assert msgs[4]["role"] == "assistant" and "tool_calls" not in msgs[4]

    def test_agent_marker_step_skipped_but_counted(self):
        rows = [
            Row("user", "做点事", []),
            Row("assistant", "没做完", [{"toolName": "agent", "status": "failed"}]),
        ]
        actions, msgs, errors = _extract_steps(rows, "toolName", "status", ("preview",))
        assert actions == []
        assert errors == 1
        assert msgs[1]["role"] == "assistant" and "tool_calls" not in msgs[1]

    def test_result_key_priority(self):
        """本体助手：优先完整 result，其次 summary。"""
        rows = [
            Row("user", "查一下", []),
            Row("assistant", "结论", [
                {"tool": "query", "arguments": {}, "summary": "概要", "result": "完整结果"},
            ]),
        ]
        actions, msgs, errors = _extract_steps(rows, "tool", None, ("result", "summary"))
        assert msgs[2]["content"] == "完整结果"
        assert actions[0]["preview"] == "完整结果"

    def test_tool_content_truncated(self):
        rows = [
            Row("user", "查一下", []),
            Row("assistant", "结论", [
                {"tool": "query", "arguments": {}, "result": "x" * 5000},
            ]),
        ]
        actions, msgs, errors = _extract_steps(rows, "tool", None, ("result",))
        assert len(msgs[2]["content"]) <= 2000 + len("…（已截断）")
        assert msgs[2]["content"].endswith("…（已截断）")


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


class TestNgramRepetition:
    def test_repeated_text_penalized(self):
        assert detect_ngram_repetition("a b c a b c a b c") < 0

    def test_short_text_clean(self):
        assert detect_ngram_repetition("太短") == 0.0

    def test_unique_text_clean(self):
        assert detect_ngram_repetition("完全 不重复 的 一段 文本 内容") == 0.0


class TestDimensions:
    def test_normalize_llm_scale(self):
        relevance = DIMENSIONS["relevance"]
        assert normalize(relevance, 1) == 0.0
        assert normalize(relevance, 5) == 100.0
        assert normalize(relevance, 3) == 50.0

    def test_normalize_repetition_penalty(self):
        dim = DIMENSIONS["response_repetition"]
        assert normalize(dim, 0) == 100.0
        assert normalize(dim, -0.3) == 0.0
        assert normalize(dim, -0.15) == 50.0

    def test_rubric_dimension(self):
        dim = rubric_dimension("本体构建质量", 0, 5)
        assert dim.key == RUBRIC_DIM_KEY
        assert dim.kind == "llm"
        assert dim.scale == (0.0, 5.0)
        assert dim.weight == 1.2

    def test_root_cause_buckets(self):
        scores = {"hallucination": 40.0, "relevance": 90.0}
        assert root_cause_of(scores, {}) .startswith("模型问题")
        assert root_cause_of({"relevance": 95.0}, {"loop_detected": True}).startswith("工具问题")
        assert root_cause_of({"relevance": 90.0}, {}) == "整体良好"
        assert root_cause_of({"harmfulness": 20.0, "relevance": 90.0}, {}).startswith("模型问题")
        assert root_cause_of({"tool_call_success": 10.0, "relevance": 90.0}, {}).startswith("工具问题")


# ---------------------------------------------------------------- 引擎解析


class TestFallbackParsing:
    def test_parse_tolerates_dirty_json(self):
        from app.assistant_evaluation.engine import _parse_judge_json

        assert _parse_judge_json('前缀 {"score": 4, "reason": "r"} 后缀')["score"] == 4
        assert _parse_judge_json(None) == {}
        assert _parse_judge_json("完全不是 JSON") == {}


def _trace(query="q", response="ans", actions=None):
    actions = actions or [{"name": "t", "arguments": {}}, {"name": "u", "arguments": {}}]
    return Trace(query=query, response=response,
                 openai_messages=[{"role": "user", "content": query},
                                  {"role": "assistant", "content": response}],
                 actions=actions, tool_error_count=0)


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
    trace = _trace()
    out = await engine.evaluate(["relevance", "trajectory", "action_loop"], trace)
    assert out["relevance"]["raw"] == 5
    assert out["trajectory"]["raw"] == 1
    assert out["action_loop"]["raw"] == 1.0


@pytest.mark.asyncio
async def test_fallback_engine_new_dims_and_rubric(monkeypatch):
    """新增维度与 rubric 的降级路径（网关桩）。"""
    import app.assistant_evaluation.engine as eng

    engine = eng.FallbackEngine.__new__(eng.FallbackEngine)
    engine._kwargs = {}

    def fake_gateway_judge(llm_kwargs, system, user):
        if "安全审核" in system:
            return json.dumps({"score": 4, "reason": "基本安全"})
        if "技术性成败" in system:
            return json.dumps({"score": 1, "reason": "全部成功"})
        if "评估标准" in system:
            return json.dumps({"score": 4, "reason": "达标"})
        return json.dumps({"score": 5, "reason": "无问题"})

    monkeypatch.setattr(eng, "_gateway_judge", fake_gateway_judge)
    rubric = {"name": "自定义标准", "rubrics": "1. a\n2. b", "min_score": 0, "max_score": 5}
    out = await engine.evaluate(
        ["harmfulness", "tool_call_success", "response_repetition", "rubric"],
        _trace(), rubric=rubric,
    )
    assert out["harmfulness"]["raw"] == 4
    assert out["tool_call_success"]["raw"] == 1
    assert out["rubric"]["raw"] == 4
    assert "response_repetition" in out  # 代码型维度本地确定性产出


@pytest.mark.skipif(
    not __import__("app.assistant_evaluation.engine",
                   fromlist=["openjudge_available"]).openjudge_available(),
    reason="py-openjudge 未安装（CI 环境）",
)
@pytest.mark.asyncio
async def test_openjudge_engine_with_stub_model(monkeypatch):
    """OpenJudge 官方评分器链路：以桩模型替代真实 LLM，验证分数与理由产出。"""
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
    trace = _trace(query="什么是本体建模？", response="把业务概念抽象成类、属性和关系。")
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
    engine._kwargs = {"provider": "compatible", "model_config_id": None}
    engine._llm_graders = {"relevance": AlwaysErrorGrader()}
    engine._trajectory_grader = None

    def fake_gateway_judge(llm_kwargs, system, user):
        return json.dumps({"score": 3, "reason": "降级评判"})

    monkeypatch.setattr(eng, "_gateway_judge", fake_gateway_judge)

    trace = _trace()
    out = await engine.evaluate(["relevance"], trace)
    assert out["relevance"]["raw"] == 3
    assert "内置评判" in out["relevance"]["reason"]


@pytest.mark.skipif(
    not __import__("app.assistant_evaluation.engine",
                   fromlist=["openjudge_available"]).openjudge_available(),
    reason="py-openjudge 未安装（CI 环境）",
)
def test_build_engine_falls_back_when_official_construction_fails():
    """judge 模型配置缺少 API Key 时，官方引擎构造失败 → 降级内置引擎。

    回归：OpenAIChatModel 构造即初始化 openai 客户端，空 API Key 会抛
    OpenAIError；build_engine 必须兜底而不是让任务直接失败。
    """
    import app.assistant_evaluation.engine as eng

    fake_cfg = SimpleNamespace(id="cfg", name="judge", provider="openai",
                               api_base="https://example.invalid/v1",
                               models=["stub-model"], api_key_encrypted=None, options={})
    engine = eng.build_engine(fake_cfg)
    assert engine.name == "builtin"


# ---------------------------------------------------------------- 服务 / API


@pytest.fixture
def inline_worker(monkeypatch):
    """把评估任务线程改为内联执行、SessionLocal 指向测试库，保证确定性断言。

    注意：只内联名称以 assistant-eval- 开头的任务线程；线程池（asyncio
    executor）创建的工作线程必须走真实 Thread，否则其 _worker 会在
    work_queue.get() 上永久阻塞导致 submit 死锁。
    """
    import threading as real_threading
    from tests.conftest import TestSession

    monkeypatch.setattr(eval_service, "SessionLocal", TestSession)
    RealThread = real_threading.Thread

    class InlineThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            self._target, self._args = target, args
            self._daemon = daemon
            self._name = name
            self._real = None

        def start(self):
            if self._name and str(self._name).startswith("assistant-eval-"):
                self._target(*self._args)
                return
            self._real = RealThread(target=self._target, args=self._args,
                                    daemon=bool(self._daemon), name=self._name)
            self._real.start()

        def join(self, timeout=None):
            if self._real is not None:
                self._real.join(timeout)

    monkeypatch.setattr(eval_service.threading, "Thread", InlineThread)


def _seed_super_assistant_conversation(db, user_id: str, title: str = "销量分析",
                                       created_at=None) -> str:
    conv = SuperAssistantConversation(owner_id=user_id, title=title)
    if created_at is not None:
        conv.created_at = created_at
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


def _seed_model_config(db, name: str = "judge-stub", created_by: str = ""):
    from app.model_configs.models import ModelConfig

    config = ModelConfig(
        id=str(uuid.uuid4()), name=name, config_type="llm",
        provider="openai", api_base="https://example.invalid/v1",
        api_key_encrypted=None, models=["stub-model"], options={},
        enabled=True, is_default=True, created_by=created_by or str(uuid.uuid4()),
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def test_meta_endpoint(client, auth_headers):
    r = client.get("/api/v1/assistant-evaluation/meta", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()["data"]
    keys = {a["key"] for a in body["assistants"]}
    assert {"ontology_agent", "super_assistant", "exploration", "steward",
            "scene_assistant"} <= keys
    assert len(body["dimension_catalog"]) >= 8
    assert {"relevance", "hallucination", "instruction_following", "harmfulness",
            "trajectory", "tool_call_success", "action_loop",
            "response_repetition"} <= {d["key"] for d in body["dimension_catalog"]}
    assert "response_repetition" in body["base_dimension_keys"]
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

    # 同助手趋势（时间升序）
    trend = client.get("/api/v1/assistant-evaluation/trend?assistant_key=super_assistant",
                       headers=auth_headers).json()["data"]
    assert any(t["id"] == task["id"] for t in trend)
    assert trend[-1]["overall"] == 100.0

    # 会话轨迹下钻：OpenAI 完整轨迹（含工具消息）
    trace_resp = client.get(
        f"/api/v1/assistant-evaluation/tasks/{task['id']}/items/{item['id']}/trace",
        headers=auth_headers,
    )
    assert trace_resp.status_code == 200, trace_resp.text
    trace_body = trace_resp.json()["data"]
    assert trace_body["conversation_id"] == conv_id
    assert trace_body["tool_error_count"] == 0
    roles = [m["role"] for m in trace_body["openai_messages"]]
    assert "tool" in roles
    assert len(trace_body["actions"]) == 2

    # 清理
    deleted = client.delete(f"/api/v1/assistant-evaluation/tasks/{task['id']}",
                            headers=auth_headers)
    assert deleted.status_code == 200


def test_full_task_flow_with_repetition_dim(client, auth_headers, db, admin_user, inline_worker):
    conv_id = _seed_super_assistant_conversation(db, admin_user.id)
    r = client.post("/api/v1/assistant-evaluation/tasks", json={
        "assistant_key": "super_assistant",
        "conversation_ids": [conv_id],
        "dimension_keys": ["response_repetition"],
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    task = r.json()["data"]
    assert task["status"] == "success"
    assert task["judge_model_name"] == "（仅代码型维度，无需 judge 模型）"
    detail = client.get(f"/api/v1/assistant-evaluation/tasks/{task['id']}",
                        headers=auth_headers).json()["data"]
    assert detail["items"][0]["scores"]["response_repetition"] == 100.0
    assert detail["summary"]["engine"] == "code-only"


def test_task_with_rubric(client, auth_headers, db, admin_user, inline_worker, monkeypatch):
    """rubric 生成（网关桩）+ 任务评分（rubric 维度 + 默认维度）。"""
    import app.assistant_evaluation.engine as eng

    _seed_model_config(db, created_by=admin_user.id)
    conv_id = _seed_super_assistant_conversation(db, admin_user.id)

    def fake_gateway_judge(llm_kwargs, system, user):
        if "评估标准设计" in system:
            return json.dumps({"rubrics": ["标准一", "标准二"], "reason": "生成"},
                              ensure_ascii=False)
        if "评估标准" in system:
            return json.dumps({"score": 4, "reason": "达标"})
        return json.dumps({"score": 5, "reason": "无问题"})

    monkeypatch.setattr(eng, "_gateway_judge", fake_gateway_judge)

    rubric_resp = client.post("/api/v1/assistant-evaluation/rubrics", json={
        "name": "本体构建质量", "task_description": "评估本体构建答复的质量",
        "sample_queries": ["帮我建个本体"], "min_score": 0, "max_score": 5,
    }, headers=auth_headers)
    assert rubric_resp.status_code == 200, rubric_resp.text
    rubric = rubric_resp.json()["data"]
    assert "1. 标准一" in rubric["rubrics"]
    assert rubric["judge_model_name"] == "judge-stub"

    listed = client.get("/api/v1/assistant-evaluation/rubrics", headers=auth_headers).json()["data"]
    assert any(r["id"] == rubric["id"] for r in listed)

    r = client.post("/api/v1/assistant-evaluation/tasks", json={
        "assistant_key": "super_assistant",
        "conversation_ids": [conv_id],
        "dimension_keys": ["response_repetition"],
        "rubric_id": rubric["id"],
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    task = r.json()["data"]
    assert task["params"]["rubric"]["name"] == "本体构建质量"

    detail = client.get(f"/api/v1/assistant-evaluation/tasks/{task['id']}",
                        headers=auth_headers).json()["data"]
    item = detail["items"][0]
    assert item["scores"]["rubric"] == 80.0   # 4/5 归一化
    assert "rubric" in detail["summary"]["dimensions"]
    assert detail["summary"]["dimensions"]["rubric"]["label"] == "本体构建质量"

    export = client.get(f"/api/v1/assistant-evaluation/tasks/{task['id']}/export",
                        headers=auth_headers)
    assert "自定义评分标准：本体构建质量" in export.text

    # rubric 删除不影响历史报告（快照在 params 中）
    deleted = client.delete(f"/api/v1/assistant-evaluation/rubrics/{rubric['id']}",
                            headers=auth_headers)
    assert deleted.status_code == 200
    export2 = client.get(f"/api/v1/assistant-evaluation/tasks/{task['id']}/export",
                         headers=auth_headers)
    assert "本体构建质量" in export2.text

    # 清理任务
    client.delete(f"/api/v1/assistant-evaluation/tasks/{task['id']}", headers=auth_headers)


def test_sample_mode_respects_window(client, auth_headers, db, admin_user, inline_worker):
    old = datetime.now(timezone.utc) - timedelta(days=60)
    _seed_super_assistant_conversation(db, admin_user.id, title="太旧", created_at=old)
    # 窗口外 → 报错
    r = client.post("/api/v1/assistant-evaluation/tasks", json={
        "assistant_key": "super_assistant",
        "sample_size": 10, "sample_days": 30,
        "dimension_keys": ["action_loop"],
    }, headers=auth_headers)
    assert r.status_code == 400
    assert "没有可评估的会话" in r.json()["detail"]
    # 窗口内 → 成功且只取窗口内会话
    fresh = _seed_super_assistant_conversation(db, admin_user.id, title="新鲜")
    r2 = client.post("/api/v1/assistant-evaluation/tasks", json={
        "assistant_key": "super_assistant",
        "sample_size": 10, "sample_days": 30,
        "dimension_keys": ["action_loop"],
    }, headers=auth_headers)
    assert r2.status_code == 200, r2.text
    task = r2.json()["data"]
    assert task["params"]["mode"] == "sample"
    assert fresh in task["params"]["conversation_ids"]


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
    # 不存在的 rubric
    r = client.post("/api/v1/assistant-evaluation/tasks", json={
        "assistant_key": "super_assistant", "conversation_ids": [],
        "dimension_keys": ["action_loop"], "rubric_id": "missing",
    }, headers=auth_headers)
    assert r.status_code == 400
    # rubric 分值区间不合法
    r = client.post("/api/v1/assistant-evaluation/rubrics", json={
        "name": "坏", "task_description": "x", "min_score": 5, "max_score": 5,
    }, headers=auth_headers)
    assert r.status_code == 400


def test_recover_interrupted_tasks(db, monkeypatch):
    """启动恢复：queued 重排、running 标记中断。"""
    from tests.conftest import TestSession

    monkeypatch.setattr(eval_service, "SessionLocal", TestSession)
    started_ids = []
    monkeypatch.setattr(eval_service, "_start_worker", lambda task_id: started_ids.append(task_id))

    queued = AssistantEvalTask(assistant_key="super_assistant", title="q", status="queued",
                               params={}, conversation_count=0, completed_conversations=0)
    running = AssistantEvalTask(assistant_key="super_assistant", title="r", status="running",
                                params={}, conversation_count=0, completed_conversations=0)
    db.add(queued)
    db.add(running)
    db.commit()

    result = eval_service.recover_interrupted_tasks()
    assert result == {"requeued": 1, "interrupted": 1}
    assert started_ids == [queued.id]
    db.refresh(running)
    assert running.status == "error"
    assert "重启中断" in (running.error or "")


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
    # rubric 维度出现在汇总中
    summary2 = eval_service._build_summary(
        items, engine_name="builtin",
        rubric={"name": "标准", "rubrics": "1. a", "min_score": 0, "max_score": 5},
    )
    assert summary2["engine"] == "builtin"
