"""数据管家（steward）的治理与集成测试：

  1. 治理状态机：创建即草稿（不激活）→ 提交 → 批准（激活 + 注册 published 影子
     流水线）→ agent 修改自动降级停用 → 重审批 → 转回草稿 / 拒绝 / 归档
  2. 影子流水线守卫：engine=n8n 的 v2_pipelines 记录禁止画布式 update/publish/
     delete；validate 跟随审批状态
  3. 工具边界：connections 引用缺失节点报错回给 LLM；同名去重；节点字段补全
  4. runner 行数据规整：webhook 响应体 / 执行末节点 items → list[dict]
"""
import uuid

import pytest

from app.data_channel.steward import service
from app.data_channel.steward.models import N8nPipeline
from app.data_channel.steward.runner import normalize_rows, _extract_execution_rows
from app.data_channel.steward.toolkit import ToolRunner
from app.models.v2.pipeline import Pipeline


# ── 假 n8n 客户端 ──────────────────────────────────────────────────

class FakeN8nClient:
    """内存版 n8n：workflows CRUD + 激活状态跟踪。"""

    def __init__(self):
        self.workflows: dict[str, dict] = {}
        self.calls: list[str] = []
        self._executions: list = []

    @staticmethod
    def sanitize_workflow(payload: dict) -> dict:
        from app.settings.workflows.n8n_client import N8nClient
        return N8nClient.sanitize_workflow(payload)

    def test_connection(self):
        return True

    def list_workflows(self, **kwargs):
        return list(self.workflows.values())

    def get_workflow(self, workflow_id: str) -> dict:
        return dict(self.workflows[str(workflow_id)])

    def create_workflow(self, payload: dict) -> dict:
        wid = str(len(self.workflows) + 1)
        wf = {**self.sanitize_workflow(payload), "id": wid, "active": False}
        self.workflows[wid] = wf
        self.calls.append(f"create:{wid}")
        return dict(wf)

    def update_workflow(self, workflow_id: str, payload: dict) -> dict:
        wf = self.workflows[str(workflow_id)]
        wf.update(self.sanitize_workflow(payload))
        self.calls.append(f"update:{workflow_id}")
        return dict(wf)

    def activate_workflow(self, workflow_id: str):
        self.workflows[str(workflow_id)]["active"] = True
        self.calls.append(f"activate:{workflow_id}")

    def deactivate_workflow(self, workflow_id: str):
        self.workflows[str(workflow_id)]["active"] = False
        self.calls.append(f"deactivate:{workflow_id}")

    def delete_workflow(self, workflow_id: str):
        self.workflows.pop(str(workflow_id), None)
        self.calls.append(f"delete:{workflow_id}")

    def list_executions(self, **kwargs):
        return list(self._executions)

    def get_execution(self, execution_id: str, include_data: bool = False):
        for e in self._executions:
            if str(e["id"]) == str(execution_id):
                return e
        return {"id": execution_id, "status": "success"}

    # 试跑路径：trigger_webhook 即"产生"一次成功执行（末节点 2 行）
    def trigger_webhook(self, webhook_path, payload=None, timeout_seconds=None):
        eid = str(len(self._executions) + 100)
        self._executions = self._executions + [{
            "id": eid, "status": "success", "startedAt": "t1", "stoppedAt": "t2",
            "data": {"resultData": {
                "lastNodeExecuted": "整理字段",
                "runData": {"整理字段": [{"data": {"main": [[
                    {"json": {"currency": "USD", "rate": 1.0}},
                    {"json": {"currency": "CNY", "rate": 7.1}},
                ]]}}]},
            }},
        }]
        self.calls.append(f"webhook:{webhook_path}")
        return 200, [{"currency": "USD", "rate": 1.0}]


WEBHOOK_NODES = [
    {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
     "parameters": {"httpMethod": "POST", "path": "ob-test", "responseMode": "lastNode"}},
    {"name": "整理字段", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "parameters": {}},
]
WEBHOOK_CONNS = {"Webhook": {"main": [[{"node": "整理字段", "type": "main", "index": 0}]]}}


