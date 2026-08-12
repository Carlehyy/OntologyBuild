"""映射建议服务与端点测试：知识库飞轮 + 规则候选 + LLM 概念化裁决。"""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest

from app.main import app
from app.models.ontology import OntologyProject
from app.models.v2.dataset import Dataset
from app.ontologies.mappings import mapping_knowledge
from app.ontologies.mappings import suggestion_router
from app.ontologies.mappings.models import MappingKnowledgeEntry
from app.ontologies.mappings.suggestion_candidates import (
    normalize_token,
    pick_primary_key_column,
    score_column_to_property,
    score_dataset_to_object,
)
from app.ontologies.mappings.suggestion_service import generate_mapping_suggestions
from app.ontologies.versions.models import OntologyVersion


# ── 构造工具 ─────────────────────────────────────────────────────────────

OBJECT_TYPES = [
    {
        "id": "ot-customer",
        "name": "Customer",
        "displayName": "客户",
        "primaryKey": "customer_id",
        "properties": [
            {"id": "p-id", "name": "customer_id", "displayName": "客户编号", "type": "string"},
            {"id": "p-name", "name": "customer_name", "displayName": "客户名称", "type": "string"},
            {"id": "p-age", "name": "age", "displayName": "年龄", "type": "integer"},
        ],
    },
    {
        "id": "ot-order",
        "name": "Order",
        "displayName": "订单",
        "primaryKey": "order_id",
        "properties": [
            {"id": "p-oid", "name": "order_id", "displayName": "订单编号", "type": "string"},
        ],
    },
]


def _snapshot(mappings=None, object_types=None):
    return {
        "objectTypes": OBJECT_TYPES if object_types is None else object_types,
        "linkTypes": [],
        "actions": [],
        "functions": [],
        "sentinels": [],
        "mappings": mappings or [],
        "linkMappings": [],
    }


def _make_ontology(db, admin_user) -> OntologyProject:
    project = OntologyProject(
        id=f"ont-{uuid.uuid4().hex[:8]}", name="客户本体", domain="test",
        created_by=admin_user.id)
    db.add(project)
    db.commit()
    return project


def _make_draft(db, project, snapshot, *, status="editing", kind="draft",
                number="v0.1") -> OntologyVersion:
    version = OntologyVersion(
        id=f"ver-{uuid.uuid4().hex[:8]}",
        ontology_id=project.id,
        version_number=number,
        node_kind=kind,
        lifecycle_status=status,
        snapshot_formal=snapshot,
        created_by=project.created_by,
    )
    db.add(version)
    db.commit()
    return version


def _make_dataset(db, name="客户表", columns=None, primary_key="cust_id") -> Dataset:
    columns = columns if columns is not None else [
        ("cust_id", "客户编号", "string"),
        ("cust_name", "客户名称", "string"),
        ("age", "年龄", "integer"),
    ]
    dataset = Dataset(
        id=f"ds-{uuid.uuid4().hex[:8]}",
        name=name,
        kind="structured",
        schema_json={
            "types_source": "declared",
            "primary_key": primary_key,
            "columns_typed": [
                {"name": col, "type": col_type, "display_name": display}
                for col, display, col_type in columns
            ],
        },
    )
    db.add(dataset)
    db.commit()
    return dataset


def _no_llm():
    return patch(
        "app.services.model_config_selector.select_llm_model_config",
        return_value=None,
    )


def _with_llm(payload: dict):
    return (
        patch(
            "app.services.model_config_selector.select_llm_model_config",
            return_value=object(),
        ),
        patch(
            "app.services.model_config_selector.llm_call_kwargs",
            return_value={"provider": "compatible", "model": "test-model"},
        ),
        patch(
            "app.services.llm_service._call_llm",
            return_value=json.dumps(payload, ensure_ascii=False),
        ),
    )


# ── 规则候选层 ───────────────────────────────────────────────────────────

def test_normalize_token_handles_pascal_and_separators():
    assert normalize_token("Status") == "status"
    assert normalize_token("customerName") == "customer_name"
    assert normalize_token("Cust-ID") == "cust_id"


def test_score_column_exact_name_and_display_name():
    column = {"name": "customer_name", "display_name": "客户名称", "type": "string"}
    assert score_column_to_property(column, OBJECT_TYPES[0]["properties"][1]) == 1.0
    column_alias = {"name": "cust_name", "display_name": "客户名称", "type": "string"}
    assert score_column_to_property(
        column_alias, OBJECT_TYPES[0]["properties"][1]) == 0.95


