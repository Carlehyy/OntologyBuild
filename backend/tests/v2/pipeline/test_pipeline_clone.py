from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.data_channel.pipelines.models import Pipeline
from app.data_channel.pipelines.router import clone_pipeline
from app.data_channel.steward import service as steward_service
from app.data_channel.steward.models import N8nPipeline
from app.data_channel.steward.service import ensure_shadow_pipeline


def _python_pipeline(**overrides) -> Pipeline:
    pipeline = Pipeline(
        name="订单同步",
        description="每日同步订单数据",
        domain="供应链",
        status="draft",
        definition={
            "engine": "python",
            "nodes": [],
            "edges": [],
            "python": {"script": "result = [{'id': 1}]", "saved_at": "2026-08-01T00:00:00Z"},
        },
        column_definitions=[{
            "source_key": "id",
            "field_key": "id",
            "field_name": "ID",
            "field_type": "integer",
            "is_primary_key": True,
            "nullable": False,
        }],
    )
    for key, value in overrides.items():
        setattr(pipeline, key, value)
    return pipeline


def test_clone_python_pipeline_copies_script_and_contract(db):
    pipeline = _python_pipeline()
    db.add(pipeline)
    db.commit()

    result = clone_pipeline(pipeline.id, db, SimpleNamespace(id="user-1"))

    assert result["name"] == "订单同步_复制"
    assert result["description"] == "每日同步订单数据"
    assert result["domain"] == "供应链"
    assert result["status"] == "draft"
    assert result["enabled"] is False
    assert result["engine"] == "python"
    clone = db.query(Pipeline).filter(Pipeline.id == result["id"]).first()
    assert clone.created_by == "user-1"
    assert clone.definition == pipeline.definition
    assert clone.definition is not pipeline.definition
    assert clone.column_definitions == pipeline.column_definitions


def test_clone_python_pipeline_strips_publish_artifacts(db):
    pipeline = _python_pipeline(
        status="published",
        enabled=True,
        validation_attestation={"execution_hash": "abc", "column_definitions_hash": "def"},
        target_curated_ids=["curated-1"],
    )
    db.add(pipeline)
    db.commit()

    result = clone_pipeline(pipeline.id, db, SimpleNamespace(id="user-1"))

    clone = db.query(Pipeline).filter(Pipeline.id == result["id"]).first()
    assert clone.status == "draft"
    assert clone.enabled is False
    assert clone.validation_attestation is None
    assert clone.target_curated_ids is None
    assert clone.version == 1


def test_clone_name_auto_increments_on_conflict(db):
    pipeline = _python_pipeline()
    db.add(pipeline)
    db.commit()

    first = clone_pipeline(pipeline.id, db, SimpleNamespace(id="user-1"))
    second = clone_pipeline(pipeline.id, db, SimpleNamespace(id="user-1"))

    assert first["name"] == "订单同步_复制"
    assert second["name"] == "订单同步_复制2"


def test_clone_rejects_retired_engine(db):
    pipeline = Pipeline(
        name="画布存量",
        status="draft",
        definition={"engine": "canvas", "nodes": [], "edges": []},
    )
    db.add(pipeline)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        clone_pipeline(pipeline.id, db, SimpleNamespace(id="user-1"))
    assert exc.value.status_code == 400


def test_clone_missing_pipeline_returns_404(db):
    with pytest.raises(HTTPException) as exc:
        clone_pipeline("missing", db, SimpleNamespace(id="user-1"))
    assert exc.value.status_code == 404


class _FakeN8nClient:
    """最小内存版 n8n：只覆盖克隆路径用到的读取与创建。"""

    def __init__(self, workflows: dict):
        self.workflows = {str(key): value for key, value in workflows.items()}
        self.created: list[dict] = []

    @staticmethod
    def sanitize_workflow(payload: dict) -> dict:
        from app.settings.workflows.n8n_client import N8nClient
        return N8nClient.sanitize_workflow(payload)

    def get_workflow(self, workflow_id: str) -> dict:
        return dict(self.workflows[str(workflow_id)])

    def create_workflow(self, payload: dict) -> dict:
        wid = f"wf-clone-{len(self.created) + 1}"
        workflow = {**self.sanitize_workflow(payload), "id": wid, "active": False}
        self.workflows[wid] = workflow
        self.created.append(workflow)
        return dict(workflow)


SOURCE_WORKFLOW = {
    "id": "wf-1",
    "name": "汇率采集",
    "nodes": [
        {"id": "n1", "name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
         "parameters": {"httpMethod": "POST", "path": "ob-origin-0123456789abcdef0123456789abcdef",
                        "responseMode": "lastNode"}},
        {"id": "n2", "name": "输出", "type": "n8n-nodes-base.noOp", "typeVersion": 1, "parameters": {}},
    ],
    "connections": {"Webhook": {"main": [[{"node": "输出", "type": "main", "index": 0}]]}},
    "settings": {},
    "active": True,
}


def test_clone_n8n_pipeline_rebuilds_workflow_and_shadow(db, monkeypatch):
    fake = _FakeN8nClient({"wf-1": SOURCE_WORKFLOW})
    monkeypatch.setattr(steward_service, "get_n8n_client", lambda _db: fake)

    record = N8nPipeline(
        name="汇率采集",
        description="采集每日汇率",
        n8n_workflow_id="wf-1",
        workflow_snapshot=SOURCE_WORKFLOW,
        created_by="owner-1",
    )
    db.add(record)
    db.flush()
    shadow = ensure_shadow_pipeline(db, record)
    shadow.domain = "财务"
    shadow.column_definitions = [{
        "source_key": "currency",
        "field_key": "currency",
        "field_name": "币种",
        "field_type": "string",
        "is_primary_key": True,
        "nullable": False,
    }]
    db.commit()

    result = clone_pipeline(shadow.id, db, SimpleNamespace(id="user-2"))

    # 远端复制：名称更新、webhook path 重新生成、保持未激活
    assert len(fake.created) == 1
    created = fake.created[0]
    assert created["name"] == "汇率采集_复制"
    assert created["active"] is False
    new_path = created["nodes"][0]["parameters"]["path"]
    assert new_path != "ob-origin-0123456789abcdef0123456789abcdef"
    assert new_path.startswith("ob-")
    assert created["nodes"][1] == SOURCE_WORKFLOW["nodes"][1]

    # 影子行：未发布未启用草稿，复制域/描述/字段契约
    assert result["name"] == "汇率采集_复制"
    assert result["description"] == "采集每日汇率"
    assert result["domain"] == "财务"
    assert result["status"] == "draft"
    assert result["enabled"] is False
    assert result["column_definitions"] == shadow.column_definitions
    n8n_definition = result["definition"]["n8n"]
    assert n8n_definition["workflow_id"] == created["id"]
    assert n8n_definition["managed_contract"] is None
    assert n8n_definition["revision"] is None

    clone_record = db.query(N8nPipeline).filter(
        N8nPipeline.id == n8n_definition["steward_id"],
    ).first()
    assert clone_record is not None
    assert clone_record.id != record.id
    assert clone_record.description == "采集每日汇率"
    assert clone_record.status == "draft"


def test_clone_n8n_pipeline_without_governance_record_rejected(db):
    pipeline = Pipeline(
        name="失管 n8n",
        status="draft",
        definition={"engine": "n8n", "n8n": {}},
    )
    db.add(pipeline)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        clone_pipeline(pipeline.id, db, SimpleNamespace(id="user-1"))
    assert exc.value.status_code == 409