@pytest.fixture
def fake_n8n(monkeypatch):
    fake = FakeN8nClient()
    monkeypatch.setattr(service, "get_n8n_client", lambda _db: fake)
    return fake


@pytest.fixture
def pipelines_client(client, db):
    """v2 pipelines 路由用的是模块内自建的 get_db（非 app.deps.get_db），
    需要单独覆盖才能命中测试库。client fixture 结束时统一 clear。"""
    from app.main import app
    from app.data_channel.pipelines.router import get_db as pipelines_get_db

    def override():
        yield db

    app.dependency_overrides[pipelines_get_db] = override
    return client


@pytest.fixture
def draft_record(db, fake_n8n):
    """经工具集创建的草稿记录（等价于 agent 在对话里创建）。"""
    runner = ToolRunner(db, user_id="u-test", conversation_id="c-test")
    out = runner.run("create_workflow", {
        "name": "订单同步流水线", "description": "测试用",
        "nodes": WEBHOOK_NODES, "connections": WEBHOOK_CONNS,
    })
    assert "error" not in out, out
    rec = db.query(N8nPipeline).filter(N8nPipeline.id == out["record"]["id"]).first()
    return rec


# ── 治理状态机 ────────────────────────────────────────────────────

def test_create_is_inactive_draft(db, fake_n8n, draft_record):
    assert draft_record.status == "draft"
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    # 创建即登记影子流水线（draft）——流水线列表立即可见；审批只决定能否被调度
    assert draft_record.pipeline_id is not None
    from app.models.v2.pipeline import Pipeline
    shadow = db.query(Pipeline).filter(Pipeline.id == draft_record.pipeline_id).first()
    assert shadow is not None
    assert shadow.status == "draft"
    assert (shadow.definition or {}).get("engine") == "n8n"
    # 名称去重
    runner = ToolRunner(db, None, None)
    dup = runner.run("create_workflow", {"name": "订单同步流水线",
                                         "nodes": WEBHOOK_NODES, "connections": WEBHOOK_CONNS})
    assert "error" in dup and "同名" in dup["error"]