def test_score_column_type_incompatible_is_zero():
    column = {"name": "age", "display_name": "年龄", "type": "integer"}
    prop = {"name": "age", "displayName": "年龄", "type": "string"}
    assert score_column_to_property(column, prop) == 0.0


def test_score_dataset_to_object_uses_chinese_display_name():
    assert score_dataset_to_object("客户表", OBJECT_TYPES[0]) >= 0.5
    assert score_dataset_to_object("客户表", OBJECT_TYPES[1]) == 0.0


def test_pick_primary_key_prefers_lake_contract():
    columns = [{"name": "cust_id"}, {"name": "cust_name"}]
    assert pick_primary_key_column(columns, {"primary_key": "cust_id"}) == "cust_id"
    assert pick_primary_key_column(columns, {}) == "cust_id"
    assert pick_primary_key_column([{"name": "code"}], {}) is None


# ── 知识库（数据飞轮）────────────────────────────────────────────────────

def test_harvest_is_idempotent_and_counts_confirmations(db):
    dataset = _make_dataset(db)
    snapshot = _snapshot(mappings=[{
        "id": "map-1",
        "curatedDatasetId": dataset.id,
        "entityClass": "Customer",
        "targetObjectTypeId": "ot-customer",
        "fieldMapping": {
            "cust_id": "customer_id",
            "cust_name": "customer_name",
            "__primary_key__": "cust_id",
        },
    }])
    assert mapping_knowledge.harvest_snapshot_mappings(db, snapshot) == 2
    assert db.query(MappingKnowledgeEntry).count() == 2
    # 幂等：再次回流不产生新行，只累计确认次数
    assert mapping_knowledge.harvest_snapshot_mappings(db, snapshot) == 2
    assert db.query(MappingKnowledgeEntry).count() == 2
    counts = {entry.property_name: entry.confirm_count
              for entry in db.query(MappingKnowledgeEntry).all()}
    assert counts == {"customer_id": 2, "customer_name": 2}


def test_harvest_skips_unknown_columns_and_reserved_keys(db):
    dataset = _make_dataset(db)
    snapshot = _snapshot(mappings=[{
        "curatedDatasetId": dataset.id,
        "entityClass": "Customer",
        "fieldMapping": {"ghost_col": "customer_id", "__primary_key__": "cust_id"},
    }])
    assert mapping_knowledge.harvest_snapshot_mappings(db, snapshot) == 0


def test_lookup_requires_existing_anchor_and_compatible_type(db):
    db.add(MappingKnowledgeEntry(
        column_key="cust_name", display_name="客户名称", col_type="string",
        object_name="Customer", property_name="customer_name", confirm_count=3))
    db.commit()
    index = {("Customer", "customer_name"): "string"}
    hits = mapping_knowledge.lookup(
        db, {"name": "cust_name", "display_name": "客户名称", "type": "string"}, index)
    assert len(hits) == 1
    # 锚定点不在当前本体清单 → 不命中
    assert mapping_knowledge.lookup(
        db, {"name": "cust_name", "display_name": "客户名称", "type": "string"},
        {}) == []
    # 类型不兼容 → 不命中
    assert mapping_knowledge.lookup(
        db, {"name": "cust_name", "display_name": "客户名称", "type": "integer"},
        {("Customer", "customer_name"): "string"}) == []


# ── 建议服务 ─────────────────────────────────────────────────────────────

def test_service_rule_fallback_without_llm_marks_all_unsure(db, admin_user):
    project = _make_ontology(db, admin_user)
    draft = _make_draft(db, project, _snapshot())
    dataset = _make_dataset(db, columns=[
        ("customer_id", "客户编号", "string"),
        ("customer_name", "客户名称", "string"),
    ])
    with _no_llm():
        result = generate_mapping_suggestions(
            db, project.id, draft.id, [dataset.id])
    assert result["llmAvailable"] is False
    suggestion = result["suggestions"][0]
    assert suggestion["error"] is None
    assert suggestion["objectTypeId"] == "ot-customer"
    assert suggestion["pairingVerdict"] == "unsure"
    assert suggestion["primaryKeyColumn"] == "customer_id"
    assert suggestion["fieldMappings"]
    assert {item["verdict"] for item in suggestion["fieldMappings"]} == {"unsure"}
    assert {item["source"] for item in suggestion["fieldMappings"]} == {"rule"}


