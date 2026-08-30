"""数据飞轮 M1 测试：结构化根因、基准集管理（train/heldout 稳定切分）、
噪声地板校准、审计时间线、created_by 补记。
"""
from __future__ import annotations

import uuid

import pytest

from app.assistant_evaluation import benchmark_service, calibration_service
from app.assistant_evaluation import service as eval_service
from app.assistant_evaluation.dimensions import root_cause_of, structured_root_cause
from app.assistant_evaluation.models import AssistantEvalItem
from app.super_assistant.models import SuperAssistantConversation, SuperAssistantMessage


# ---------------------------------------------------------------- 纯函数：结构化根因


class TestStructuredRootCause:
    def test_good_case(self):
        attr = structured_root_cause({"relevance": 95.0, "hallucination": 90.0}, {})
        assert attr["category"] == "good"
        assert attr["dim_key"] is None
        assert attr["levers"] == []
        assert attr["summary"] == "整体良好"

    def test_loop_flag_overrides_lowest_dim(self):
        scores = {"relevance": 95.0, "action_loop": 0.0}
        flags = {"loop_detected": True}
        attr = structured_root_cause(scores, flags)
        assert attr["category"] == "tool"
        assert attr["dim_key"] == "action_loop"
        assert attr["severity"] == "high"
        assert set(attr["levers"]) == {"prompt_patch", "tool_policy"}
        # 文案与结构化类别同源，不会互相矛盾
        assert attr["summary"] == root_cause_of(scores, flags)

    def test_lowest_dim_maps_category_and_levers(self):
        attr = structured_root_cause({"relevance": 60.0, "hallucination": 30.0}, {})
        assert attr["category"] == "model"
        assert attr["dim_key"] == "hallucination"
        assert attr["dim_score"] == 30.0
        assert attr["severity"] == "high"
        assert "model_swap" in attr["levers"]

    def test_severity_medium_boundary(self):
        attr = structured_root_cause({"instruction_following": 50.0}, {})
        assert attr["category"] == "prompt"
        assert attr["severity"] == "medium"

    def test_tool_error_trajectory_branch(self):
        attr = structured_root_cause(
            {"trajectory": 10.0, "hallucination": 80.0}, {"tool_error_count": 3})
        assert attr["category"] == "tool"
        assert attr["dim_key"] == "trajectory"


# ---------------------------------------------------------------- fixtures / seeds


@pytest.fixture
def inline_workers(monkeypatch):
    """任务 / 校准线程内联执行、SessionLocal 指向测试库（同既有 inline_worker
    语义，覆盖 calibration_service 模块）。"""
    import threading as real_threading

    from tests.conftest import TestSession

    monkeypatch.setattr(eval_service, "SessionLocal", TestSession)
    monkeypatch.setattr(calibration_service, "SessionLocal", TestSession)
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

    monkeypatch.setattr(eval_service.threading, "Thread", InlineThread)
    monkeypatch.setattr(calibration_service.threading, "Thread", InlineThread)


@pytest.fixture
def editor_token(client, editor_user):
    r = client.post("/api/v1/auth/login",
                    json={"username": "editor", "password": "editor123"})
    return r.json()["data"]["access_token"]


def _loop_steps() -> list[dict]:
    step = {"toolName": "sql_query", "status": "success",
            "arguments": {"sql": "select 1"}, "preview": "1"}
    return [dict(step), dict(step), dict(step)]


def _seed_conversation(db, user_id: str, title: str = "销量分析",
                       steps: list | None = None) -> str:
    conv = SuperAssistantConversation(owner_id=user_id, title=title)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    db.add(SuperAssistantMessage(conversation_id=conv.id, role="user",
                                 content="帮我看看上月销量", status="complete"))
    db.add(SuperAssistantMessage(
        conversation_id=conv.id, role="assistant",
        content="上月销量 1200 件。", status="complete",
        steps=steps if steps is not None else [
            {"toolName": "sql_query", "status": "success",
             "arguments": {"sql": "select sum(qty) from orders"}, "preview": "1200"},
        ],
    ))
    db.commit()
    return conv.id


