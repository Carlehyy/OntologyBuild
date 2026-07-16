"""数据管家（steward）的治理与集成测试：

  1. 生命周期（发布唯一入口 = 流水线编辑向导的 publish 端点）：
     新建即未发布（不激活）→ publish 激活 + 封版 + 固化 definition →
     已发布永久封版（数据管家修改与旧 unpublish 端点均被拒）
  2. 职权边界（管家只有两项持久写权限）：新建骨架（create_pipeline）+ 编排
     「未发布 且 未启用」的流水线（update_workflow）。已启用即拒编排；
     用户明确要求时允许受控执行预览；纳管 / 归档删除仍不在管家职权内。
  3. 编排工具边界：connections 引用缺失节点报错回给 LLM；节点字段补全
  4. runner 行数据规整：webhook 响应体 / 执行末节点 items → list[dict]
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.data_channel.steward import service
from app.data_channel.steward.models import N8nPipeline
from app.data_channel.steward.runner import (
    _extract_execution_rows,
    collect_n8n_rows,
    normalize_rows,
    trigger_and_collect,
)
from app.data_channel.steward.service import StewardError
from app.data_channel.steward.toolkit import ToolRunner
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineVersion
from app.settings.workflows.n8n_client import N8nClient


def test_steward_intent_router_does_not_default_every_turn_to_overview():
    from app.data_channel.steward.orchestrator import classify_steward_intent

    assert classify_steward_intent("现在有哪些流水线")["code"] == "inventory"
    assert classify_steward_intent("帮我修改订单流水线的整形节点")["code"] == "edit"
    assert classify_steward_intent("为什么昨晚执行失败")["code"] == "diagnose"
    assert classify_steward_intent("帮我执行一下订单流水线")["code"] == "execute"
    assert classify_steward_intent("用表格看看最近输出的前十条")["code"] == "preview"
    assert classify_steward_intent("你好，先聊聊需求")["code"] == "consult"


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
        wf = {
            **self.sanitize_workflow(payload),
            "id": wid,
            "active": False,
            "versionId": f"v-{wid}-1",
            "activeVersionId": None,
            "updatedAt": f"2026-07-11T00:00:0{wid}.000Z",
        }
        self.workflows[wid] = wf
        self.calls.append(f"create:{wid}")
        return dict(wf)

    def update_workflow(self, workflow_id: str, payload: dict) -> dict:
        wf = self.workflows[str(workflow_id)]
        wf.update(self.sanitize_workflow(payload))
        current = int(str(wf["versionId"]).rsplit("-", 1)[-1])
        wf["versionId"] = f"v-{workflow_id}-{current + 1}"
        wf["updatedAt"] = f"2026-07-11T00:01:{current:02d}.000Z"
        self.calls.append(f"update:{workflow_id}")
        return dict(wf)

    def activate_workflow(self, workflow_id: str):
        self.workflows[str(workflow_id)]["active"] = True
        self.workflows[str(workflow_id)]["activeVersionId"] = self.workflows[str(workflow_id)]["versionId"]
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
    def trigger_webhook(self, webhook_path, payload=None, timeout_seconds=None, headers=None):
        output_name = "整理字段"
        for workflow in self.workflows.values():
            try:
                contract = service.validate_managed_workflow_contract(workflow)
            except StewardError:
                continue
            if contract.get("webhook_path") == webhook_path:
                output_name = contract["output_node_name"]
                break
        eid = str(len(self._executions) + 100)
        self._executions = self._executions + [{
            "id": eid, "status": "success", "startedAt": "t1", "stoppedAt": "t2",
            "data": {"resultData": {
                "lastNodeExecuted": output_name,
                "runData": {
                    "Webhook": [{"data": {"main": [[{
                        "json": {"body": dict(payload or {})},
                    }]]}}],
                    output_name: [{"data": {"main": [[
                        {"json": {"currency": "USD", "rate": 1.0}},
                        {"json": {"currency": "CNY", "rate": 7.1}},
                    ]]}}],
                },
            }},
        }]
        self.calls.append(f"webhook:{webhook_path}")
        return 200, [{"currency": "USD", "rate": 1.0}]


WEBHOOK_PATH = "ob-test-0123456789abcdef0123456789abcdef"
WEBHOOK_NODES = [
    {"id": "node-webhook", "name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
     "parameters": {"httpMethod": "POST", "path": WEBHOOK_PATH, "responseMode": "lastNode"}},
    {"id": "node-output", "name": "整理字段", "type": "n8n-nodes-base.set", "typeVersion": 3.4,
     "parameters": {}},
]
WEBHOOK_CONNS = {"Webhook": {"main": [[{"node": "整理字段", "type": "main", "index": 0}]]}}


def _managed_workflow(*, nodes=None, connections=None) -> dict:
    return {
        "nodes": deepcopy(nodes if nodes is not None else WEBHOOK_NODES),
        "connections": deepcopy(connections if connections is not None else WEBHOOK_CONNS),
        "settings": {},
    }


@pytest.fixture
def fake_n8n(monkeypatch):
    fake = FakeN8nClient()
    monkeypatch.setattr(service, "get_n8n_client", lambda _db: fake)
    monkeypatch.setattr("app.data_channel.steward.runner.time.sleep", lambda *_: None)
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
    # 后续用例依赖 WEBHOOK_NODES 这套节点（高熵静态 path、末节点=整理字段）
    updated = runner.run("update_workflow", {
        "record_id": rid, "nodes": WEBHOOK_NODES, "connections": WEBHOOK_CONNS,
    })
    assert "error" not in updated, updated
    return db.query(N8nPipeline).filter(N8nPipeline.id == rid).first()


VALIDATED_COLUMNS = [
    {
        "source_key": "currency",
        "field_key": "currency",
        "field_name": "币种",
        "field_type": "string",
        "is_primary_key": True,
        "nullable": False,
    },
    {
        "source_key": "rate",
        "field_key": "rate",
        "field_name": "汇率",
        "field_type": "float",
        "is_primary_key": False,
        "nullable": False,
    },
]


def _validate_for_publish(client, auth_headers, pipeline_id: str):
    preview = client.post(
        f"/api/v2/pipelines/{pipeline_id}/dry-run", headers=auth_headers)
    if preview.status_code != 200:
        return preview
    dry_run_id = preview.json()["dry_run_id"]
    validation = client.post(
        f"/api/v2/pipelines/{pipeline_id}/validate-definitions",
        params={"dry_run_id": dry_run_id},
        headers=auth_headers,
        json={"column_definitions": VALIDATED_COLUMNS},
    )
    if validation.status_code != 200 or not validation.json().get("valid"):
        return validation
    saved = client.put(
        f"/api/v2/pipelines/{pipeline_id}",
        headers=auth_headers,
        json={"column_definitions": VALIDATED_COLUMNS},
    )
    return saved


def _publish(client, auth_headers, pipeline_id: str, enable: bool = False):
    current = client.get(
        f"/api/v2/pipelines/{pipeline_id}", headers=auth_headers)
    if current.status_code == 200 and current.json().get("status") != "published":
        prepared = _validate_for_publish(client, auth_headers, pipeline_id)
        if prepared.status_code != 200:
            return prepared
    return client.post(f"/api/v2/pipelines/{pipeline_id}/publish",
                       headers=auth_headers, json={"enable": enable})


# ── 生命周期：新建 → 发布 → 封版 → 撤回 ───────────────────────────


def test_n8n_publish_rejects_missing_preview_validation_attestation(
        pipelines_client, client, auth_headers, fake_n8n, draft_record):
    response = client.post(
        f"/api/v2/pipelines/{draft_record.pipeline_id}/publish",
        headers=auth_headers,
        json={"enable": True},
    )

    assert response.status_code == 400
    assert "执行预览" in response.json()["detail"]
    assert not any(call.startswith("activate:") for call in fake_n8n.calls)


def test_canvas_publish_remains_compatible_without_n8n_attestation(db, monkeypatch):
    from app.data_channel.pipelines import router as pipelines_router

    pipeline = Pipeline(
        name="画布兼容发布",
        route="A",
        spec={},
        definition={"engine": "canvas", "nodes": [], "edges": []},
        column_definitions=[],
        status="draft",
        enabled=False,
        version=1,
    )
    db.add(pipeline)
    db.commit()
    monkeypatch.setattr(
        pipelines_router,
        "validate_pipeline",
        lambda *_args, **_kwargs: pipelines_router.ValidateResult(
            valid=True, errors=[], warnings=[]),
    )

    result = pipelines_router.publish_pipeline(
        pipeline.id,
        pipelines_router.PublishBody(enable=False),
        db,
        current_user=SimpleNamespace(id=None),
    )

    assert result["status"] == "published"
    assert db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_id == pipeline.id).count() == 1


def test_validate_definitions_persists_complete_n8n_publish_attestation(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    preview = client.post(
        f"/api/v2/pipelines/{draft_record.pipeline_id}/dry-run",
        headers=auth_headers,
    )
    assert preview.status_code == 200, preview.text
    dry_run_id = preview.json()["dry_run_id"]

    validation = client.post(
        f"/api/v2/pipelines/{draft_record.pipeline_id}/validate-definitions",
        params={"dry_run_id": dry_run_id},
        headers=auth_headers,
        json={"column_definitions": VALIDATED_COLUMNS},
    )
    assert validation.status_code == 200 and validation.json()["valid"] is True, validation.text

    db.refresh(draft_record)
    attestation = service.validation_attestation(draft_record)
    assert attestation is not None
    assert attestation["dry_run_id"] == dry_run_id
    assert len(attestation["column_definitions_hash"]) == 64
    assert len(attestation["workflow_snapshot_hash"]) == 64
    assert len(attestation["output_checksum"]) == 64
    assert attestation["workflow_revision"] == N8nClient.workflow_revision(
        fake_n8n.workflows[draft_record.n8n_workflow_id])

    # 向导校验后再保存同一 canonical 契约，凭证应保留。
    saved = client.put(
        f"/api/v2/pipelines/{draft_record.pipeline_id}",
        headers=auth_headers,
        json={"column_definitions": VALIDATED_COLUMNS},
    )
    assert saved.status_code == 200, saved.text
    db.refresh(draft_record)
    assert service.validation_attestation(draft_record) == attestation

    published = client.post(
        f"/api/v2/pipelines/{draft_record.pipeline_id}/publish",
        headers=auth_headers,
        json={"enable": False},
    )
    assert published.status_code == 200, published.text
    version = db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_id == draft_record.pipeline_id).one()
    assert version.definition["n8n"]["validation_attestation"] == attestation


def test_validate_definitions_recovers_internal_evidence_from_snapshot_hash(
        pipelines_client, client, auth_headers, db, draft_record):
    """旧预览缺 workflow_evidence 时，平台用已核对的 snapshot hash 自动恢复。"""
    preview = client.post(
        f"/api/v2/pipelines/{draft_record.pipeline_id}/dry-run",
        headers=auth_headers,
    )
    assert preview.status_code == 200, preview.text
    dry_run_id = preview.json()["dry_run_id"]

    from app.data_channel.pipelines.router import _DRY_RUN_BUCKET, _dry_run_uri
    from app.services.storage_service import get_storage_service

    storage = get_storage_service()
    payload = json.loads(storage.get_object(
        _dry_run_uri(draft_record.pipeline_id, dry_run_id)).decode("utf-8"))
    engine_meta = payload["engine_meta"]
    assert engine_meta.get("workflow_snapshot_hash")
    engine_meta.pop("workflow_evidence", None)
    storage.put_bytes(
        _DRY_RUN_BUCKET,
        f"dry-runs/{draft_record.pipeline_id}/{dry_run_id}.json",
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )

    validation = client.post(
        f"/api/v2/pipelines/{draft_record.pipeline_id}/validate-definitions",
        params={"dry_run_id": dry_run_id},
        headers=auth_headers,
        json={"column_definitions": VALIDATED_COLUMNS},
    )

    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True
    db.refresh(draft_record)
    assert service.validation_attestation(draft_record) is not None


def test_column_definition_change_invalidates_n8n_publish_attestation(
        pipelines_client, client, auth_headers, db, draft_record):
    assert _validate_for_publish(
        client, auth_headers, draft_record.pipeline_id).status_code == 200
    changed = deepcopy(VALIDATED_COLUMNS)
    changed[1]["field_name"] = "换算汇率"

    saved = client.put(
        f"/api/v2/pipelines/{draft_record.pipeline_id}",
        headers=auth_headers,
        json={"column_definitions": changed},
    )
    assert saved.status_code == 200, saved.text
    db.refresh(draft_record)
    assert service.validation_attestation(draft_record) is None

    published = client.post(
        f"/api/v2/pipelines/{draft_record.pipeline_id}/publish",
        headers=auth_headers,
        json={"enable": False},
    )
    assert published.status_code == 400
    assert "一致性确认" in published.json()["detail"]


@pytest.mark.parametrize("drift_kind", ["revision", "snapshot"])
def test_n8n_workflow_drift_invalidates_attestation_before_activation(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record, drift_kind):
    assert _validate_for_publish(
        client, auth_headers, draft_record.pipeline_id).status_code == 200
    workflow = fake_n8n.workflows[draft_record.n8n_workflow_id]
    if drift_kind == "revision":
        workflow["versionId"] = "edited-after-validation"
        workflow["updatedAt"] = "2026-07-11T09:00:00.000Z"
    else:
        workflow["nodes"][1]["parameters"] = {"assignments": {"changed": True}}
    activations_before = sum(call.startswith("activate:") for call in fake_n8n.calls)

    published = client.post(
        f"/api/v2/pipelines/{draft_record.pipeline_id}/publish",
        headers=auth_headers,
        json={"enable": True},
    )

    assert published.status_code == 400
    assert "漂移" in published.json()["detail"]
    assert sum(call.startswith("activate:") for call in fake_n8n.calls) == activations_before
    db.refresh(draft_record)
    assert service.validation_attestation(draft_record) is None


def test_validate_definitions_rejects_tampered_dry_run_output(
        pipelines_client, client, auth_headers, db, draft_record):
    preview = client.post(
        f"/api/v2/pipelines/{draft_record.pipeline_id}/dry-run",
        headers=auth_headers,
    )
    assert preview.status_code == 200, preview.text
    dry_run_id = preview.json()["dry_run_id"]
    from app.data_channel.pipelines.router import _DRY_RUN_BUCKET, _dry_run_uri
    from app.services.storage_service import get_storage_service

    storage = get_storage_service()
    payload = json.loads(storage.get_object(
        _dry_run_uri(draft_record.pipeline_id, dry_run_id)).decode("utf-8"))
    payload["outputs"][0]["rows"][0]["rate"] = 999
    storage.put_bytes(
        _DRY_RUN_BUCKET,
        f"dry-runs/{draft_record.pipeline_id}/{dry_run_id}.json",
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )

    validation = client.post(
        f"/api/v2/pipelines/{draft_record.pipeline_id}/validate-definitions",
        params={"dry_run_id": dry_run_id},
        headers=auth_headers,
        json={"column_definitions": VALIDATED_COLUMNS},
    )
    assert validation.status_code == 400
    assert "校验和" in validation.json()["detail"]
    db.refresh(draft_record)
    assert service.validation_attestation(draft_record) is None

def test_create_is_inactive_draft(db, fake_n8n, draft_record):
    assert draft_record.status == "draft"
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    # 新建即登记影子流水线（draft）——流水线列表立即可见；发布决定能否被调度
    assert draft_record.pipeline_id is not None
    shadow = db.query(Pipeline).filter(Pipeline.id == draft_record.pipeline_id).first()
    assert shadow is not None
    assert shadow.status == "draft"
    assert (shadow.definition or {}).get("engine") == "n8n"
    # bootstrap 的生产 webhook path 必须包含至少 128 bit 随机后缀。
    path = service.find_webhook_path(fake_n8n.workflows[draft_record.n8n_workflow_id])
    token = str(path).rsplit("-", 1)[-1]
    assert len(token) == 32 and all(ch in "0123456789abcdef" for ch in token.lower())
    # 名称去重
    runner = ToolRunner(db, None, None)
    dup = runner.run("create_pipeline", {"name": "订单同步流水线"})
    assert "error" in dup and "同名" in dup["error"]


def test_bootstrap_webhook_path_has_128_bit_random_suffix(db, fake_n8n):
    rec = service.bootstrap_blank_workflow(db, "高熵路径测试")
    path = service.find_webhook_path(fake_n8n.workflows[rec.n8n_workflow_id])
    token = str(path).rsplit("-", 1)[-1]
    assert len(token) == 32
    assert all(ch in "0123456789abcdef" for ch in token.lower())


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
    assert pl.definition["n8n"]["webhook_path"] == WEBHOOK_PATH
    assert pl.definition["n8n"]["managed_contract"] == {
        "webhook_node_id": "node-webhook",
        "webhook_node_name": "Webhook",
        "webhook_path": WEBHOOK_PATH,
        "output_node_id": "node-output",
        "output_node_name": "整理字段",
    }
    assert pl.definition["n8n"]["revision"] == {
        "versionId": fake_n8n.workflows[draft_record.n8n_workflow_id]["versionId"],
        "activeVersionId": fake_n8n.workflows[draft_record.n8n_workflow_id]["activeVersionId"],
        "updatedAt": fake_n8n.workflows[draft_record.n8n_workflow_id]["updatedAt"],
    }
    # 版本快照与发布同一事务
    snaps = db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_id == pid, PipelineVersion.status == "published").all()
    assert len(snaps) == 1
    assert snaps[0].definition["n8n"]["revision"] == pl.definition["n8n"]["revision"]

    # 重复发布被拒
    r = _publish(client, auth_headers, pid)
    assert r.status_code == 400


def test_publish_without_enable_is_published_but_remote_inactive(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """发布只形成不可变版本；未勾选启用时，平台和 n8n 都必须保持停用。"""
    response = _publish(client, auth_headers, draft_record.pipeline_id, enable=False)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "published"
    assert response.json()["enabled"] is False
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    assert f"activate:{draft_record.n8n_workflow_id}" in fake_n8n.calls
    assert f"deactivate:{draft_record.n8n_workflow_id}" in fake_n8n.calls
    db.expire_all()
    pl = db.query(Pipeline).filter(Pipeline.id == draft_record.pipeline_id).first()
    assert pl.status == "published" and pl.enabled is False


def test_publish_fixes_expected_columns_from_wizard_preview(
        pipelines_client, client, auth_headers, db, monkeypatch, fake_n8n, draft_record):
    """发布固化最近一次执行预览的列集合为期望列契约（运行期漂移检测基线）。

    未发布 n8n 的执行预览走 runner.collect_test_rows（临时激活→触发→还原）+
    persist_test_result，与流水线编辑向导第 2 步同一条通道。数据管家的受控执行
    也复用 collect_test_rows，但不形成发布所需的字段校验凭证。"""
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


def test_preview_deactivation_failure_is_explicit(db, fake_n8n, draft_record, monkeypatch):
    from app.data_channel.steward.runner import collect_test_rows

    monkeypatch.setattr("app.data_channel.steward.runner.time.sleep", lambda *_: None)
    monkeypatch.setattr(
        fake_n8n,
        "deactivate_workflow",
        lambda _workflow_id: (_ for _ in ()).throw(RuntimeError("n8n unavailable")),
    )

    with pytest.raises(service.StewardError) as error:
        collect_test_rows(db, draft_record)

    assert "恢复 n8n 草稿" in str(error.value)
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is True


def test_published_is_sealed_for_steward_edit(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """已发布 = 编排封版：数据管家修改被拒，n8n 侧保持激活。"""
    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid, enable=True).status_code == 200

    runner = ToolRunner(db, None, None)
    out = runner.run("update_workflow", {
        "record_id": draft_record.id,
        "nodes": WEBHOOK_NODES + [{"name": "过滤", "type": "n8n-nodes-base.filter",
                                   "typeVersion": 2.2, "parameters": {}}],
        "connections": {
            **WEBHOOK_CONNS,
            "整理字段": {"main": [[{"node": "过滤", "type": "main", "index": 0}]]},
        },
    })
    assert "error" in out and "新建" in out["error"]
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


def test_published_release_cannot_be_unpublished_or_reopened(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """发布是单向封版：旧端点拒绝且本地/远端状态都不变化。"""
    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid, enable=True).status_code == 200

    r = client.post(f"/api/v2/pipelines/{pid}/unpublish", headers=auth_headers)
    assert r.status_code == 409
    assert "不可变版本" in r.json()["detail"]
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is True
    db.expire_all()
    pl = db.query(Pipeline).filter(Pipeline.id == pid).first()
    assert pl.status == "published" and pl.enabled is True

    # 编排继续受封版保护；名称与描述属于展示信息，发布后仍可维护。
    runner = ToolRunner(db, None, None)
    out = runner.run("update_workflow", {
        "record_id": draft_record.id,
        "nodes": WEBHOOK_NODES + [{"name": "过滤", "type": "n8n-nodes-base.filter",
                                   "typeVersion": 2.2, "parameters": {}}],
        "connections": {
            **WEBHOOK_CONNS,
            "整理字段": {"main": [[{"node": "过滤", "type": "main", "index": 0}]]},
        },
    })
    assert "error" in out and "新建" in out["error"]
    update = client.put(
        f"/api/v2/pipelines/{pid}", headers=auth_headers,
        json={"name": "发布后新名称", "description": "发布后更新的说明"},
    )
    assert update.status_code == 200
    assert update.json()["name"] == "发布后新名称"
    assert update.json()["description"] == "发布后更新的说明"
    db.refresh(draft_record)
    assert draft_record.name == "发布后新名称"
    assert draft_record.description == "发布后更新的说明"
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is True
    assert len(fake_n8n.workflows[draft_record.n8n_workflow_id]["nodes"]) == 2


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


def test_pipeline_validate_uses_full_managed_n8n_contract(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """validate 不能只看有没有 Webhook；危险的方法/响应模式也必须前置报错。"""
    # Fake client intentionally keeps payloads in memory; mutate a deep copy so
    # this negative case cannot contaminate the module-level valid fixture.
    invalid_workflow = deepcopy(fake_n8n.workflows[draft_record.n8n_workflow_id])
    invalid_workflow["nodes"][0]["parameters"]["httpMethod"] = "GET"
    fake_n8n.workflows[draft_record.n8n_workflow_id] = invalid_workflow
    draft_record.workflow_snapshot = N8nClient.sanitize_workflow(
        fake_n8n.workflows[draft_record.n8n_workflow_id])
    db.commit()

    response = client.post(
        f"/api/v2/pipelines/{draft_record.pipeline_id}/validate", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert any("POST" in item["message"] for item in response.json()["errors"])


def test_managed_contract_accepts_one_post_webhook_and_one_sink():
    contract = service.validate_managed_workflow_contract(_managed_workflow())
    assert contract["webhook_path"] == WEBHOOK_PATH
    assert contract["output_node_id"] == "node-output"
    assert contract["output_node_name"] == "整理字段"


def test_managed_contract_requires_exactly_one_enabled_webhook():
    second = deepcopy(WEBHOOK_NODES[0])
    second.update({"id": "node-webhook-2", "name": "Webhook 2"})
    with pytest.raises(StewardError, match="只能有 1 个"):
        service.validate_managed_workflow_contract(
            _managed_workflow(nodes=WEBHOOK_NODES + [second]))

    second["disabled"] = True
    contract = service.validate_managed_workflow_contract(
        _managed_workflow(nodes=WEBHOOK_NODES + [second]))
    assert contract["webhook_node_name"] == "Webhook"


@pytest.mark.parametrize(("field", "value", "message"), [
    ("httpMethod", "GET", "POST"),
    ("responseMode", "onReceived", "lastNode"),
    ("path", "ob-low-entropy", "128 bit"),
    ("path", "ob/:tenant/0123456789abcdef0123456789abcdef", "静态安全路径"),
    ("authentication", "headerAuth", "authentication"),
])
def test_managed_contract_rejects_unsupported_webhook_parameters(field, value, message):
    workflow = _managed_workflow()
    workflow["nodes"][0]["parameters"][field] = value
    with pytest.raises(StewardError, match=message):
        service.validate_managed_workflow_contract(workflow)


@pytest.mark.parametrize("trigger_type", [
    "n8n-nodes-base.scheduleTrigger",
    "n8n-nodes-base.cron",
    "n8n-nodes-base.manualTrigger",
])
def test_managed_contract_rejects_webhook_plus_enabled_trigger(trigger_type):
    trigger = {
        "id": f"node-{trigger_type.rsplit('.', 1)[-1]}",
        "name": "Extra Trigger",
        "type": trigger_type,
        "parameters": {},
    }
    with pytest.raises(StewardError, match="禁止已启用"):
        service.validate_managed_workflow_contract(
            _managed_workflow(nodes=WEBHOOK_NODES + [trigger]))


def test_managed_contract_rejects_extra_root_two_sinks_and_dangling_lane():
    orphan = {
        "id": "node-orphan", "name": "Orphan", "type": "n8n-nodes-base.set",
        "parameters": {},
    }
    with pytest.raises(StewardError, match="额外根节点"):
        service.validate_managed_workflow_contract(
            _managed_workflow(nodes=WEBHOOK_NODES + [orphan]))

    other_sink = {
        "id": "node-other-sink", "name": "另一个输出", "type": "n8n-nodes-base.set",
        "parameters": {},
    }
    two_sink_connections = {
        "Webhook": {"main": [[
            {"node": "整理字段", "type": "main", "index": 0},
            {"node": "另一个输出", "type": "main", "index": 0},
        ]]},
    }
    with pytest.raises(StewardError, match="末端输出节点"):
        service.validate_managed_workflow_contract(_managed_workflow(
            nodes=WEBHOOK_NODES + [other_sink], connections=two_sink_connections))

    dangling = {"Webhook": {"main": [[
        {"node": "整理字段", "type": "main", "index": 0},
    ], []]}}
    with pytest.raises(StewardError, match="悬空分支"):
        service.validate_managed_workflow_contract(
            _managed_workflow(connections=dangling))


def test_managed_contract_allows_branches_that_converge_to_one_sink():
    nodes = [WEBHOOK_NODES[0]] + [
        {"id": "node-if", "name": "判断", "type": "n8n-nodes-base.if", "parameters": {}},
        {"id": "node-a", "name": "分支A", "type": "n8n-nodes-base.set", "parameters": {}},
        {"id": "node-b", "name": "分支B", "type": "n8n-nodes-base.set", "parameters": {}},
        {"id": "node-merge", "name": "汇总", "type": "n8n-nodes-base.merge", "parameters": {}},
        WEBHOOK_NODES[1],
    ]
    connections = {
        "Webhook": {"main": [[{"node": "判断", "type": "main", "index": 0}]]},
        "判断": {"main": [
            [{"node": "分支A", "type": "main", "index": 0}],
            [{"node": "分支B", "type": "main", "index": 0}],
        ]},
        "分支A": {"main": [[{"node": "汇总", "type": "main", "index": 0}]]},
        "分支B": {"main": [[{"node": "汇总", "type": "main", "index": 1}]]},
        "汇总": {"main": [[{"node": "整理字段", "type": "main", "index": 0}]]},
    }
    contract = service.validate_managed_workflow_contract(
        _managed_workflow(nodes=nodes, connections=connections))
    assert contract["output_node_name"] == "整理字段"


@pytest.mark.parametrize("trigger_type", [
    "n8n-nodes-base.scheduleTrigger",
    "n8n-nodes-base.cron",
    "n8n-nodes-base.manualTrigger",
])
def test_publish_requires_platform_webhook_not_other_trigger(
        pipelines_client, client, auth_headers, db, fake_n8n, trigger_type):
    runner = ToolRunner(db, None, None)
    created = runner.run("create_pipeline", {"name": f"非平台触发-{trigger_type}"})
    rid = created["record"]["id"]
    up = runner.run("update_workflow", {
        "record_id": rid,
        "nodes": [{"name": "Trigger", "type": trigger_type, "parameters": {}}],
        "connections": {},
    })
    assert "error" not in up, up
    rec = db.query(N8nPipeline).filter(N8nPipeline.id == rid).first()

    check = runner.run("check_workflow", {"record_id": rid})
    assert check["ok"] is False
    assert any("Webhook" in issue["message"] for issue in check["issues"] if issue["level"] == "error")

    response = _publish(client, auth_headers, rec.pipeline_id)

    assert response.status_code == 400
    assert "Webhook" in response.json()["detail"]
    assert fake_n8n.workflows[rec.n8n_workflow_id]["active"] is False


def test_publish_requires_complete_remote_revision_and_compensates(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    fake_n8n.workflows[draft_record.n8n_workflow_id].pop("updatedAt")

    response = _publish(client, auth_headers, draft_record.pipeline_id)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "一致性确认" in detail
    assert "updatedAt" not in detail  # 底层 revision 字段不暴露给业务用户
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    db.expire_all()
    assert db.query(Pipeline).filter(Pipeline.id == draft_record.pipeline_id).first().status == "draft"


def test_publish_local_commit_failure_compensates_remote_activation(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record, monkeypatch):
    from fastapi import HTTPException
    from app.data_channel.pipelines.router import PublishBody, publish_pipeline

    assert _validate_for_publish(
        client, auth_headers, draft_record.pipeline_id).status_code == 200
    real_commit = db.commit
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(HTTPException) as error:
        publish_pipeline(draft_record.pipeline_id, PublishBody(enable=True), db)
    monkeypatch.setattr(db, "commit", real_commit)

    assert error.value.status_code == 500
    assert "发布前的停用状态" in str(error.value.detail)
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    assert f"deactivate:{draft_record.n8n_workflow_id}" in fake_n8n.calls


def test_unpublish_never_calls_remote_n8n(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record, monkeypatch):
    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid, enable=True).status_code == 200

    monkeypatch.setattr(
        fake_n8n,
        "deactivate_workflow",
        lambda _workflow_id: (_ for _ in ()).throw(RuntimeError("n8n unavailable")),
    )
    response = client.post(f"/api/v2/pipelines/{pid}/unpublish", headers=auth_headers)

    assert response.status_code == 409
    assert "不可变版本" in response.json()["detail"]
    db.expire_all()
    assert db.query(Pipeline).filter(Pipeline.id == pid).first().status == "published"


def test_unpublish_rejection_does_not_depend_on_governance_record(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    pid = draft_record.pipeline_id
    workflow_id = draft_record.n8n_workflow_id
    assert _publish(client, auth_headers, pid, enable=True).status_code == 200
    db.delete(draft_record)
    db.commit()

    response = client.post(
        f"/api/v2/pipelines/{pid}/unpublish", headers=auth_headers)

    assert response.status_code == 409
    assert "不可变版本" in response.json()["detail"]
    db.expire_all()
    assert db.query(Pipeline).filter(Pipeline.id == pid).one().status == "published"
    assert fake_n8n.workflows[workflow_id]["active"] is True


def test_unpublish_rejection_performs_no_database_commit(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record, monkeypatch):
    from fastapi import HTTPException
    from app.data_channel.pipelines.router import unpublish_pipeline

    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid, enable=True).status_code == 200
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db down")))

    with pytest.raises(HTTPException) as error:
        unpublish_pipeline(pid, db)
    assert error.value.status_code == 409
    assert "不可变版本" in str(error.value.detail)
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is True
    db.expire_all()
    assert db.query(Pipeline).filter(Pipeline.id == pid).first().status == "published"


def test_published_enable_switch_drives_and_confirms_n8n(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid, enable=False).status_code == 200
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False

    enabled = client.patch(
        f"/api/v2/pipelines/{pid}/enabled",
        headers=auth_headers,
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is True

    disabled = client.patch(
        f"/api/v2/pipelines/{pid}/enabled",
        headers=auth_headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False


def test_enable_rejects_remote_revision_drift_and_keeps_local_disabled(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid, enable=False).status_code == 200
    fake_n8n.workflows[draft_record.n8n_workflow_id]["versionId"] = "edited-after-publish"

    response = client.patch(
        f"/api/v2/pipelines/{pid}/enabled",
        headers=auth_headers,
        json={"enabled": True},
    )

    assert response.status_code == 400
    assert "版本漂移" in response.json()["detail"]
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    db.expire_all()
    assert db.query(Pipeline).filter(Pipeline.id == pid).first().enabled is False


def test_enable_transport_error_after_remote_change_is_compensated(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record, monkeypatch):
    """即使响应在 n8n 已切换后丢失，也要探测并恢复切换前状态。"""
    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid, enable=False).status_code == 200
    real_activate = fake_n8n.activate_workflow

    def activate_then_timeout(workflow_id):
        real_activate(workflow_id)
        raise RuntimeError("response lost")

    monkeypatch.setattr(fake_n8n, "activate_workflow", activate_then_timeout)
    response = client.patch(
        f"/api/v2/pipelines/{pid}/enabled",
        headers=auth_headers,
        json={"enabled": True},
    )

    assert response.status_code == 400
    assert "response lost" in response.json()["detail"]
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    db.expire_all()
    assert db.query(Pipeline).filter(Pipeline.id == pid).first().enabled is False


def test_disable_remote_failure_and_local_commit_failure_do_not_split_state(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record, monkeypatch):
    from fastapi import HTTPException
    from app.data_channel.pipelines.router import EnabledBody, set_pipeline_enabled

    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid, enable=True).status_code == 200

    real_deactivate = fake_n8n.deactivate_workflow
    monkeypatch.setattr(
        fake_n8n,
        "deactivate_workflow",
        lambda _workflow_id: (_ for _ in ()).throw(RuntimeError("n8n unavailable")),
    )
    response = client.patch(
        f"/api/v2/pipelines/{pid}/enabled",
        headers=auth_headers,
        json={"enabled": False},
    )
    assert response.status_code == 400
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is True
    db.expire_all()
    assert db.query(Pipeline).filter(Pipeline.id == pid).first().enabled is True

    monkeypatch.setattr(fake_n8n, "deactivate_workflow", real_deactivate)
    real_commit = db.commit
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(HTTPException) as error:
        set_pipeline_enabled(pid, EnabledBody(enabled=False), db)
    monkeypatch.setattr(db, "commit", real_commit)

    assert error.value.status_code == 500
    assert "原状态已恢复" in str(error.value.detail)
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is True
    db.expire_all()
    assert db.query(Pipeline).filter(Pipeline.id == pid).first().enabled is True


# ── 影子流水线的画布路径守卫 ──────────────────────────────────────

def test_shadow_pipeline_guards(pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid).status_code == 200

    # 发布后名称/描述仍可维护，并同步回数据管家治理记录。
    r = client.put(
        f"/api/v2/pipelines/{pid}",
        headers=auth_headers,
        json={"name": "改名", "description": "更新后的说明"},
    )
    assert r.status_code == 200
    db.refresh(draft_record)
    assert draft_record.name == "改名"
    assert draft_record.description == "更新后的说明"
    # 编排字段仍归数据管家托管
    r = client.put(f"/api/v2/pipelines/{pid}", headers=auth_headers, json={"definition": {"nodes": []}})
    assert r.status_code == 400
    # 列表里可见（published）
    r = client.get("/api/v2/pipelines", headers=auth_headers)
    assert any(p["id"] == pid for p in r.json())


def test_delete_pipeline_archives_record(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """n8n 删除 = 归档：停用远端并完整保留影子、版本、运行审计。"""
    pid = draft_record.pipeline_id
    rid = draft_record.id
    assert _publish(client, auth_headers, pid, enable=True).status_code == 200
    run = PipelineRun(pipeline_id=pid, status="success")
    db.add(run)
    db.commit()
    version_ids = [row.id for row in db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_id == pid).all()]

    r = client.delete(f"/api/v2/pipelines/{pid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "archived"
    db.expire_all()
    shadow = db.query(Pipeline).filter(Pipeline.id == pid).first()
    assert shadow is not None
    assert shadow.status == "archived" and shadow.enabled is False
    assert [row.id for row in db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_id == pid).all()] == version_ids
    assert db.query(PipelineRun).filter(PipelineRun.id == run.id).first() is not None
    db.refresh(draft_record)
    assert draft_record.status == "archived"
    assert draft_record.pipeline_id == pid
    # n8n 侧：停用但保留
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    # 默认工作列表隐藏；审计查询可显式取回。
    listed = client.get("/api/v2/pipelines", headers=auth_headers).json()
    assert all(item["id"] != pid for item in listed)
    archived = client.get("/api/v2/pipelines?status=archived", headers=auth_headers).json()
    assert any(item["id"] == pid for item in archived)
    # 数据管家面板不再展示
    r = client.get("/api/v2/steward/pipelines", headers=auth_headers)
    assert all(item["id"] != rid for item in r.json()["data"])


def test_archive_remote_failure_keeps_local_pipeline_and_audit_live(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record, monkeypatch):
    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid, enable=True).status_code == 200
    monkeypatch.setattr(
        fake_n8n,
        "deactivate_workflow",
        lambda _workflow_id: (_ for _ in ()).throw(RuntimeError("n8n unavailable")),
    )

    response = client.delete(f"/api/v2/pipelines/{pid}", headers=auth_headers)

    assert response.status_code == 400
    assert "归档" in response.json()["detail"]
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is True
    db.expire_all()
    shadow = db.query(Pipeline).filter(Pipeline.id == pid).first()
    assert shadow.status == "published" and shadow.enabled is True
    assert db.query(PipelineVersion).filter(PipelineVersion.pipeline_id == pid).count() == 1
    db.refresh(draft_record)
    assert draft_record.status != "archived"


def test_archive_local_commit_failure_restores_remote_state(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record, monkeypatch):
    pid = draft_record.pipeline_id
    assert _publish(client, auth_headers, pid, enable=True).status_code == 200
    real_commit = db.commit
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db down")))

    with pytest.raises(StewardError, match="原状态已恢复"):
        service.archive(db, draft_record, fake_n8n)
    monkeypatch.setattr(db, "commit", real_commit)

    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is True
    db.expire_all()
    shadow = db.query(Pipeline).filter(Pipeline.id == pid).first()
    assert shadow.status == "published" and shadow.enabled is True
    db.refresh(draft_record)
    assert draft_record.status != "archived"


def test_panel_lists_only_orchestrable(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """面板只列可编排草稿；发布后永久退出，漂移激活的草稿也隐藏。"""
    rid = draft_record.id
    panel_ids = lambda: [item["id"] for item in
                         client.get("/api/v2/steward/pipelines", headers=auth_headers).json()["data"]]

    # 未发布未启用 → 面板可见
    assert rid in panel_ids()

    # 发布 → 封版 + n8n 激活 → 从面板消失（改到流水线列表管理）
    assert _publish(client, auth_headers, draft_record.pipeline_id, enable=True).status_code == 200
    assert rid not in panel_ids()

    # 发布不可撤回，仍不会重新出现在编排面板。
    assert client.post(f"/api/v2/pipelines/{draft_record.pipeline_id}/unpublish",
                       headers=auth_headers).status_code == 409
    assert rid not in panel_ids()

    # 另一条草稿在 n8n 侧被手动激活，也应从面板隐藏。
    created = ToolRunner(db, None, None).run("create_pipeline", {"name": "漂移激活草稿"})
    drift_id = created["record"]["id"]
    drift = db.query(N8nPipeline).filter(N8nPipeline.id == drift_id).one()
    assert drift_id in panel_ids()
    fake_n8n.activate_workflow(drift.n8n_workflow_id)
    assert drift_id not in panel_ids()


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


# ── 执行预览与只读编排辅助工具 ───────────────────────────────────

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


def test_execute_pipeline_triggers_fresh_draft_run_and_restores_state(
        db, fake_n8n, draft_record):
    """明确执行指令会真实触发 n8n，并展示本次输出，但不发布、不入湖、不遗留激活。"""
    fake_n8n.calls.clear()

    out = ToolRunner(db, None, None).run("execute_pipeline", {
        "record_id": draft_record.id,
        "payload": {"requested_by": "steward-test"},
        "sample_limit": 1,
        "columns": ["currency", "rate"],
    })

    assert "error" not in out, out
    assert out["pipelineStatus"] == "draft" and out["rows"] == 2
    assert out["preview"]["title"] == "本次执行输出"
    assert out["preview"]["columns"] == ["currency", "rate"]
    assert out["preview"]["rows"] == [{"currency": "USD", "rate": 1.0}]
    assert out["preview"]["totalRows"] == 2 and out["preview"]["shownRows"] == 1
    assert out["execution"]["execution_status"] == "success"
    assert fake_n8n.calls == [
        f"activate:{draft_record.n8n_workflow_id}",
        f"webhook:{WEBHOOK_PATH}",
        f"deactivate:{draft_record.n8n_workflow_id}",
    ]
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    db.refresh(draft_record)
    assert service.shadow_status(db, draft_record) == "draft"
    assert not draft_record.last_test_result


def test_execute_pipeline_does_not_require_draft_publish_revision_metadata(
        db, fake_n8n, draft_record, monkeypatch):
    """管家执行与发布凭证解耦：n8n 缺 activeVersionId 仍返回真实输出。"""
    original_activate = fake_n8n.activate_workflow

    def activate_without_version(workflow_id: str):
        original_activate(workflow_id)
        fake_n8n.workflows[str(workflow_id)]["activeVersionId"] = None

    monkeypatch.setattr(fake_n8n, "activate_workflow", activate_without_version)

    out = ToolRunner(db, None, None).run("execute_pipeline", {
        "record_id": draft_record.id,
    })

    assert "error" not in out, out
    assert out["rows"] == 2 and out["execution"]["execution_status"] == "success"
    assert out["preview"]["rows"][0] == {"currency": "USD", "rate": 1.0}
    assert "workflow_snapshot_hash" in out["execution"]
    evidence = out["execution"]["workflow_evidence"]
    assert evidence["revision"]["activeVersionId"] is None
    assert evidence["snapshot_hash"] == out["execution"]["workflow_snapshot_hash"]
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    db.refresh(draft_record)
    assert not draft_record.last_test_result


def test_wizard_preview_builds_internal_evidence_without_active_version_id(
        db, fake_n8n, draft_record, monkeypatch):
    """inactive 草稿缺 activeVersionId 时由平台自动用 revision + snapshot 完成校验。"""
    from app.data_channel.steward.runner import collect_test_rows

    original_activate = fake_n8n.activate_workflow

    def activate_without_version(workflow_id: str):
        original_activate(workflow_id)
        fake_n8n.workflows[str(workflow_id)]["activeVersionId"] = None

    monkeypatch.setattr(fake_n8n, "activate_workflow", activate_without_version)

    rows, exec_meta = collect_test_rows(db, draft_record)

    assert rows and exec_meta["workflow_evidence"]["snapshot_hash"]
    revision = exec_meta["workflow_evidence"]["revision"]
    assert revision["versionId"] and revision["updatedAt"]
    assert revision["activeVersionId"] is None
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False


def test_wizard_validates_and_publishes_when_active_version_id_is_absent(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record,
        monkeypatch):
    """n8n API 不暴露 activeVersionId 时，完整向导仍由平台自动完成并安全发布。"""
    original_activate = fake_n8n.activate_workflow

    def activate_without_version(workflow_id: str):
        original_activate(workflow_id)
        fake_n8n.workflows[str(workflow_id)]["activeVersionId"] = None

    monkeypatch.setattr(fake_n8n, "activate_workflow", activate_without_version)

    response = _publish(
        client, auth_headers, draft_record.pipeline_id, enable=False)

    assert response.status_code == 200, response.text
    db.expire_all()
    pipeline = db.query(Pipeline).filter(
        Pipeline.id == draft_record.pipeline_id).one()
    assert pipeline.status == "published"
    revision = pipeline.definition["n8n"]["revision"]
    assert revision["versionId"] and revision["updatedAt"]
    assert revision["activeVersionId"] is None
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False


def test_execute_pipeline_validates_published_release_and_restores_disabled_state(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """已发布但停用的流水线也可隔离执行；revision 校验仍生效，执行后恢复停用。"""
    published = _publish(client, auth_headers, draft_record.pipeline_id, enable=False)
    assert published.status_code == 200, published.text
    fake_n8n.calls.clear()

    out = ToolRunner(db, None, None).run("execute_pipeline", {
        "record_id": draft_record.id,
    })

    assert "error" not in out, out
    assert out["pipelineStatus"] == "published" and out["rows"] == 2
    assert fake_n8n.calls == [
        f"activate:{draft_record.n8n_workflow_id}",
        f"webhook:{WEBHOOK_PATH}",
        f"deactivate:{draft_record.n8n_workflow_id}",
    ]
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False

    fake_n8n.workflows[draft_record.n8n_workflow_id]["versionId"] = "drifted-version"
    rejected = ToolRunner(db, None, None).run("execute_pipeline", {
        "record_id": draft_record.id,
    })
    assert "版本漂移" in rejected["error"]


def test_inspect_runs_reads_latest_execution(db, fake_n8n, draft_record):
    """只读诊断：无记录给提示；有记录展开最近一次的报错/节点行数/末节点样例。"""
    runner = ToolRunner(db, None, None)
    empty = runner.run("inspect_runs", {"record_id": draft_record.id})
    assert empty["executions"] == [] and "execute_pipeline" in empty["note"]

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
    assert out["preview"] == {
        "title": "最近一次执行输出",
        "columns": ["a"],
        "rows": [{"a": 1}, {"a": 2}],
        "totalRows": 2,
        "shownRows": 2,
        "totalColumns": 1,
        "omittedColumns": 0,
        "missingColumns": [],
        "redactedColumns": [],
        "truncated": False,
        "node": "整理字段",
        "executionId": "e1",
    }


def test_inspect_runs_table_preview_limits_columns_and_redacts_secrets(db, fake_n8n, draft_record):
    """在线预览按用户要求裁行/选列，并在送到浏览器和 LLM 前隐藏凭据形字段。"""
    fake_n8n._executions = [{
        "id": "e-preview", "status": "success", "startedAt": "t1", "stoppedAt": "t2",
        "data": {"resultData": {
            "lastNodeExecuted": "输出",
            "runData": {"输出": [{"data": {"main": [[
                {"json": {"title": "第一条", "score": 99, "access_token": "raw-secret",
                          "metadata": {"cookie": "session-secret", "source": "api"}}},
                {"json": {"title": "第二条", "score": 88, "access_token": "raw-secret-2"}},
                {"json": {"title": "第三条", "score": 77, "access_token": "raw-secret-3"}},
            ]]}}]},
        }},
    }]

    out = ToolRunner(db, None, None).run("inspect_runs", {
        "record_id": draft_record.id,
        "sample_limit": 2,
        "columns": ["title", "access_token", "metadata", "missing"],
    })
    preview = out["preview"]
    assert preview["shownRows"] == 2 and preview["totalRows"] == 3 and preview["truncated"] is True
    assert preview["columns"] == ["title", "access_token", "metadata"]
    assert preview["missingColumns"] == ["missing"]
    assert preview["redactedColumns"] == ["access_token"]
    assert preview["rows"][0]["access_token"] == "[已隐藏]"
    assert "session-secret" not in json.dumps(out, ensure_ascii=False)
    invalid = ToolRunner(db, None, None).run("inspect_runs", {
        "record_id": draft_record.id, "sample_limit": 0,
    })
    assert "sample_limit" in invalid["error"]


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


def test_normalize_rows_rejects_truncation(monkeypatch):
    from app.data_channel.steward import runner as runner_module
    monkeypatch.setattr(runner_module, "_MAX_ROWS", 2)
    with pytest.raises(StewardError, match="不会截断入湖"):
        normalize_rows([{"a": 1}, {"a": 2}, {"a": 3}])


def test_n8n_execution_must_be_unique_and_successful():
    class Client:
        executions = []

        def list_executions(self, **_kwargs):
            return list(self.executions)

        def trigger_webhook(self, *_args, payload=None, **_kwargs):
            self.executions = [{
                "id": "1", "status": "error",
                "data": {"resultData": {
                    "error": {"message": "boom"},
                    "runData": {"Webhook": [{"data": {"main": [[{
                        "json": {"body": {"run_id": payload["run_id"]}},
                    }]]}}]},
                }},
            }]
            return 200, [{"should_not": "be_ingested"}]

        def get_execution(self, *_args, **_kwargs):
            return self.executions[0]

    with pytest.raises(StewardError, match="未成功"):
        trigger_and_collect(
            Client(), "wf", "hook", wait_seconds=1, expected_output_node="Output")


def test_n8n_missing_execution_lineage_is_not_webhook_success():
    class Client:
        def list_executions(self, **_kwargs):
            return []

        def trigger_webhook(self, *_args, **_kwargs):
            return 200, [{"unverified": True}]

    with pytest.raises(StewardError, match="精确关联"):
        trigger_and_collect(
            Client(), "wf", "hook", wait_seconds=0, expected_output_node="Output")


def test_normalize_rows_rejects_oversized_result_without_truncation():
    with pytest.raises(service.StewardError) as error:
        normalize_rows([{"id": i} for i in range(50001)])
    message = str(error.value)
    assert "50000" in message and "50001" in message and "不会截断" in message

    oversized_execution = {
        "id": "too-large",
        "status": "success",
        "data": {"resultData": {
            "lastNodeExecuted": "Output",
            "runData": {"Output": [{"data": {"main": [[
                {"json": {"id": i}} for i in range(50001)
            ]]}}]},
        }},
    }
    with pytest.raises(service.StewardError):
        _extract_execution_rows(oversized_execution, "Output")


def test_published_read_tools_never_overwrite_release_snapshot(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    assert _publish(client, auth_headers, draft_record.pipeline_id, enable=True).status_code == 200
    db.refresh(draft_record)
    frozen = deepcopy(draft_record.workflow_snapshot)
    fake_n8n.workflows[draft_record.n8n_workflow_id]["name"] = "远端被人工改名"

    result = ToolRunner(db, None, None).run("get_workflow", {"record_id": draft_record.id})

    assert result["workflow"]["name"] == "远端被人工改名"
    db.expire_all()
    persisted = db.query(N8nPipeline).filter(N8nPipeline.id == draft_record.id).one()
    assert persisted.workflow_snapshot == frozen


def test_legacy_published_release_derives_unique_output_only_when_snapshot_matches(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    """旧发布记录缺 contract/revision 时仍可安全运行，不要求撤回重发。"""
    assert _publish(client, auth_headers, draft_record.pipeline_id, enable=True).status_code == 200
    pl = db.query(Pipeline).filter(Pipeline.id == draft_record.pipeline_id).one()
    legacy_definition = deepcopy(pl.definition)
    legacy_definition["n8n"].pop("managed_contract", None)
    legacy_definition["n8n"].pop("revision", None)
    pl.definition = legacy_definition
    db.refresh(draft_record)
    legacy_snapshot = deepcopy(draft_record.workflow_snapshot)
    legacy_snapshot["nodes"][0]["parameters"]["path"] = "ob-legacy-a1b2c3"
    draft_record.workflow_snapshot = legacy_snapshot
    legacy_remote = deepcopy(fake_n8n.workflows[draft_record.n8n_workflow_id])
    legacy_remote["nodes"][0]["parameters"]["path"] = "ob-legacy-a1b2c3"
    fake_n8n.workflows[draft_record.n8n_workflow_id] = legacy_remote
    db.commit()

    rows, meta = collect_n8n_rows(db, pl)

    assert rows == [{"currency": "USD", "rate": 1.0}, {"currency": "CNY", "rate": 7.1}]
    assert meta["execution_status"] == "success"

    # 相同的缺失契约一旦伴随拓扑漂移，必须在 webhook 触发前失败。
    drifted_remote = deepcopy(fake_n8n.workflows[draft_record.n8n_workflow_id])
    drifted_remote["nodes"][1]["name"] = "未经审核的输出"
    fake_n8n.workflows[draft_record.n8n_workflow_id] = drifted_remote
    webhook_calls = sum(call.startswith("webhook:") for call in fake_n8n.calls)
    with pytest.raises(service.StewardError, match="当前定义与发布时保留的快照不一致"):
        collect_n8n_rows(db, pl)
    assert sum(call.startswith("webhook:") for call in fake_n8n.calls) == webhook_calls


def test_disabled_published_pipeline_can_preview_and_restores_remote_inactive(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    assert _publish(client, auth_headers, draft_record.pipeline_id, enable=False).status_code == 200
    pl = db.query(Pipeline).filter(Pipeline.id == draft_record.pipeline_id).one()
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False

    rows, _meta = collect_n8n_rows(db, pl)

    assert len(rows) == 2
    assert fake_n8n.workflows[draft_record.n8n_workflow_id]["active"] is False
    calls = fake_n8n.calls
    assert f"activate:{draft_record.n8n_workflow_id}" in calls
    assert calls[-1] == f"deactivate:{draft_record.n8n_workflow_id}"


def test_run_rejects_remote_revision_drift_before_webhook(
        pipelines_client, client, auth_headers, db, fake_n8n, draft_record):
    assert _publish(client, auth_headers, draft_record.pipeline_id).status_code == 200
    fake_n8n.workflows[draft_record.n8n_workflow_id]["versionId"] = "edited-after-publish"
    webhook_calls_before = sum(
        call.startswith("webhook:") for call in fake_n8n.calls)

    pl = db.query(Pipeline).filter(Pipeline.id == draft_record.pipeline_id).first()
    with pytest.raises(service.StewardError) as error:
        collect_n8n_rows(db, pl)

    assert "版本漂移" in str(error.value)
    assert sum(call.startswith("webhook:") for call in fake_n8n.calls) == webhook_calls_before


def test_trigger_and_collect_matches_run_id_not_newest_execution(monkeypatch):
    class ConcurrentClient(FakeN8nClient):
        def trigger_webhook(self, webhook_path, payload=None, timeout_seconds=None):
            def execution(eid, request_id, value):
                return {
                    "id": eid,
                    "status": "success",
                    "data": {"resultData": {
                        "lastNodeExecuted": "Output",
                        "runData": {
                            "Webhook": [{"data": {"main": [[{
                                "json": {"body": {"run_id": request_id}},
                            }]]}}],
                            "Output": [{"data": {"main": [[{"json": {"value": value}}]]}}],
                        },
                    }},
                }

            # Newest belongs to another concurrent request; the second one is ours.
            self._executions = [
                execution("newest-wrong", "other-run", "wrong"),
                execution("matched", payload["run_id"], "right"),
            ]
            return 200, {"ok": True}

    monkeypatch.setattr("app.data_channel.steward.runner.time.sleep", lambda *_: None)
    rows, meta = trigger_and_collect(
        ConcurrentClient(), "wf-1", "hook", payload={"run_id": "wanted"}, wait_seconds=1,
        expected_output_node="Output")

    assert rows == [{"value": "right"}]
    assert meta["execution_id"] == "matched" and meta["run_id"] == "wanted"


def test_trigger_and_collect_fails_when_run_id_cannot_be_verified(monkeypatch):
    class UnmatchedClient(FakeN8nClient):
        def __init__(self):
            super().__init__()
            self.triggered = False

        def list_executions(self, **kwargs):
            if not self.triggered:
                return []
            return [{
                "id": "wrong",
                "status": "success",
                "data": {"resultData": {
                    "lastNodeExecuted": "Output",
                    "runData": {"Output": [{"data": {"main": [[{"json": {"value": 1}}]]}}]},
                }},
            }]

        def trigger_webhook(self, webhook_path, payload=None, timeout_seconds=None):
            self.triggered = True
            return 200, {"ok": True}

    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr("app.data_channel.steward.runner.time.time", lambda: next(ticks))
    monkeypatch.setattr("app.data_channel.steward.runner.time.sleep", lambda *_: None)

    with pytest.raises(service.StewardError) as error:
        trigger_and_collect(
            UnmatchedClient(), "wf-1", "hook", payload={"run_id": "wanted"}, wait_seconds=1,
            expected_output_node="Output")

    message = str(error.value)
    assert "精确关联" in message and "不会采用最新执行" in message


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
    rows, meta = _extract_execution_rows(execution, "整理字段")
    assert rows == [{"a": 1}, {"a": 2}]
    assert meta["execution_id"] == "77" and meta["last_node"] == "整理字段"

    failed = {"id": "78", "status": "error",
              "data": {"resultData": {"error": {"message": "boom", "node": {"name": "HTTP"}}}}}
    rows, meta = _extract_execution_rows(failed, "整理字段")
    assert rows == [] and "boom" in meta["error"] and "HTTP" in meta["error"]


def test_extract_execution_rows_rejects_output_node_drift():
    execution = {
        "id": "wrong-last", "status": "success",
        "data": {"resultData": {
            "lastNodeExecuted": "意外节点",
            "runData": {"意外节点": [{"data": {"main": [[{"json": {"a": 1}}]]}}]},
        }},
    }
    with pytest.raises(StewardError, match="与发布契约输出节点"):
        _extract_execution_rows(execution, "整理字段")


def test_extract_execution_rows_rejects_multiple_runs_and_main_branches():
    run = {"data": {"main": [[{"json": {"a": 1}}]]}}
    multiple_runs = {
        "id": "multi-run", "status": "success",
        "data": {"resultData": {
            "lastNodeExecuted": "输出",
            "runData": {"输出": [deepcopy(run), deepcopy(run)]},
        }},
    }
    with pytest.raises(StewardError, match="实际 2 次"):
        _extract_execution_rows(multiple_runs, "输出")

    multiple_main = {
        "id": "multi-main", "status": "success",
        "data": {"resultData": {
            "lastNodeExecuted": "输出",
            "runData": {"输出": [{"data": {"main": [
                [{"json": {"a": 1}}], [],
            ]}}]},
        }},
    }
    with pytest.raises(StewardError, match="实际 2 个"):
        _extract_execution_rows(multiple_main, "输出")


@pytest.mark.parametrize("run", [
    {},
    {"data": {}},
    {"data": {"main": {"not": "a-list"}}},
    {"data": {"main": ["not-an-item-list"]}},
])
def test_extract_execution_rows_rejects_malformed_output_shape(run):
    execution = {
        "id": "bad-shape", "status": "success",
        "data": {"resultData": {
            "lastNodeExecuted": "输出",
            "runData": {"输出": [run]},
        }},
    }
    with pytest.raises(StewardError):
        _extract_execution_rows(execution, "输出")


def test_n8n_client_webhook_headers_are_optional_and_sanitized(monkeypatch):
    captured = []

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"ok": True}

    class HttpClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, **kwargs):
            captured.append((url, kwargs))
            return Response()

    monkeypatch.setattr("app.settings.workflows.n8n_client.httpx.Client", HttpClient)
    client = N8nClient("https://n8n.example/api/v1", "api-key")

    client.trigger_webhook("safe", {"a": 1})
    assert captured[-1][1]["headers"] is None  # 没有凭据时不伪造 HMAC/signature

    client.trigger_webhook("safe", headers={"X-Ontology-Token": "secret"})
    assert captured[-1][1]["headers"] == {"X-Ontology-Token": "secret"}

    for unsafe in (
        {"Host": "evil.example"},
        {"X-N8N-API-KEY": "leak"},
        {"X-Test": "ok\r\nInjected: yes"},
    ):
        with pytest.raises(ValueError, match="unsafe webhook header"):
            client.trigger_webhook("safe", headers=unsafe)