def test_service_llm_adjudication_with_server_side_validation(db, admin_user):
    project = _make_ontology(db, admin_user)
    draft = _make_draft(db, project, _snapshot())
    dataset = _make_dataset(db, columns=[
        ("cust_id", "客户编号", "string"),
        ("khmc", "客户姓名", "string"),
        ("nl", "岁数", "integer"),
    ])
    payload = {
        "pairing": {"object_type_id": "ot-customer", "verdict": "match",
                    "reason": "列语义指向客户"},
        "column_concepts": [{"column": "khmc", "concept": "客户姓名"}],
        "field_mappings": [
            {"column": "cust_id", "property": "customer_id",
             "verdict": "match", "reason": "编号概念"},
            {"column": "khmc", "property": "customer_name",
             "verdict": "match", "reason": "概念化：客户姓名→客户名称"},
            {"column": "nl", "property": "customer_name",
             "verdict": "match", "reason": "类型不兼容应被丢弃"},
            {"column": "nl", "property": "age",
             "verdict": "match", "reason": "概念化：岁数→年龄"},
            {"column": "khmc", "property": "ghost_prop",
             "verdict": "match", "reason": "幻觉属性应被丢弃"},
        ],
        "primary_key_column": "cust_id",
    }
    select_patch, kwargs_patch, call_patch = _with_llm(payload)
    with select_patch, kwargs_patch, call_patch:
        result = generate_mapping_suggestions(
            db, project.id, draft.id, [dataset.id])
    assert result["llmAvailable"] is True
    suggestion = result["suggestions"][0]
    by_column = {item["column"]: item for item in suggestion["fieldMappings"]}
    assert by_column["cust_id"]["property"] == "customer_id"
    assert by_column["khmc"]["property"] == "customer_name"
    assert by_column["khmc"]["source"] == "llm"
    # 类型不兼容的 nl→customer_name 被丢弃，nl→age 被采纳；幻觉属性被丢弃
    assert by_column["nl"]["property"] == "age"
    assert all(
        item["property"] != "ghost_prop" for item in suggestion["fieldMappings"])


def test_service_llm_garbage_falls_back_to_rules(db, admin_user):
    project = _make_ontology(db, admin_user)
    draft = _make_draft(db, project, _snapshot())
    # cust_name 与 customer_name 只有部分词元重叠（0.6），会进入 LLM 批次；
    # LLM 返回垃圾后应落回规则兜底并标记 unsure。
    dataset = _make_dataset(db, columns=[("cust_name", "客户姓名", "string")])
    select_patch, kwargs_patch, _ = _with_llm({})
    with select_patch, kwargs_patch, patch(
        "app.services.llm_service._call_llm", return_value="不是JSON",
    ):
        result = generate_mapping_suggestions(
            db, project.id, draft.id, [dataset.id])
    suggestion = result["suggestions"][0]
    assert suggestion["error"] is None
    assert suggestion["fieldMappings"]
    assert all(item["verdict"] == "unsure" for item in suggestion["fieldMappings"])
    assert all(item["source"] == "rule" for item in suggestion["fieldMappings"])


def test_service_knowledge_hit_short_circuits_llm(db, admin_user):
    project = _make_ontology(db, admin_user)
    draft = _make_draft(db, project, _snapshot())
    dataset = _make_dataset(db, columns=[("cust_name", "客户名称", "string")])
    db.add(MappingKnowledgeEntry(
        column_key="cust_name", display_name="客户名称", col_type="string",
        object_name="Customer", property_name="customer_name", confirm_count=5))
    db.commit()
    select_patch, kwargs_patch, call_patch = _with_llm({})
    with select_patch, kwargs_patch, call_patch as mock_call:
        result = generate_mapping_suggestions(
            db, project.id, draft.id, [dataset.id])
    suggestion = result["suggestions"][0]
    assert suggestion["fieldMappings"][0]["source"] == "knowledge"
    assert suggestion["fieldMappings"][0]["verdict"] == "match"
    assert suggestion["pairingVerdict"] == "match"
    assert result["knowledgeHits"] == 1
    # 全部列被知识库覆盖且配对确定 → 不消耗 LLM 调用（飞轮降本）
    mock_call.assert_not_called()


