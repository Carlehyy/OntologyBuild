"""数据飞轮 M3 测试：投产/回退版本链、投产后看守、值守循环全链路、
预算熔断、is_due 调度判定、RBAC。

LLM / 评分 / 基线抽样全部打桩（与 M2 同法）：LLM 桩按系统提示中的
草稿标记分臂回答，评分桩按答复内容给分，基线桩按 since 参数区分
投产前/投产后样本——门禁与回退行为均可确定性断言。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.assistant_evaluation import (
    apply_service,
    autopilot_service,
    benchmark_service,
    calibration_service,
    experiment_service,
)
from app.assistant_evaluation import engine as eng
from app.assistant_evaluation import service as eval_service
from app.assistant_evaluation.models import (
    AssistantEvalAutopilotConfig,
    AssistantEvalProfileVersion,
)
from app.inbox.models import InboxItem
from app.ontologies.agent_runtime import llm_bridge
from app.ontologies.agent_runtime.boundary import get_or_create_profile
from app.ontologies.agent_runtime.models import AgentConversation, AgentMessage

DRAFT_MARKER = "【值守草稿】回答必须引用工具数据。"


# ---------------------------------------------------------------- fixtures / seeds


@pytest.fixture
def inline_workers(monkeypatch):
    """任务 / 校准 / 实验 / 值守循环线程内联执行、SessionLocal 指向测试库。"""
    import threading as real_threading

    from tests.conftest import TestSession

    for module in (eval_service, calibration_service, experiment_service,
                   autopilot_service):
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

    for module in (eval_service, calibration_service, experiment_service,
                   autopilot_service):
        if hasattr(module, "threading"):
            monkeypatch.setattr(module.threading, "Thread", InlineThread)


@pytest.fixture
def editor_token(client, editor_user):
    r = client.post("/api/v1/auth/login",
                    json={"username": "editor", "password": "editor123"})
    return r.json()["data"]["access_token"]


def _seed_model_config(db, created_by: str = "", name: str = "judge-stub"):
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


def _seed_agent_conversation(db, ontology_id: str, messages: list[tuple[str, str]],
                             title: str = "会话") -> str:
    """messages: [(role, content), ...]；created_at 递增保证顺序稳定。"""
    conv = AgentConversation(ontology_id=ontology_id, title=title)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    base = datetime.now(timezone.utc)
    for i, (role, content) in enumerate(messages):
        db.add(AgentMessage(conversation_id=conv.id, role=role, content=content,
                            created_at=base + timedelta(seconds=i)))
    db.commit()
    return conv.id


def _seed_validated_proposal(db, ontology_id: str, prompt: str,
                             created_by: str | None = None) -> str:
    row = experiment_service.create_proposal(
        db, ontology_id=ontology_id, type="prompt_patch",
        title="测试提案", rationale="测试", payload={"system_prompt_extra": prompt},
        evidence={}, created_by=created_by)
    row.status = "validated"   # 跳过实验，直接模拟门禁通过
    db.commit()
    return row.id


def _fake_production_stats(monkeypatch, *, pre_overall: float, post_overall: float,
                           post_conversations: int = 5):
    """基线抽样桩：since 为空（投产前）返回 pre，否则返回 post（投产后看守）。"""
    def fake(db, ontology_id, *, since=None, limit=8, dimension_keys=None, engine=None):
        if since is None:
            return {"overall": pre_overall, "per_dim": {"relevance": pre_overall},
                    "conversations": 5}
        return {"overall": post_overall, "per_dim": {"relevance": post_overall},
                "conversations": post_conversations}
    monkeypatch.setattr(apply_service, "score_recent_production", fake)


# ---------------------------------------------------------------- 投产 / 回退版本链


def test_apply_and_rollback_roundtrip(client, auth_headers, db, admin_user,
                                      ontology, monkeypatch):
    ontology_id = ontology["id"]
    _seed_model_config(db, created_by=admin_user.id)
    _fake_production_stats(monkeypatch, pre_overall=90.0, post_overall=90.0)

    prompt_v1 = "第一版提示词补充"
    prompt_v2 = "第二版提示词补充"
    p1 = _seed_validated_proposal(db, ontology_id, prompt_v1, created_by=admin_user.id)
    p2 = _seed_validated_proposal(db, ontology_id, prompt_v2, created_by=admin_user.id)

    # 未过门禁（draft）的提案不允许投产
    draft = experiment_service.create_proposal(
        db, ontology_id=ontology_id, type="prompt_patch", title="草稿",
        rationale="", payload={"system_prompt_extra": "draft"}, evidence={},
        created_by=admin_user.id)
    rejected = client.post(f"/api/v1/assistant-evaluation/proposals/{draft.id}/apply",
                           headers=auth_headers)
    assert rejected.status_code == 400

    # 投产 v1：profile 更新 + 版本链登记（快照为投产前的空提示词）
    applied1 = client.post(
        f"/api/v1/assistant-evaluation/proposals/{p1}/apply", headers=auth_headers)
    assert applied1.status_code == 200, applied1.text
    v1 = applied1.json()["data"]
    assert v1["version"] == 1 and v1["status"] == "active"
    assert v1["snapshot"]["system_prompt_extra"] == ""
    assert v1["pre_apply_stats"]["conversations"] == 5
    profile = get_or_create_profile(db, ontology_id)
    db.refresh(profile)
    assert profile.system_prompt_extra == prompt_v1

    # 投产 v2：v1 让位
    applied2 = client.post(
        f"/api/v1/assistant-evaluation/proposals/{p2}/apply", headers=auth_headers)
    v2 = applied2.json()["data"]
    assert v2["version"] == 2 and v2["status"] == "active"
    db.expire_all()
    assert apply_service.active_version(db, ontology_id).id == v2["id"]
    assert {v.version: v.status
            for v in apply_service.list_versions(db, ontology_id)}[1] == "superseded"

    # 回退 v2：profile 恢复到 v1 状态，v1 恢复 active，提案标记回退，管理员告警
    rolled = client.post(
        f"/api/v1/assistant-evaluation/profile-versions/{v2['id']}/rollback",
        json={"reason": "测试回退"}, headers=auth_headers)
    assert rolled.status_code == 200, rolled.text
    db.expire_all()
    profile = get_or_create_profile(db, ontology_id)
    db.refresh(profile)
    assert profile.system_prompt_extra == prompt_v1
    versions = {v.version: v.status
                for v in apply_service.list_versions(db, ontology_id)}
    assert versions == {1: "active", 2: "rolled_back"}
    assert db.query(InboxItem).count() >= 1   # 回退告警已投递收件箱

    # 已回退版本不能再次回退
    again = client.post(
        f"/api/v1/assistant-evaluation/profile-versions/{v2['id']}/rollback",
        json={"reason": "重复回退"}, headers=auth_headers)
    assert again.status_code == 400


def test_watch_degrades_and_rolls_back(client, db, admin_user, ontology, monkeypatch):
    ontology_id = ontology["id"]
    _seed_model_config(db, created_by=admin_user.id)
    # 投产前基线 90，投产后抽样 55：overall 下降 35 → 劣化回退
    _fake_production_stats(monkeypatch, pre_overall=90.0, post_overall=55.0)

    proposal_id = _seed_validated_proposal(db, ontology_id, "劣化版本")
    version = apply_service.apply_proposal(db, proposal_id=proposal_id,
                                           trigger="manual",
                                           actor_user_id=admin_user.id)
    assert version.status == "active"

    outcome = apply_service.watch_latest(db, ontology_id,
                                         dimension_keys=["relevance"], engine=None)
    assert outcome == "rolled_back"
    db.expire_all()
    profile = get_or_create_profile(db, ontology_id)
    db.refresh(profile)
    assert profile.system_prompt_extra == ""    # 恢复投产前状态
    assert apply_service.active_version(db, ontology_id) is None  # v1 无前任


def test_watch_verifies_when_stable(client, db, admin_user, ontology, monkeypatch):
    ontology_id = ontology["id"]
    _seed_model_config(db, created_by=admin_user.id)
    _fake_production_stats(monkeypatch, pre_overall=90.0, post_overall=88.0)

    proposal_id = _seed_validated_proposal(db, ontology_id, "稳定版本")
    apply_service.apply_proposal(db, proposal_id=proposal_id, trigger="manual",
                                 actor_user_id=admin_user.id)
    assert apply_service.watch_latest(db, ontology_id,
                                      dimension_keys=["relevance"],
                                      engine=None) == "verified"


def test_watch_pending_without_enough_samples(client, db, admin_user, ontology,
                                              monkeypatch):
    ontology_id = ontology["id"]
    _seed_model_config(db, created_by=admin_user.id)
    _fake_production_stats(monkeypatch, pre_overall=90.0, post_overall=50.0,
                           post_conversations=1)   # 新样本不足 3 条

    proposal_id = _seed_validated_proposal(db, ontology_id, "待观察版本")
    apply_service.apply_proposal(db, proposal_id=proposal_id, trigger="manual",
                                 actor_user_id=admin_user.id)
    assert apply_service.watch_latest(db, ontology_id,
                                      dimension_keys=["relevance"],
                                      engine=None) == "pending"


# ---------------------------------------------------------------- 值守循环全链路


def _setup_cycle(client, auth_headers, db, admin_user, ontology) -> str:
    ontology_id = ontology["id"]
    _seed_model_config(db, created_by=admin_user.id)
    # 基准会话（留出）：只含用户脚本，供沙箱回放
    conv_heldout = _seed_agent_conversation(
        db, ontology_id, [("user", "问销量的口径")], title="留出会话")
    bench = client.post("/api/v1/assistant-evaluation/benchmarks", json={
        "assistant_key": "ontology_agent", "ontology_id": ontology_id,
        "name": "值守基准",
        "items": [{"conversation_id": conv_heldout, "split": "heldout"}],
    }, headers=auth_headers).json()["data"]

    # 生产坏例会话：含完整问答（"坏答复"），采样评估得 0 分进 badcase
    _seed_agent_conversation(db, ontology_id,
                             [("user", "问一"), ("assistant", "坏答复")],
                             title="坏例一")
    _seed_agent_conversation(db, ontology_id,
                             [("user", "问二"), ("assistant", "坏答复")],
                             title="坏例二")

    config = client.put(f"/api/v1/assistant-evaluation/autopilot/config/{ontology_id}",
                        json={"enabled": True, "run_at": "03:00",
                              "benchmark_set_id": bench["id"],
                              "dimension_keys": ["relevance"], "threshold": 5.0,
                              "max_applies_per_week": 3, "sample_days": 14},
                        headers=auth_headers)
    assert config.status_code == 200, config.text
    return config.json()["data"]["id"]


def _stub_cycle_llm(monkeypatch):
    """LLM 桩：草稿提示词→好答复；评分桩：好答复 100 / 坏答复 0。"""
    def fake_chat(call_kwargs, messages, tools):
        answer = "好答复" if DRAFT_MARKER in messages[0]["content"] else "坏答复"
        return {"content": answer, "tool_calls": [],
                "usage": {"inputTokens": 1, "outputTokens": 1}}

    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    async def fake_evaluate(engine, dim_keys, trace, rubric=None):
        raw = 5.0 if trace.response == "好答复" else 1.0
        return {k: {"raw": raw, "reason": ""} for k in dim_keys}

    monkeypatch.setattr(eval_service, "_evaluate_async", fake_evaluate)

    def fake_gateway_judge(llm_kwargs, system, user):
        return json.dumps({"rationale": "针对坏例补充证据约束",
                           "system_prompt_extra": DRAFT_MARKER},
                          ensure_ascii=False)

    monkeypatch.setattr(eng, "_gateway_judge", fake_gateway_judge)


def test_autopilot_cycle_applies_validated_proposal(client, auth_headers, db,
                                                    admin_user, ontology,
                                                    inline_workers, monkeypatch):
    config_id = _setup_cycle(client, auth_headers, db, admin_user, ontology)
    _stub_cycle_llm(monkeypatch)
    _fake_production_stats(monkeypatch, pre_overall=80.0, post_overall=80.0)

    result = autopilot_service.run_cycle(config_id)
    assert result["status"] == "success", result
    assert result["applied"] is True and result["version"] == 1

    # 生产 profile 已更新为值守草稿，版本链登记且看守基线入档
    profile = get_or_create_profile(db, ontology_id := ontology["id"])
    db.refresh(profile)
    assert DRAFT_MARKER in (profile.system_prompt_extra or "")
    version = apply_service.active_version(db, ontology_id)
    assert version.source["trigger"] == "autopilot"
    assert version.pre_apply_stats["overall"] == 80.0

    # 新坏例已并入基准集（origin=badcase）
    config = db.query(AssistantEvalAutopilotConfig).filter_by(id=config_id).first()
    db.refresh(config)
    items = benchmark_service.items_of(db, config.benchmark_set_id)
    assert len(items) == 3 and sum(i.origin == "badcase" for i in items) == 2

    # 循环留痕：started + succeeded + proposal_applied
    events = client.get(
        "/api/v1/assistant-evaluation/timeline?ref_type=autopilot_config"
        f"&ref_id={config_id}", headers=auth_headers).json()["data"]
    types = [e["event_type"] for e in events]
    assert "cycle_started" in types and "cycle_succeeded" in types
    applied_events = client.get(
        "/api/v1/assistant-evaluation/timeline?ref_type=profile_version",
        headers=auth_headers).json()["data"]
    assert any(e["event_type"] == "proposal_applied"
               and e["actor"] == "autopilot" for e in applied_events)


def test_autopilot_cycle_budget_exhaustion(client, auth_headers, db, admin_user,
                                           ontology, inline_workers, monkeypatch):
    config_id = _setup_cycle(client, auth_headers, db, admin_user, ontology)
    _stub_cycle_llm(monkeypatch)
    _fake_production_stats(monkeypatch, pre_overall=80.0, post_overall=80.0)

    # 预置本周已自动投产 3 次（达到上限）
    for i in range(3):
        db.add(AssistantEvalProfileVersion(
            ontology_id=ontology["id"], version=i + 1,
            snapshot={}, source={"trigger": "autopilot"},
            pre_apply_stats={}, created_by=admin_user.id))
    db.commit()

    result = autopilot_service.run_cycle(config_id)
    assert result["status"] == "skipped_budget", result
    assert db.query(AssistantEvalProfileVersion).filter_by(
        ontology_id=ontology["id"]).count() == 3   # 没有新增投产
    assert db.query(InboxItem).filter(
        InboxItem.title.like("%预算%")).count() >= 1


def test_autopilot_cycle_suspends_after_consecutive_failures(
        client, auth_headers, db, admin_user, ontology, inline_workers, monkeypatch):
    config_id = _setup_cycle(client, auth_headers, db, admin_user, ontology)
    _stub_cycle_llm(monkeypatch)

    def broken_patch(db, config, task):
        raise autopilot_service.ServiceError("提案生成失败（测试注入）")

    monkeypatch.setattr(autopilot_service, "_generate_prompt_patch", broken_patch)

    statuses = [autopilot_service.run_cycle(config_id)["status"] for _ in range(3)]
    assert statuses == ["error", "error", "error"]
    config = db.query(AssistantEvalAutopilotConfig).filter_by(id=config_id).first()
    db.refresh(config)
    assert config.suspended is True
    assert "连续失败 3 轮" in config.suspend_reason
    assert db.query(InboxItem).filter(
        InboxItem.title.like("%熔断%")).count() >= 1

    # 熔断后循环不再执行；人工保存配置解除熔断
    skipped = autopilot_service.run_cycle(config_id)
    assert skipped["status"] == "skipped_disabled"
    saved = client.put(
        f"/api/v1/assistant-evaluation/autopilot/config/{ontology['id']}",
        json={"enabled": True, "run_at": "04:00",
              "benchmark_set_id": config.benchmark_set_id,
              "dimension_keys": ["relevance"]},
        headers=auth_headers)
    assert saved.status_code == 200
    assert saved.json()["data"]["suspended"] is False


# ---------------------------------------------------------------- 调度判定 / 配置校验 / RBAC


class TestIsDue:
    def _config(self, **kwargs):
        base = {"enabled": True, "suspended": False, "run_at": "03:00",
                "last_dispatched_at": None}
        base.update(kwargs)
        return SimpleNamespace(**base)

    def test_before_slot_not_due(self):
        now = datetime(2026, 8, 30, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert autopilot_service.is_due(self._config(), now) is False

    def test_after_slot_due_without_dispatch(self):
        now = datetime(2026, 8, 30, 3, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert autopilot_service.is_due(self._config(), now) is True

    def test_already_dispatched_today_not_due(self):
        now = datetime(2026, 8, 30, 3, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
        last = datetime(2026, 8, 30, 3, 1, tzinfo=timezone.utc)  # 11:01 北京时间
        assert autopilot_service.is_due(
            self._config(last_dispatched_at=last), now) is False

    def test_disabled_or_suspended_never_due(self):
        now = datetime(2026, 8, 30, 5, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert autopilot_service.is_due(
            self._config(enabled=False), now) is False
        assert autopilot_service.is_due(
            self._config(suspended=True), now) is False


def test_autopilot_config_validation(client, auth_headers, db, admin_user, ontology):
    ontology_id = ontology["id"]
    # 开启必须绑定基准集
    no_bench = client.put(
        f"/api/v1/assistant-evaluation/autopilot/config/{ontology_id}",
        json={"enabled": True}, headers=auth_headers)
    assert no_bench.status_code == 400
    # 时间格式非法
    bad_time = client.put(
        f"/api/v1/assistant-evaluation/autopilot/config/{ontology_id}",
        json={"enabled": False, "run_at": "25:00"}, headers=auth_headers)
    assert bad_time.status_code == 400
    # 正常保存（未开启，无需基准集）
    ok = client.put(
        f"/api/v1/assistant-evaluation/autopilot/config/{ontology_id}",
        json={"enabled": False, "run_at": "03:30"}, headers=auth_headers)
    assert ok.status_code == 200
    fetched = client.get(
        f"/api/v1/assistant-evaluation/autopilot/config/{ontology_id}",
        headers=auth_headers).json()["data"]
    assert fetched["run_at"] == "03:30"


def test_m3_endpoints_require_admin(client, editor_token, ontology):
    r = client.get("/api/v1/assistant-evaluation/profile-versions?ontology_id=x",
                   headers={"Authorization": f"Bearer {editor_token}"})
    assert r.status_code == 403
    r = client.get("/api/v1/assistant-evaluation/autopilot/config/x",
                   headers={"Authorization": f"Bearer {editor_token}"})
    assert r.status_code == 403
