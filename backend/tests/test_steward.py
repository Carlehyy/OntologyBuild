"""数据管家（steward）的治理与集成测试：

  1. 生命周期（发布唯一入口 = 流水线编辑向导的 publish/unpublish 端点）：
     新建即未发布（不激活）→ publish 激活 + 封版 + 固化 definition →
     已发布编排封版（数据管家修改被拒）→ unpublish 停用回草稿可继续编排
  2. 职权边界（管家只有两项写权限）：新建骨架（create_pipeline）+ 编排
     「未发布 且 未启用」的流水线（update_workflow）。已启用即拒编排；
     试跑 / 纳管 / 执行观测 / 归档删除都不再是管家职权。
  3. 编排工具边界：connections 引用缺失节点报错回给 LLM；节点字段补全
  4. runner 行数据规整：webhook 响应体 / 执行末节点 items → list[dict]
"""
import pytest

from app.data_channel.steward import service
from app.data_channel.steward.models import N8nPipeline
from app.data_channel.steward.runner import normalize_rows, _extract_execution_rows
from app.data_channel.steward.toolkit import ToolRunner
from app.models.v2.pipeline import Pipeline, PipelineVersion


# ── 假 n8n 客户端 ──────────────────────────────────────────────────

class FakeN8nClient:
    """内存版 n8n：workflows CRUD + 激活状态跟踪。"""

    def __init__(self):
        self.workflows: dict[str, dict] = {}
        self.calls: list[str] = []
        self._executions: list = []
        self.credentials: list = []

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

    def list_credentials(self, **kwargs):
        return list(self.credentials)

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
    """经工具集新建并编排的未发布记录（等价于 agent 在对话里建骨架再补全节点）：
    create_pipeline 出 Webhook→输出 骨架，update_workflow 换成 Webhook→Set 取数链路。"""
    runner = ToolRunner(db, user_id="u-test", conversation_id="c-test")
    created = runner.run("create_pipeline", {"name": "订单同步流水线", "description": "测试用"})
    assert "error" not in created, created
    rid = created["record"]["id"]
    # 后续用例依赖 WEBHOOK_NODES 这套节点（webhook path=ob-test、末节点=整理字段）
    updated = runner.run("update_workflow", {
        "record_id": rid, "nodes": WEBHOOK_NODES, "connections": WEBHOOK_CONNS,
    })
    assert "error" not in updated, updated
    return db.query(N8nPipeline).filter(N8nPipeline.id == rid).first()


def _publish(client, auth_headers, pipeline_id: str, enable: bool = False):
    return client.post(f"/api/v2/pipelines/{pipeline_id}/publish",
                       headers=auth_headers, json={"enable": enable})


# ── 生命周期：新建 → 发布 → 封版 → 撤回 ───────────────────────────

def test_create_is_inactive_draft(db, fake_n8n, draft_record):
    assert draft_record.status == "draft"
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    # 新建即登记影子流水线（draft）——流水线列表立即可见；发布决定能否被调度
    assert draft_record.pipeline_id is not None
    shadow = db.query(Pipeline).filter(Pipeline.id == draft_record.pipeline_id).first()
    assert shadow is not None
    assert shadow.status == "draft"
    assert (shadow.definition or {}).get("engine") == "n8n"
    # 名称去重
    runner = ToolRunner(db, None, None)
    dup = runner.run("create_pipeline", {"name": "订单同步流水线"})
    assert "error" in dup and "同名" in dup["error"]


