"""数据飞轮 M2 测试：优化提案、双臂沙箱实验、留出集门禁、沙箱清理。

LLM 与评分均打桩：LLM 桩以系统提示中的草稿标记区分两臂（顺带端到端
证明 profile_override 真实生效），评分桩按答复内容给分，门禁结果可
确定性断言。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.assistant_evaluation import (
    benchmark_service,
    calibration_service,
    experiment_service,
)
from app.assistant_evaluation import service as eval_service
from app.ontologies.agent_runtime import llm_bridge
from app.ontologies.agent_runtime.models import AgentConversation, AgentMessage

DRAFT_MARKER = "【草稿提示】回答必须基于工具结果给证据。"


@pytest.fixture
def inline_workers(monkeypatch):
    """任务 / 校准 / 实验线程内联执行、SessionLocal 指向测试库。"""
    import threading as real_threading

    from tests.conftest import TestSession

    for module in (eval_service, calibration_service, experiment_service):
        monkeypatch.setattr(module, "SessionLocal", TestSession)

    RealThread = real_threading.Thread

    class InlineThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            self._target, self._args = target, args
            self._daemon, self._name = daemon, name
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

    for module in (eval_service, calibration_service, experiment_service):
        monkeypatch.setattr(module.threading, "Thread", InlineThread)


@pytest.fixture
def editor_token(client, editor_user):
    r = client.post("/api/v1/auth/login",
                    json={"username": "editor", "password": "editor123"})
    return r.json()["data"]["access_token"]


def _seed_model_config(db, created_by: str = "", name: str = "judge-stub",
                       model: str = "stub-model"):
    from app.model_configs.models import ModelConfig

    config = ModelConfig(
        id=str(uuid.uuid4()), name=name, config_type="llm",
        provider="openai", api_base="https://example.invalid/v1",
        api_key_encrypted=None, models=[model], options={},
        enabled=True, is_default=True, created_by=created_by or str(uuid.uuid4()),
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def _seed_agent_conversation(db, ontology_id: str, questions: list[str],
                             title: str = "基准会话") -> str:
    """基准会话：只落用户消息脚本（created_at 递增保证回放顺序稳定）。"""
    conv = AgentConversation(ontology_id=ontology_id, title=title)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    base = datetime.now(timezone.utc)
    for i, question in enumerate(questions):
        db.add(AgentMessage(conversation_id=conv.id, role="user", content=question,
                            created_at=base + timedelta(seconds=i)))
    db.commit()
    return conv.id


def test_dual_arm_experiment_end_to_end(client, auth_headers, db, admin_user,
                                        ontology, inline_workers, monkeypatch):
    ontology_id = ontology["id"]
    _seed_model_config(db, created_by=admin_user.id)

    conv_train = _seed_agent_conversation(db, ontology_id, ["问一", "追问一"],
                                          title="训练会话")
    conv_heldout = _seed_agent_conversation(db, ontology_id, ["问二"],
                                            title="留出会话")

    bench = client.post("/api/v1/assistant-evaluation/benchmarks", json={
        "assistant_key": "ontology_agent", "ontology_id": ontology_id,
        "name": "M2 基准",
        "items": [{"conversation_id": conv_train, "split": "train"},
                  {"conversation_id": conv_heldout, "split": "heldout"}],
    }, headers=auth_headers)
    assert bench.status_code == 200, bench.text
    benchmark_id = bench.json()["data"]["id"]
    assert bench.json()["data"]["ontology_id"] == ontology_id

    proposal = client.post("/api/v1/assistant-evaluation/proposals", json={
        "ontology_id": ontology_id, "type": "prompt_patch",
        "title": "强化证据约束", "rationale": "幻觉坏例集中",
        "payload": {"system_prompt_extra": DRAFT_MARKER},
        "evidence": {"categories": {"model": {"count": 1}}},
    }, headers=auth_headers)
    assert proposal.status_code == 200, proposal.text
    proposal_data = proposal.json()["data"]
    proposal_id = proposal_data["id"]
    assert proposal_data["status"] == "draft"
    assert proposal_data["payload"]["base_system_prompt_extra"] == ""  # 提案时基线快照

    # LLM 桩按草稿标记分臂回答（同时端到端验证 profile_override 生效）
    def fake_chat(call_kwargs, messages, tools):
        answer = "好答复" if DRAFT_MARKER in messages[0]["content"] else "坏答复"
        return {"content": answer, "tool_calls": [],
                "usage": {"inputTokens": 1, "outputTokens": 1}}

    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    async def fake_evaluate(engine, dim_keys, trace, rubric=None):
        raw = 5.0 if trace.response == "好答复" else 1.0   # 100 / 25
        return {k: {"raw": raw, "reason": ""} for k in dim_keys}

    monkeypatch.setattr(eval_service, "_evaluate_async", fake_evaluate)

    experiment = client.post("/api/v1/assistant-evaluation/experiments", json={
        "proposal_id": proposal_id, "benchmark_set_id": benchmark_id,
        "dimension_keys": ["relevance"], "threshold": 5.0,
    }, headers=auth_headers)
    assert experiment.status_code == 200, experiment.text
    exp = experiment.json()["data"]
    assert exp["status"] == "success"

    result = exp["result"]
    assert result["baseline"]["overall"] == 0.0    # raw 1/5 → 0 分（坏答复）
    assert result["trial"]["overall"] == 100.0     # raw 5/5 → 100 分（好答复）
    assert result["by_split"]["heldout"]["delta"] == 100.0
    assert result["gate"]["passed"] is True
    assert result["gate"]["effective_threshold"] == 5.0   # 无校准记录 → 噪声地板 0

    # 门禁通过后提案升级为 validated
    refreshed = client.get(
        f"/api/v1/assistant-evaluation/proposals/{proposal_id}",
        headers=auth_headers).json()["data"]
    assert refreshed["status"] == "validated"

    # 实验条目：两臂 × 两条基准会话，轨迹快照自包含
    detail = client.get(
        f"/api/v1/assistant-evaluation/experiments/{exp['id']}",
        headers=auth_headers).json()["data"]
    assert len(detail["items"]) == 4
    trial_item = next(i for i in detail["items"]
                      if i["arm"] == "trial" and i["split"] == "heldout")
    assert trial_item["overall_score"] == 100.0
    assert trial_item["transcript"]["response"] == "好答复"

    # 沙箱会话全部清理（快照已存回实验条目）
    db.expire_all()
    remaining = (db.query(AgentConversation)
                 .filter(AgentConversation.is_sandbox.is_(True)).count())
    assert remaining == 0

    # 时间线：实验事件由 system actor 留痕
    events = client.get(
        "/api/v1/assistant-evaluation/timeline?ref_type=experiment",
        headers=auth_headers).json()["data"]
    assert events[0]["event_type"] == "experiment_succeeded"
    assert events[0]["actor"] == "system"
    assert events[0]["detail"]["gate"]["passed"] is True

    # 实验删除 + 列表
    deleted = client.delete(
        f"/api/v1/assistant-evaluation/experiments/{exp['id']}",
        headers=auth_headers)
    assert deleted.status_code == 200


def test_experiment_gate_respects_noise_floor(client, auth_headers, db, admin_user,
                                              ontology, inline_workers, monkeypatch):
    """噪声地板抬高门禁下界：小幅增量在抖动范围内不得放行。"""
    ontology_id = ontology["id"]
    _seed_model_config(db, created_by=admin_user.id)
    conv = _seed_agent_conversation(db, ontology_id, ["问"], title="留出会话")
    bench = client.post("/api/v1/assistant-evaluation/benchmarks", json={
        "assistant_key": "ontology_agent", "ontology_id": ontology_id,
        "name": "噪声门禁基准",
        "items": [{"conversation_id": conv, "split": "heldout"}],
    }, headers=auth_headers).json()["data"]

    proposal = client.post("/api/v1/assistant-evaluation/proposals", json={
        "ontology_id": ontology_id, "type": "prompt_patch",
        "payload": {"system_prompt_extra": DRAFT_MARKER},
    }, headers=auth_headers).json()["data"]

    # 校准记录：overall_noise = 8 → 有效阈值 = max(5, 16) = 16
    db.add(calibration_service.AssistantEvalCalibration(
        assistant_key="ontology_agent", status="success",
        params={}, judge_model_name="", result={"overall_noise": 8.0},
        created_by=admin_user.id))
    db.commit()

    monkeypatch.setattr(llm_bridge, "chat", lambda kw, m, t: {
        "content": "好答复" if DRAFT_MARKER in m[0]["content"] else "坏答复",
        "tool_calls": [], "usage": {}})

    async def fake_evaluate(engine, dim_keys, trace, rubric=None):
        raw = 5.0 if trace.response == "好答复" else 4.0   # 100 vs 75 → 增量 25
        return {k: {"raw": raw, "reason": ""} for k in dim_keys}

    monkeypatch.setattr(eval_service, "_evaluate_async", fake_evaluate)

    experiment = client.post("/api/v1/assistant-evaluation/experiments", json={
        "proposal_id": proposal["id"], "benchmark_set_id": bench["id"],
        "dimension_keys": ["relevance"], "threshold": 5.0,
    }, headers=auth_headers)
    assert experiment.status_code == 200, experiment.text
    result = experiment.json()["data"]["result"]
    assert result["gate"]["noise_floor"] == 8.0
    assert result["gate"]["effective_threshold"] == 16.0
    assert result["gate"]["heldout_delta"] == 25.0
    assert result["gate"]["passed"] is True    # 25 ≥ 16：放行

    # 增量 12.5（87.5 vs 100）时应被噪声门禁拦下
    async def fake_evaluate_small(engine, dim_keys, trace, rubric=None):
        raw = 5.0 if trace.response == "好答复" else 4.5   # 100 vs 87.5 → 增量 12.5
        return {k: {"raw": raw, "reason": ""} for k in dim_keys}

    monkeypatch.setattr(eval_service, "_evaluate_async", fake_evaluate_small)
    proposal2 = client.post("/api/v1/assistant-evaluation/proposals", json={
        "ontology_id": ontology_id, "type": "prompt_patch",
        "payload": {"system_prompt_extra": DRAFT_MARKER + " 二稿"},
    }, headers=auth_headers).json()["data"]
    experiment2 = client.post("/api/v1/assistant-evaluation/experiments", json={
        "proposal_id": proposal2["id"], "benchmark_set_id": bench["id"],
        "dimension_keys": ["relevance"], "threshold": 5.0,
    }, headers=auth_headers)
    assert experiment2.status_code == 200
    result2 = experiment2.json()["data"]["result"]
    assert result2["gate"]["heldout_delta"] == 12.5
    assert result2["gate"]["passed"] is False  # 12.5 < 16：judge 抖动范围内，拦截
    refreshed2 = client.get(
        f"/api/v1/assistant-evaluation/proposals/{proposal2['id']}",
        headers=auth_headers).json()["data"]
    assert refreshed2["status"] == "draft"     # 门禁未过不升级


def test_proposal_and_experiment_validation(client, auth_headers, db, admin_user,
                                            ontology):
    ontology_id = ontology["id"]

    # 未知本体 / 非法类型 / 空提示词 / 模型不存在
    assert client.post("/api/v1/assistant-evaluation/proposals", json={
        "ontology_id": str(uuid.uuid4()), "type": "prompt_patch",
        "payload": {"system_prompt_extra": "x"},
    }, headers=auth_headers).status_code == 400
    assert client.post("/api/v1/assistant-evaluation/proposals", json={
        "ontology_id": ontology_id, "type": "tool_magic",
        "payload": {},
    }, headers=auth_headers).status_code == 400
    assert client.post("/api/v1/assistant-evaluation/proposals", json={
        "ontology_id": ontology_id, "type": "prompt_patch",
        "payload": {"system_prompt_extra": "  "},
    }, headers=auth_headers).status_code == 400
    assert client.post("/api/v1/assistant-evaluation/proposals", json={
        "ontology_id": ontology_id, "type": "model_swap",
        "payload": {"model_config_id": str(uuid.uuid4())},
    }, headers=auth_headers).status_code == 400

    # 本体助手基准集必须绑定本体；跨本体会话拒绝
    other_conv = _seed_agent_conversation(db, str(uuid.uuid4()), ["问"])
    assert client.post("/api/v1/assistant-evaluation/benchmarks", json={
        "assistant_key": "ontology_agent", "name": "未绑定",
        "items": [{"conversation_id": other_conv}],
    }, headers=auth_headers).status_code == 400
    assert client.post("/api/v1/assistant-evaluation/benchmarks", json={
        "assistant_key": "ontology_agent", "ontology_id": ontology_id,
        "name": "跨本体", "items": [{"conversation_id": other_conv}],
    }, headers=auth_headers).status_code == 400

    # 无留出集条目的基准集不能开实验
    conv_train = _seed_agent_conversation(db, ontology_id, ["问"], title="仅训练")
    bench = client.post("/api/v1/assistant-evaluation/benchmarks", json={
        "assistant_key": "ontology_agent", "ontology_id": ontology_id,
        "name": "仅训练基准",
        "items": [{"conversation_id": conv_train, "split": "train"}],
    }, headers=auth_headers).json()["data"]
    proposal = client.post("/api/v1/assistant-evaluation/proposals", json={
        "ontology_id": ontology_id, "type": "prompt_patch",
        "payload": {"system_prompt_extra": DRAFT_MARKER},
    }, headers=auth_headers).json()["data"]
    no_heldout = client.post("/api/v1/assistant-evaluation/experiments", json={
        "proposal_id": proposal["id"], "benchmark_set_id": bench["id"],
        "dimension_keys": ["relevance"],
    }, headers=auth_headers)
    assert no_heldout.status_code == 400
    assert "留出集" in no_heldout.json()["detail"]


def test_new_m2_endpoints_require_admin(client, editor_token):
    for path, method in (
        ("/api/v1/assistant-evaluation/proposals", "get"),
        ("/api/v1/assistant-evaluation/experiments", "get"),
    ):
        r = client.get(path, headers={"Authorization": f"Bearer {editor_token}"})
        assert r.status_code == 403