def test_approve_activates_and_registers_published_pipeline(pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    rid = draft_record.id
    # 未提交不能批准
    r = client.post(f"/api/v2/steward/pipelines/{rid}/approve", headers=auth_headers)
    assert r.status_code == 400

    r = client.post(f"/api/v2/steward/pipelines/{rid}/submit", headers=auth_headers)
    assert r.status_code == 200
    r = client.post(f"/api/v2/steward/pipelines/{rid}/approve", headers=auth_headers)
    assert r.status_code == 200, r.text
    db.refresh(draft_record)

    assert draft_record.status == "approved"
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is True
    assert draft_record.approved_snapshot is not None

    pl = db.query(Pipeline).filter(Pipeline.id == draft_record.pipeline_id).first()
    assert pl is not None and pl.status == "published"
    assert pl.definition["engine"] == "n8n"
    assert pl.definition["n8n"]["steward_id"] == rid
    assert pl.definition["n8n"]["webhook_path"] == "ob-test"

    # validate：已批准 → 合法
    r = client.post(f"/api/v2/pipelines/{pl.id}/validate", headers=auth_headers)
    assert r.status_code == 200 and r.json()["valid"] is True


def test_agent_edit_demotes_approved(pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    rid = draft_record.id
    client.post(f"/api/v2/steward/pipelines/{rid}/submit", headers=auth_headers)
    client.post(f"/api/v2/steward/pipelines/{rid}/approve", headers=auth_headers)
    db.refresh(draft_record)
    assert draft_record.status == "approved"

    runner = ToolRunner(db, None, None)
    out = runner.run("update_workflow", {
        "record_id": rid,
        "nodes": WEBHOOK_NODES + [{"name": "过滤", "type": "n8n-nodes-base.filter",
                                   "typeVersion": 2.2, "parameters": {}}],
    })
    assert "error" not in out, out
    assert "退回草稿" in out.get("notice", "")
    db.refresh(draft_record)
    assert draft_record.status == "draft"
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    # 影子流水线降级为 draft → 任务池"必须已发布"校验自然拦截
    pl = db.query(Pipeline).filter(Pipeline.id == draft_record.pipeline_id).first()
    assert pl.status == "draft"
    # validate 此时报未批准
    r = client.post(f"/api/v2/pipelines/{pl.id}/validate", headers=auth_headers)
    assert r.json()["valid"] is False


def test_reject_revoke_archive_flow(client, auth_headers, db, fake_n8n, draft_record):
    rid = draft_record.id
    client.post(f"/api/v2/steward/pipelines/{rid}/submit", headers=auth_headers)
    r = client.post(f"/api/v2/steward/pipelines/{rid}/reject", headers=auth_headers,
                    json={"reason": "字段不完整"})
    assert r.status_code == 200
    db.refresh(draft_record)
    assert draft_record.status == "rejected"
    assert draft_record.reject_reason == "字段不完整"

    # 被拒绝后可重新提交并批准
    client.post(f"/api/v2/steward/pipelines/{rid}/submit", headers=auth_headers)
    client.post(f"/api/v2/steward/pipelines/{rid}/approve", headers=auth_headers)
    db.refresh(draft_record)
    assert draft_record.status == "approved"

    # 已批准不能直接归档
    r = client.delete(f"/api/v2/steward/pipelines/{rid}", headers=auth_headers)
    assert r.status_code == 400

    # 转回草稿：停用 + 影子降级
    r = client.post(f"/api/v2/steward/pipelines/{rid}/revoke", headers=auth_headers)
    assert r.status_code == 200
    db.refresh(draft_record)
    assert draft_record.status == "draft"
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False

    # 归档后从面板消失，影子流水线一并从列表移除
    shadow_id = draft_record.pipeline_id
    r = client.delete(f"/api/v2/steward/pipelines/{rid}", headers=auth_headers)
    assert r.status_code == 200
    r = client.get("/api/v2/steward/pipelines", headers=auth_headers)
    assert all(item["id"] != rid for item in r.json()["data"])
    from app.models.v2.pipeline import Pipeline
    db.expire_all()
    assert db.query(Pipeline).filter(Pipeline.id == shadow_id).first() is None
    db.refresh(draft_record)
    assert draft_record.pipeline_id is None


def test_submit_requires_trigger(db, fake_n8n):
    runner = ToolRunner(db, None, None)
    out = runner.run("create_workflow", {
        "name": "无触发器流水线",
        "nodes": [{"name": "整理", "type": "n8n-nodes-base.set", "parameters": {}}],
        "connections": {},
    })
    rec_id = out["record"]["id"]
    res = runner.run("submit_for_approval", {"record_id": rec_id})
    assert "error" in res and "触发器" in res["error"]


# ── 影子流水线的画布路径守卫 ──────────────────────────────────────

def test_shadow_pipeline_guards(pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    rid = draft_record.id
    client.post(f"/api/v2/steward/pipelines/{rid}/submit", headers=auth_headers)
    client.post(f"/api/v2/steward/pipelines/{rid}/approve", headers=auth_headers)
    db.refresh(draft_record)
    pid = draft_record.pipeline_id

    # 名称/描述/契约平台侧放行（0008 生命周期收敛后的新行为），且回同步管家记录
    r = client.put(f"/api/v2/pipelines/{pid}", headers=auth_headers, json={"name": "改名"})
    assert r.status_code == 200
    db.refresh(draft_record)
    assert draft_record.name == "改名"
    # 编排字段仍归数据管家托管
    r = client.put(f"/api/v2/pipelines/{pid}", headers=auth_headers, json={"definition": {"nodes": []}})
    assert r.status_code == 400
    r = client.post(f"/api/v2/pipelines/{pid}/publish", headers=auth_headers)
    assert r.status_code == 400
    r = client.delete(f"/api/v2/pipelines/{pid}", headers=auth_headers)
    assert r.status_code == 400
    # 列表里可见（published）
    r = client.get("/api/v2/pipelines", headers=auth_headers)
    assert any(p["id"] == pid for p in r.json())


# ── 工具边界 ──────────────────────────────────────────────────────

def test_toolkit_validates_connections(db, fake_n8n):
    runner = ToolRunner(db, None, None)
    out = runner.run("create_workflow", {
        "name": "坏连接",
        "nodes": WEBHOOK_NODES,
        "connections": {"Webhook": {"main": [[{"node": "不存在的节点", "type": "main", "index": 0}]]}},
    })
    assert "error" in out and "不存在的目标节点" in out["error"]
    # 错误发生在 n8n 写入之前
    assert fake_n8n.workflows == {}


def test_toolkit_normalizes_nodes(db, fake_n8n):
    runner = ToolRunner(db, None, None)
    out = runner.run("create_workflow", {
        "name": "字段补全",
        "nodes": [{"name": "Webhook", "type": "n8n-nodes-base.webhook",
                   "parameters": {"path": "x"}}],
        "connections": {},
    })
    assert "error" not in out, out
    wf = fake_n8n.workflows[out["record"]["n8nWorkflowId"]]
    node = wf["nodes"][0]
    assert node["id"] and node["position"] and node["typeVersion"] == 1


# ── runner 行数据规整 ─────────────────────────────────────────────

def test_normalize_rows_variants():
    assert normalize_rows(None) == []
    assert normalize_rows({"a": 1}) == [{"a": 1}]
    assert normalize_rows([{"json": {"a": 1}}, {"json": {"a": 2}}]) == [{"a": 1}, {"a": 2}]
    assert normalize_rows([{"a": 1}, "raw"]) == [{"a": 1}, {"value": "raw"}]


def test_tool_test_run(monkeypatch, db, fake_n8n, draft_record):
    # 试跑不 sleep：直接返回 fake 里预置的成功执行
    monkeypatch.setattr("app.data_channel.steward.runner.time.sleep", lambda *_: None)
    runner = ToolRunner(db, None, None)
    out = runner.run("test_run", {"record_id": draft_record.id})
    assert "error" not in out or out.get("error") is None, out
    assert out["rows"] == 2
    assert out["columns"] == ["currency", "rate"]
    assert out["sample"][0]["currency"] == "USD"
    # 试跑不改变治理状态，也不写湖：草稿仍是草稿、结束后停用还原
    db.refresh(draft_record)
    assert draft_record.status == "draft"
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False


def test_probe_url_guards_and_json_shape(db, fake_n8n):
    from app.data_channel.steward.toolkit import _json_shape

    runner = ToolRunner(db, None, None)
    assert "http" in runner.run("probe_url", {"url": "ftp://x"})["error"]
    assert "不允许" in runner.run("probe_url", {"url": "http://169.254.169.254/meta"})["error"]

    shape = _json_shape({"items": [{"name": "a" * 200, "score": 1}] * 50, "total": 50})
    assert shape["items"]["__type"] == "array[50]"
    assert shape["items"]["item"]["name"].endswith("…")  # 长字符串截断
    assert shape["total"] == 50


def test_extract_execution_rows():
    execution = {
        "id": "77", "status": "success", "startedAt": "t1", "stoppedAt": "t2",
        "data": {"resultData": {
            "lastNodeExecuted": "整理字段",
            "runData": {"整理字段": [{"data": {"main": [[{"json": {"a": 1}}, {"json": {"a": 2}}]]}}]},
        }},
    }
    rows, meta = _extract_execution_rows(execution)
    assert rows == [{"a": 1}, {"a": 2}]
    assert meta["execution_id"] == "77" and meta["last_node"] == "整理字段"

    failed = {"id": "78", "status": "error",
              "data": {"resultData": {"error": {"message": "boom", "node": {"name": "HTTP"}}}}}
    rows, meta = _extract_execution_rows(failed)
    assert rows == [] and "boom" in meta["error"] and "HTTP" in meta["error"]