def test_publish_activates_and_seals(pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """发布唯一入口 = 编辑向导的 publish 端点：激活 workflow + 封版 + 固化 definition。"""
    pid = draft_record.pipeline_id

    # 未发布时 validate 也必须通过——否则发布流程死锁
    r = client.post(f"/api/v2/pipelines/{pid}/validate", headers=auth_headers)
    assert r.status_code == 200 and r.json()["valid"] is True, r.text

    r = _publish(client, auth_headers, pid, enable=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "published" and body["enabled"] is True

    # n8n 侧被激活
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is True

    db.expire_all()
    pl = db.query(Pipeline).filter(Pipeline.id == pid).first()
    assert pl.status == "published" and pl.enabled is True
    assert pl.definition["engine"] == "n8n"
    assert pl.definition["n8n"]["steward_id"] == draft_record.id
    assert pl.definition["n8n"]["webhook_path"] == "ob-test"
    # 版本快照与发布同一事务
    snaps = db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_id == pid, PipelineVersion.status == "published").all()
    assert len(snaps) == 1

    # 重复发布被拒
    r = _publish(client, auth_headers, pid)
    assert r.status_code == 400


def test_publish_fixes_expected_columns_from_wizard_preview(
        pipelines_client, client, auth_headers, db, monkeypatch, fake_n8n, draft_record):
    """发布固化最近一次执行预览的列集合为期望列契约（运行期漂移检测基线）。

    未发布 n8n 的执行预览走 runner.collect_test_rows（临时激活→触发→还原）+
    persist_test_result，与流水线编辑向导第 2 步同一条通道——管家已无试跑工具。"""
    monkeypatch.setattr("app.data_channel.steward.runner.time.sleep", lambda *_: None)
    from app.data_channel.steward.runner import collect_test_rows, persist_test_result

    rows, exec_meta = collect_test_rows(db, draft_record)
    assert exec_meta.get("error") is None, exec_meta
    persist_test_result(db, draft_record, rows, exec_meta)
    # 预览不改变发布/激活状态：临时激活后已还原为未激活
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False

    r = _publish(client, auth_headers, draft_record.pipeline_id)
    assert r.status_code == 200, r.text
    db.expire_all()
    pl = db.query(Pipeline).filter(Pipeline.id == draft_record.pipeline_id).first()
    assert pl.definition["n8n"]["expected_columns"] == ["currency", "rate"]


def test_published_is_sealed_for_steward_edit(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """已发布 = 编排封版：数据管家修改被拒，n8n 侧保持激活。"""
    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid).status_code == 200

    runner = ToolRunner(db, None, None)
    out = runner.run("update_workflow", {
        "record_id": draft_record.id,
        "nodes": WEBHOOK_NODES + [{"name": "过滤", "type": "n8n-nodes-base.filter",
                                   "typeVersion": 2.2, "parameters": {}}],
    })
    assert "error" in out and "撤回发布" in out["error"]
    # 封版未被破坏：workflow 仍激活、影子仍 published、节点未变
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is True
    assert len(fake_n8n.workflows[draft_record.n8n_workflow_id]["nodes"]) == 2
    db.expire_all()
    pl = db.query(Pipeline).filter(Pipeline.id == pid).first()
    assert pl.status == "published"


def test_active_workflow_rejected_for_orchestration(db, fake_n8n, draft_record):
    """未启用约束：即便影子仍未发布，只要 n8n 侧已激活，编排也被拒（漂移兜底）。"""
    fake_n8n.activate_workflow(draft_record.n8n_workflow_id)
    runner = ToolRunner(db, None, None)
    out = runner.run("update_workflow", {
        "record_id": draft_record.id,
        "nodes": WEBHOOK_NODES + [{"name": "过滤", "type": "n8n-nodes-base.filter",
                                   "typeVersion": 2.2, "parameters": {}}],
    })
    assert "error" in out and "启用" in out["error"]
    # 未写入 n8n：节点仍是编排前的 2 个
    assert len(fake_n8n.workflows[draft_record.n8n_workflow_id]["nodes"]) == 2


def test_unpublish_deactivates_and_reopens_editing(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """撤回发布：停用 workflow + 回草稿 + 停用启用开关，之后可继续编排。"""
    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid, enable=True).status_code == 200

    r = client.post(f"/api/v2/pipelines/{pid}/unpublish", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    db.expire_all()
    pl = db.query(Pipeline).filter(Pipeline.id == pid).first()
    assert pl.status == "draft" and pl.enabled is False

    # 回草稿（未发布 且 未启用）后编排恢复可写
    runner = ToolRunner(db, None, None)
    out = runner.run("update_workflow", {
        "record_id": draft_record.id,
        "nodes": WEBHOOK_NODES + [{"name": "过滤", "type": "n8n-nodes-base.filter",
                                   "typeVersion": 2.2, "parameters": {}}],
    })
    assert "error" not in out, out

    # 再次发布：版本号语义 = 已发布快照序号，第二次发布才递增
    assert _publish(client, auth_headers, pid).status_code == 200
    db.expire_all()
    pl = db.query(Pipeline).filter(Pipeline.id == pid).first()
    assert pl.version == 2


def test_publish_requires_trigger(pipelines_client, client, auth_headers, db, fake_n8n):
    """无触发器的工作流不能发布（不可调度的发布没有意义）。

    骨架自带 Webhook 触发器，这里用 update_workflow 把它编排掉再试发布。"""
    runner = ToolRunner(db, None, None)
    created = runner.run("create_pipeline", {"name": "无触发器流水线"})
    rid = created["record"]["id"]
    up = runner.run("update_workflow", {
        "record_id": rid,
        "nodes": [{"name": "整理", "type": "n8n-nodes-base.set", "parameters": {}}],
        "connections": {},
    })
    assert "error" not in up, up
    rec = db.query(N8nPipeline).filter(N8nPipeline.id == rid).first()
    r = _publish(client, auth_headers, rec.pipeline_id)
    assert r.status_code == 400 and "触发器" in r.json()["detail"]
    assert fake_n8n.workflows[rec.n8n_workflow_id]["active"] is False


# ── 影子流水线的画布路径守卫 ──────────────────────────────────────

def test_shadow_pipeline_guards(pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid).status_code == 200

    # 名称/描述/契约平台侧放行，且回同步管家记录
    r = client.put(f"/api/v2/pipelines/{pid}", headers=auth_headers, json={"name": "改名"})
    assert r.status_code == 200
    db.refresh(draft_record)
    assert draft_record.name == "改名"
    # 编排字段仍归数据管家托管
    r = client.put(f"/api/v2/pipelines/{pid}", headers=auth_headers, json={"definition": {"nodes": []}})
    assert r.status_code == 400
    # 列表里可见（published）
    r = client.get("/api/v2/pipelines", headers=auth_headers)
    assert any(p["id"] == pid for p in r.json())


def test_delete_pipeline_archives_record(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """流水线列表删除 n8n 流水线 = 归档治理记录：停用 workflow、影子行移除、
    n8n 侧工作流保留。归档只在流水线列表操作，不再是管家职权。"""
    pid = draft_record.pipeline_id
    rid = draft_record.id
    assert _publish(client, auth_headers, pid).status_code == 200

    r = client.delete(f"/api/v2/pipelines/{pid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(Pipeline).filter(Pipeline.id == pid).first() is None
    db.refresh(draft_record)
    assert draft_record.status == "archived"
    assert draft_record.pipeline_id is None
    # n8n 侧：停用但保留
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    # 数据管家面板不再展示
    r = client.get("/api/v2/steward/pipelines", headers=auth_headers)
    assert all(item["id"] != rid for item in r.json()["data"])


def test_panel_lists_only_orchestrable(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """受管流水线面板只列可编排的（未发布 且 未启用）——与 require_orchestrable 同口径：
    草稿可见 → 发布（封版+激活）后隐藏 → 撤回发布恢复 → n8n 侧漂移激活也隐藏。"""
    rid = draft_record.id
    panel_ids = lambda: [item["id"] for item in
                         client.get("/api/v2/steward/pipelines", headers=auth_headers).json()["data"]]

    # 未发布未启用 → 面板可见
    assert rid in panel_ids()

    # 发布 → 封版 + n8n 激活 → 从面板消失（改到流水线列表管理）
    assert _publish(client, auth_headers, draft_record.pipeline_id, enable=True).status_code == 200
    assert rid not in panel_ids()

    # 撤回发布回草稿（未发布 且 未启用）→ 面板恢复可见
    assert client.post(f"/api/v2/pipelines/{draft_record.pipeline_id}/unpublish",
                       headers=auth_headers).status_code == 200
    assert rid in panel_ids()

    # 漂移：影子仍未发布，但有人在 n8n 侧手动激活 → 也从面板隐藏
    fake_n8n.activate_workflow(draft_record.n8n_workflow_id)
    assert rid not in panel_ids()


# ── 编排工具边界 ──────────────────────────────────────────────────

def test_update_validates_connections(db, fake_n8n, draft_record):
    """编排时 connections 引用不存在的目标节点 → 报错回给 LLM，且不写 n8n。"""
    runner = ToolRunner(db, None, None)
    out = runner.run("update_workflow", {
        "record_id": draft_record.id,
        "nodes": WEBHOOK_NODES,
        "connections": {"Webhook": {"main": [[{"node": "不存在的节点", "type": "main", "index": 0}]]}},
    })
    assert "error" in out and "不存在的目标节点" in out["error"]
    # 校验发生在写 n8n 之前：工作流仍是编排前的 2 个节点
    assert len(fake_n8n.workflows[draft_record.n8n_workflow_id]["nodes"]) == 2


def test_update_normalizes_nodes(db, fake_n8n, draft_record):
    """编排时省略的 id/position/typeVersion 自动补全。"""
    runner = ToolRunner(db, None, None)
    out = runner.run("update_workflow", {
        "record_id": draft_record.id,
        "nodes": [{"name": "Webhook", "type": "n8n-nodes-base.webhook",
                   "parameters": {"path": "x"}}],
        "connections": {},
    })
    assert "error" not in out, out
    wf = fake_n8n.workflows[draft_record.n8n_workflow_id]
    node = wf["nodes"][0]
    assert node["id"] and node["position"] and node["typeVersion"] == 1


# ── 只读编排辅助工具（作者增强 / 调错，无副作用） ─────────────────

def test_describe_node_and_reference(db, fake_n8n):
    """describe_node 返回节点深挖详情；n8n_reference 返回表达式/Code/模式骨架。"""
    runner = ToolRunner(db, None, None)
    d = runner.run("describe_node", {"node_type": "httpRequest"})  # 短名可解析
    assert d["type"] == "n8n-nodes-base.httpRequest"
    assert "detail" in d and "example" in d["detail"]
    assert "error" in runner.run("describe_node", {"node_type": "根本不存在xyz"})

    assert "text" in runner.run("n8n_reference", {"topic": "expressions"})
    assert "text" in runner.run("n8n_reference", {"topic": "code"})
    pats = runner.run("n8n_reference", {"topic": "patterns"})
    assert len(pats["patterns"]) >= 3 and "nodes" in pats["patterns"][0]
    assert "error" in runner.run("n8n_reference", {"topic": "乱写"})


def test_inspect_runs_reads_latest_execution(db, fake_n8n, draft_record):
    """只读诊断：无记录给提示；有记录展开最近一次的报错/节点行数/末节点样例。"""
    runner = ToolRunner(db, None, None)
    empty = runner.run("inspect_runs", {"record_id": draft_record.id})
    assert empty["executions"] == [] and "手动执行" in empty["note"]

    fake_n8n._executions = [{
        "id": "e1", "status": "error", "startedAt": "t1", "stoppedAt": "t2",
        "data": {"resultData": {
            "lastNodeExecuted": "整理字段",
            "error": {"message": "boom", "node": {"name": "拉取"}},
            "runData": {"整理字段": [{"data": {"main": [[
                {"json": {"a": 1}}, {"json": {"a": 2}}]]}}]},
        }},
    }]
    out = runner.run("inspect_runs", {"record_id": draft_record.id})
    assert len(out["executions"]) == 1
    assert out["latest"]["error"]["message"] == "boom" and out["latest"]["error"]["node"] == "拉取"
    assert out["latest"]["nodeItemCounts"]["整理字段"] == 2
    assert out["latest"]["lastNodeSample"] == [{"a": 1}, {"a": 2}]


def test_check_credentials_flags_missing_and_present(db, fake_n8n, draft_record):
    """凭据缺口：比对工作流引用 vs 实例已配置，标出缺失；配齐后 note 变‘都已配置’。"""
    runner = ToolRunner(db, None, None)
    nodes = WEBHOOK_NODES + [{
        "name": "查询", "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
        "parameters": {"operation": "executeQuery", "query": "SELECT 1"},
        "credentials": {"postgres": {"id": "cred-x", "name": "prod-db"}},
    }]
    conns = {**WEBHOOK_CONNS, "整理字段": {"main": [[{"node": "查询", "type": "main", "index": 0}]]}}
    assert "error" not in runner.run("update_workflow", {
        "record_id": draft_record.id, "nodes": nodes, "connections": conns})

    fake_n8n.credentials = []  # 实例没配 → 缺
    out = runner.run("check_credentials", {"record_id": draft_record.id})
    assert len(out["referenced"]) == 1 and out["referenced"][0]["type"] == "postgres"
    assert len(out["missing"]) == 1

    fake_n8n.credentials = [{"id": "c9", "name": "prod-db", "type": "postgres"}]  # 同名同类型 → 齐
    out2 = runner.run("check_credentials", {"record_id": draft_record.id})
    assert out2["missing"] == [] and "都已配置" in out2["note"]


# ── runner 行数据规整 ─────────────────────────────────────────────

def test_normalize_rows_variants():
    assert normalize_rows(None) == []
    assert normalize_rows({"a": 1}) == [{"a": 1}]
    assert normalize_rows([{"json": {"a": 1}}, {"json": {"a": 2}}]) == [{"a": 1}, {"a": 2}]
    assert normalize_rows([{"a": 1}, "raw"]) == [{"a": 1}, {"value": "raw"}]


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