def _seed_model_config(db, created_by: str = ""):
    from app.model_configs.models import ModelConfig

    config = ModelConfig(
        id=str(uuid.uuid4()), name="judge-stub", config_type="llm",
        provider="openai", api_base="https://example.invalid/v1",
        api_key_encrypted=None, models=["stub-model"], options={},
        enabled=True, is_default=True, created_by=created_by or str(uuid.uuid4()),
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def _create_task(client, auth_headers, conv_id: str, dimension_keys=("action_loop",)) -> dict:
    r = client.post("/api/v1/assistant-evaluation/tasks", json={
        "assistant_key": "super_assistant",
        "conversation_ids": [conv_id],
        "dimension_keys": list(dimension_keys),
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---------------------------------------------------------------- 任务归因 / 时间线


def test_task_structured_attribution_and_timeline(client, auth_headers, db, admin_user,
                                                  inline_workers):
    conv_id = _seed_conversation(db, admin_user.id, title="循环坏例", steps=_loop_steps())
    task = _create_task(client, auth_headers, conv_id)
    assert task["created_by"] == admin_user.id

    detail = client.get(f"/api/v1/assistant-evaluation/tasks/{task['id']}",
                        headers=auth_headers).json()["data"]
    item = detail["items"][0]
    assert item["attribution"]["category"] == "tool"
    assert item["attribution"]["dim_key"] == "action_loop"
    assert item["root_cause"] == item["attribution"]["summary"]

    insights = detail["summary"]["insights"]
    assert insights["by_category"]["tool"]["count"] == 1
    assert insights["by_category"]["tool"]["conversation_ids"] == [conv_id]
    assert set(insights["by_category"]["tool"]["levers"]) == {"prompt_patch", "tool_policy"}
    assert insights["suggested_levers"]

    # 明细行落库（worker 经 TestSession 写入同一测试库）
    row = db.query(AssistantEvalItem).filter(
        AssistantEvalItem.task_id == task["id"]).first()
    db.refresh(row)
    assert row.attribution["category"] == "tool"

    events = client.get("/api/v1/assistant-evaluation/timeline?limit=50",
                        headers=auth_headers).json()["data"]
    types = [e["event_type"] for e in events]
    assert "task_created" in types
    assert "task_succeeded" in types
    created = next(e for e in events if e["event_type"] == "task_created")
    assert created["actor"] == "admin"
    assert created["actor_user_id"] == admin_user.id
    succeeded = next(e for e in events if e["event_type"] == "task_succeeded")
    assert succeeded["actor"] == "system"
    assert succeeded["detail"]["insights"]["by_category"]["tool"]["count"] == 1

    task_events = client.get(
        f"/api/v1/assistant-evaluation/timeline?ref_type=task&ref_id={task['id']}",
        headers=auth_headers).json()["data"]
    assert {e["event_type"] for e in task_events} == {"task_created", "task_succeeded"}
    assert task_events[0]["event_type"] == "task_succeeded"  # 时间倒序


# ---------------------------------------------------------------- 基准集


def test_benchmark_crud_and_stable_split(client, auth_headers, db, admin_user):
    c1 = _seed_conversation(db, admin_user.id, title="会话一")
    c2 = _seed_conversation(db, admin_user.id, title="会话二")

    r = client.post("/api/v1/assistant-evaluation/benchmarks", json={
        "assistant_key": "super_assistant", "name": "回归基准",
        "items": [{"conversation_id": c1}, {"conversation_id": c2, "split": "heldout"}],
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    bench = r.json()["data"]
    assert bench["item_count"] == 2
    assert bench["train_count"] + bench["heldout_count"] == 2

    detail = client.get(
        f"/api/v1/assistant-evaluation/benchmarks/{bench['id']}",
        headers=auth_headers).json()["data"]
    by_conv = {i["conversation_id"]: i for i in detail["items"]}
    assert by_conv[c2]["split"] == "heldout"                 # 显式指定优先
    assert by_conv[c1]["split"] == benchmark_service.split_for(c1)  # 稳定哈希
    assert by_conv[c1]["origin"] == "manual"

    listing = client.get("/api/v1/assistant-evaluation/benchmarks",
                         headers=auth_headers).json()["data"]
    assert any(b["id"] == bench["id"] for b in listing)

    # 重复条目拒绝；新会话可追加
    dup = client.post(f"/api/v1/assistant-evaluation/benchmarks/{bench['id']}/items",
                      json={"items": [{"conversation_id": c1}]}, headers=auth_headers)
    assert dup.status_code == 400
    c3 = _seed_conversation(db, admin_user.id, title="会话三")
    add = client.post(f"/api/v1/assistant-evaluation/benchmarks/{bench['id']}/items",
                      json={"items": [{"conversation_id": c3}]}, headers=auth_headers)
    assert add.status_code == 200
    assert add.json()["data"]["item_count"] == 3

    item_id = by_conv[c2]["id"]
    removed = client.delete(
        f"/api/v1/assistant-evaluation/benchmarks/{bench['id']}/items/{item_id}",
        headers=auth_headers)
    assert removed.status_code == 200
    again = client.delete(
        f"/api/v1/assistant-evaluation/benchmarks/{bench['id']}/items/{item_id}",
        headers=auth_headers)
    assert again.status_code == 404

    deleted = client.delete(
        f"/api/v1/assistant-evaluation/benchmarks/{bench['id']}",
        headers=auth_headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/assistant-evaluation/benchmarks/{bench['id']}",
                      headers=auth_headers).status_code == 404


def test_benchmark_rejects_foreign_conversation(client, auth_headers, db, admin_user,
                                                ontology):
    r = client.post("/api/v1/assistant-evaluation/benchmarks", json={
        "assistant_key": "ontology_agent", "ontology_id": ontology["id"],
        "name": "错误归属",
        "items": [{"conversation_id": str(uuid.uuid4())}],
    }, headers=auth_headers)
    assert r.status_code == 400
    assert "不存在" in r.json()["detail"]


def test_benchmark_from_task_badcases(client, auth_headers, db, admin_user,
                                      inline_workers):
    conv_id = _seed_conversation(db, admin_user.id, title="循环坏例", steps=_loop_steps())
    task = _create_task(client, auth_headers, conv_id)
    assert task["status"] == "success"

    r = client.post("/api/v1/assistant-evaluation/benchmarks/from-task", json={
        "task_id": task["id"], "include": "badcase",
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    bench = r.json()["data"]
    assert bench["source_task_id"] == task["id"]
    assert bench["item_count"] == 1

    detail = client.get(
        f"/api/v1/assistant-evaluation/benchmarks/{bench['id']}",
        headers=auth_headers).json()["data"]
    assert detail["items"][0]["origin"] == "badcase"
    assert detail["items"][0]["conversation_id"] == conv_id

    events = client.get(
        "/api/v1/assistant-evaluation/timeline?ref_type=benchmark_set"
        f"&ref_id={bench['id']}", headers=auth_headers).json()["data"]
    assert [e["event_type"] for e in events] == ["benchmark_created"]


# ---------------------------------------------------------------- 噪声校准


def test_calibration_code_only_zero_noise(client, auth_headers, db, admin_user,
                                          inline_workers):
    conv_id = _seed_conversation(db, admin_user.id, steps=_loop_steps())
    r = client.post("/api/v1/assistant-evaluation/calibrations", json={
        "assistant_key": "super_assistant",
        "conversation_ids": [conv_id],
        "repeats": 3,
        "dimension_keys": ["action_loop"],
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    cal = r.json()["data"]
    assert cal["status"] == "success"
    assert cal["judge_model_name"] == "（仅代码型维度，无需 judge 模型）"

    result = cal["result"]
    assert result["repeats"] == 3
    assert result["scored_conversations"] == 1
    # 代码型维度确定性：重复评分方差恒为 0
    assert result["per_dim"]["action_loop"]["noise"] == 0.0
    assert result["overall_noise"] == 0.0


def test_calibration_measures_judge_variance(client, auth_headers, db, admin_user,
                                             inline_workers, monkeypatch):
    _seed_model_config(db, created_by=admin_user.id)
    conv_id = _seed_conversation(db, admin_user.id)
    calls = {"n": 0}

    async def fake_evaluate(engine, dim_keys, trace, rubric=None):
        calls["n"] += 1
        raw = 5.0 if calls["n"] % 2 == 1 else 3.0   # 归一化后 100 / 50
        return {k: {"raw": raw, "reason": ""} for k in dim_keys}

    monkeypatch.setattr(eval_service, "_evaluate_async", fake_evaluate)

    r = client.post("/api/v1/assistant-evaluation/calibrations", json={
        "assistant_key": "super_assistant",
        "conversation_ids": [conv_id],
        "repeats": 2,
        "dimension_keys": ["relevance"],
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    result = r.json()["data"]["result"]
    # |100-50|/2 = 25.0：judge 方差被如实测出并作为投产阈值的下界
    assert result["per_dim"]["relevance"]["noise"] == 25.0
    assert result["overall_noise"] == 25.0

    events = client.get(
        "/api/v1/assistant-evaluation/timeline?ref_type=calibration",
        headers=auth_headers).json()["data"]
    types = {e["event_type"] for e in events}
    assert {"calibration_created", "calibration_succeeded"} <= types
    succeeded = next(e for e in events if e["event_type"] == "calibration_succeeded")
    assert succeeded["actor"] == "system"


def test_calibration_caps_and_benchmark_source(client, auth_headers, db, admin_user,
                                               inline_workers):
    # action_loop 维度要求 ≥2 个工具动作，种子带重复步骤
    ids = [_seed_conversation(db, admin_user.id, title=f"会话{i}", steps=_loop_steps())
           for i in range(3)]
    # 上限保护：超过 10 条直接拒绝
    overflow = client.post("/api/v1/assistant-evaluation/calibrations", json={
        "assistant_key": "super_assistant",
        "conversation_ids": [str(uuid.uuid4()) for _ in range(11)],
        "dimension_keys": ["action_loop"],
    }, headers=auth_headers)
    assert overflow.status_code == 400

    bench = client.post("/api/v1/assistant-evaluation/benchmarks", json={
        "assistant_key": "super_assistant", "name": "校准基准",
        "items": [{"conversation_id": cid} for cid in ids],
    }, headers=auth_headers).json()["data"]

    r = client.post("/api/v1/assistant-evaluation/calibrations", json={
        "assistant_key": "super_assistant",
        "benchmark_set_id": bench["id"],
        "dimension_keys": ["action_loop"],
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    cal = r.json()["data"]
    assert cal["status"] == "success"
    assert sorted(cal["params"]["conversation_ids"]) == sorted(ids)

    detail = client.get(
        f"/api/v1/assistant-evaluation/calibrations/{cal['id']}",
        headers=auth_headers)
    assert detail.status_code == 200
    listing = client.get("/api/v1/assistant-evaluation/calibrations",
                         headers=auth_headers).json()["data"]
    assert any(c["id"] == cal["id"] for c in listing)
    deleted = client.delete(
        f"/api/v1/assistant-evaluation/calibrations/{cal['id']}",
        headers=auth_headers)
    assert deleted.status_code == 200


# ---------------------------------------------------------------- RBAC


def test_new_endpoints_require_admin(client, editor_token):
    for path in ("/api/v1/assistant-evaluation/benchmarks",
                 "/api/v1/assistant-evaluation/calibrations",
                 "/api/v1/assistant-evaluation/timeline"):
        r = client.get(path, headers={"Authorization": f"Bearer {editor_token}"})
        assert r.status_code == 403