def test_service_harvests_existing_draft_mappings_on_suggest(db, admin_user):
    project = _make_ontology(db, admin_user)
    dataset = _make_dataset(db)
    draft = _make_draft(db, project, _snapshot(mappings=[{
        "curatedDatasetId": dataset.id,
        "entityClass": "Customer",
        "targetObjectTypeId": "ot-customer",
        "fieldMapping": {"cust_id": "customer_id", "__primary_key__": "cust_id"},
    }]))
    other = _make_dataset(db, name="订单表", columns=[
        ("order_id", "订单编号", "string")], primary_key="order_id")
    with _no_llm():
        generate_mapping_suggestions(db, project.id, draft.id, [other.id])
    # 飞轮回流：草稿里已保存的映射被沉淀进知识库
    entries = db.query(MappingKnowledgeEntry).all()
    assert [(entry.column_key, entry.property_name) for entry in entries] == [
        ("cust_id", "customer_id")]


def test_service_empty_inventory_rejected(db, admin_user):
    project = _make_ontology(db, admin_user)
    draft = _make_draft(db, project, _snapshot(object_types=[]))
    dataset = _make_dataset(db)
    with _no_llm(), pytest.raises(Exception) as exc_info:
        generate_mapping_suggestions(db, project.id, draft.id, [dataset.id])
    assert getattr(exc_info.value, "status_code", None) == 422


def test_service_unknown_dataset_yields_error_entry(db, admin_user):
    project = _make_ontology(db, admin_user)
    draft = _make_draft(db, project, _snapshot())
    with _no_llm():
        result = generate_mapping_suggestions(db, project.id, draft.id, ["ds-ghost"])
    assert result["suggestions"][0]["error"]


# ── HTTP 端点 ────────────────────────────────────────────────────────────

@pytest.fixture
def suggestion_api(client, db):
    def override_db():
        yield db
    app.dependency_overrides[suggestion_router.get_db] = override_db
    yield client
    app.dependency_overrides.pop(suggestion_router.get_db, None)


def _suggest_url(ontology_id: str, version_id: str) -> str:
    return (f"/api/v2/ontologies/{ontology_id}/versions/{version_id}"
            "/mapping-suggestions")


def test_endpoint_happy_path(suggestion_api, auth_headers, db, admin_user):
    project = _make_ontology(db, admin_user)
    draft = _make_draft(db, project, _snapshot())
    dataset = _make_dataset(db)
    with _no_llm():
        response = suggestion_api.post(
            _suggest_url(project.id, draft.id),
            json={"datasetIds": [dataset.id]},
            headers=auth_headers,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["llmAvailable"] is False
    assert body["suggestions"][0]["datasetId"] == dataset.id
    assert body["suggestions"][0]["error"] is None


def test_endpoint_version_not_found(suggestion_api, auth_headers, db, admin_user):
    project = _make_ontology(db, admin_user)
    response = suggestion_api.post(
        _suggest_url(project.id, "ver-ghost"),
        json={"datasetIds": ["ds-1"]},
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_endpoint_release_version_conflict(suggestion_api, auth_headers, db, admin_user):
    project = _make_ontology(db, admin_user)
    release = _make_draft(
        db, project, _snapshot(), kind="release", status="released", number="v0")
    response = suggestion_api.post(
        _suggest_url(project.id, release.id),
        json={"datasetIds": ["ds-1"]},
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "immutable_release"


def test_endpoint_trial_ready_conflict(suggestion_api, auth_headers, db, admin_user):
    project = _make_ontology(db, admin_user)
    trial = _make_draft(db, project, _snapshot(), status="trial_ready")
    response = suggestion_api.post(
        _suggest_url(project.id, trial.id),
        json={"datasetIds": ["ds-1"]},
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "trial_snapshot_frozen"


def test_endpoint_empty_dataset_ids_rejected(suggestion_api, auth_headers, db, admin_user):
    project = _make_ontology(db, admin_user)
    draft = _make_draft(db, project, _snapshot())
    response = suggestion_api.post(
        _suggest_url(project.id, draft.id),
        json={"datasetIds": []},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_endpoint_viewer_forbidden(suggestion_api, db, admin_user):
    from app.auth.service import hash_password
    from app.models.user import User

    viewer = User(id=str(uuid.uuid4()), username="viewer", email="v@test.com",
                  password_hash=hash_password("viewer123"), role="viewer")
    db.add(viewer)
    db.commit()
    token = suggestion_api.post("/api/v1/auth/login", json={
        "username": "viewer", "password": "viewer123"}).json()["data"]["access_token"]
    project = _make_ontology(db, admin_user)
    draft = _make_draft(db, project, _snapshot())
    response = suggestion_api.post(
        _suggest_url(project.id, draft.id),
        json={"datasetIds": ["ds-1"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
