"""完整版本树 → 隔离试跑 → 原子发布的核心回归测试。"""
from __future__ import annotations

import csv
import io

from sqlalchemy import event

from app.data_channel.datasets.service import DatasetService
from app.models.ontology import OntologyProject
from app.models.ontology_formal import ObjectInstance, ObjectType, PropertyFact
from app.models.ontology_version import (
    OntologyTrialLink, OntologyTrialObject, OntologyTrialRun, OntologyVersion,
)
from app.models.v2.mapping import OntologyMapping
from app.ontologies.versions.evolution_service import (
    impact_report, validate_release_mapping_contract,
)


class MemoryStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str = "") -> str:
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data
        return uri

    def get_object(self, uri: str) -> bytes:
        return self.objects[uri]

    def delete_object(self, uri: str) -> None:
        self.objects.pop(uri, None)


def _csv(rows: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def _root(client, headers, ontology_id: str) -> dict:
    detail = client.get(f"/api/v1/ontologies/{ontology_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    project = detail.json()["data"]
    assert project["version"] == "v0"
    assert project["current_release_version"] == "v0"
    listing = client.get("/api/v1/ontologies?page_size=1000", headers=headers)
    assert listing.status_code == 200, listing.text
    listed = next(item for item in listing.json()["data"]["items"]
                  if item["id"] == ontology_id)
    assert listed["version"] == "v0"
    assert listed["current_release_version"] == "v0"
    response = client.get(
        f"/api/v2/ontologies/{ontology_id}/version-tree", headers=headers)
    assert response.status_code == 200, response.text
    tree = response.json()["data"]
    root = next(item for item in tree["versions"] if item["version_number"] == "v0")
    assert root["node_kind"] == "release"
    assert tree["current_release_id"] == root["id"]
    assert project["current_release_id"] == root["id"]
    assert listed["current_release_id"] == root["id"]
    return root


def test_current_release_read_model_ignores_mutable_runtime_drift(
        client, auth_headers, ontology, db):
    """Detail structure/mapping reads are pinned to current_release_id.

    Legacy mutable projection rows may still exist for compatibility, but they
    must never leak into the management page before a real version promotion.
    """
    ontology_id = ontology["id"]
    root = _root(client, auth_headers, ontology_id)
    db.add_all([
        ObjectType(
            id="runtime-only-type", ontology_id=ontology_id,
            name="RuntimeOnly", display_name="未发布运行表类型",
            properties=[], interfaces=[], position_x=0, position_y=0,
        ),
        OntologyMapping(
            id="runtime-only-mapping", ontology_id=ontology_id,
            curated_dataset_id=None, entity_class="RuntimeOnly",
            target_object_type_id="runtime-only-type",
            field_mapping={"source": "target"}, status="draft",
        ),
        ObjectInstance(
            id="runtime-only-instance", ontology_id=ontology_id,
            object_type_id="runtime-only-type",
            properties={"source": "draft data"}, source="pipeline",
            # No immutable release owner: this simulates the historical leak.
            ontology_release_id=None,
        ),
    ])
    db.commit()

    # Prove the compatibility projections really have drifted.
    live_types = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/object-types",
        headers=auth_headers).json()["data"]
    assert [item["id"] for item in live_types] == ["runtime-only-type"]
    assert db.query(OntologyMapping).filter_by(
        ontology_id=ontology_id).one().id == "runtime-only-mapping"

    official_instances = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/instances"
        f"?expected_release_id={root['id']}",
        headers=auth_headers)
    assert official_instances.status_code == 200, official_instances.text
    assert official_instances.json()["data"] == []
    stale_instances = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/instances"
        "?expected_release_id=stale-release",
        headers=auth_headers)
    assert stale_instances.status_code == 409, stale_instances.text
    assert stale_instances.json()["detail"]["code"] == "release_context_changed"

    structure = client.get(
        f"/api/v2/ontologies/{ontology_id}/current-release/workspace",
        headers=auth_headers)
    assert structure.status_code == 200, structure.text
    payload = structure.json()["data"]
    assert payload["version"] == "v0"
    assert payload["versionId"] == root["id"]
    assert payload["isCurrentRelease"] is True
    assert payload["editable"] is False
    assert payload["objectTypes"] == []
    assert payload["linkTypes"] == []
    assert payload["actions"] == []
    assert payload["functions"] == []

    mappings = client.get(
        f"/api/v2/ontologies/{ontology_id}/current-release/mappings",
        headers=auth_headers)
    assert mappings.status_code == 200, mappings.text
    payload = mappings.json()["data"]
    assert payload["versionId"] == root["id"]
    assert payload["versionNumber"] == "v0"
    assert payload["isCurrentRelease"] is True
    assert payload["editable"] is False
    assert payload["mappings"] == []
    assert payload["linkMappings"] == []


def _draft(client, headers, ontology_id: str, source_id: str) -> dict:
    response = client.post(
        f"/api/v2/ontologies/{ontology_id}/versions/{source_id}/drafts",
        headers=headers, json={"versionLabel": "订单演进"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _workspace(draft: dict) -> dict:
    return {
        "version": draft["version_number"],
        "baseRevision": f"{draft['revision']}:{draft['snapshot_hash']}",
        "objectTypes": [{
            "id": "ot-order", "name": "Order", "displayName": "订单",
            "primaryKey": "p-id", "positionX": 10, "positionY": 20,
            "properties": [
                {"id": "p-id", "name": "id", "displayName": "订单号",
                 "type": "string", "required": True},
                {"id": "p-name", "name": "name", "displayName": "名称",
                 "type": "string", "required": True},
            ],
        }],
        "linkTypes": [], "actions": [], "functions": [],
        # 即使恶意客户端提交实例，草稿工作区也不会接收或污染生产运行数据。
        "instances": [{"id": "should-not-persist", "objectTypeId": "ot-order",
                       "properties": {"id": "bad", "name": "bad"}}],
        "linkInstances": [],
    }


def _configure_draft(client, headers, ontology_id: str, draft: dict) -> dict:
    saved = client.put(
        f"/api/v2/ontologies/{ontology_id}/versions/{draft['id']}/workspace",
        headers=headers, json=_workspace(draft),
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["data"]["revision"]
    mapping = client.put(
        f"/api/v2/ontologies/{ontology_id}/versions/{draft['id']}/workspace/mappings",
        headers=headers, json={
            "baseRevision": revision,
            "mappings": [{
                "id": "mapping-order", "curatedDatasetId": "dataset-orders",
                "entityClass": "Order", "targetObjectTypeId": "ot-order",
                "fieldMapping": {"id": "id", "name": "name", "__primary_key__": "id"},
                "status": "draft", "confidence": 1,
            }],
            "linkMappings": [], "sentinels": [],
        },
    )
    assert mapping.status_code == 200, mapping.text
    tree = client.get(
        f"/api/v2/ontologies/{ontology_id}/version-tree", headers=headers).json()["data"]
    return next(item for item in tree["versions"] if item["id"] == draft["id"])


def _dataset(db, monkeypatch) -> tuple[DatasetService, MemoryStorage]:
    storage = MemoryStorage()
    monkeypatch.setattr(
        "app.data_channel.datasets.service.get_storage_service", lambda: storage)
    service = DatasetService(db, storage=storage)
    dataset = service.create_dataset(
        "订单数据", "structured",
        schema_json={
            "primary_key": "id",
            "columns": [{"name": "id", "type": "string"},
                        {"name": "name", "type": "string"}],
        },
    )
    dataset.id = "dataset-orders"
    db.commit()
    service.create_version(
        dataset.id, _csv([{"id": "O-1", "name": "一号订单"},
                          {"id": "O-2", "name": "二号订单"}]), rowcount=2)
    return service, storage


def _paired_dataset(db, monkeypatch) -> None:
    storage = MemoryStorage()
    monkeypatch.setattr(
        "app.data_channel.datasets.service.get_storage_service", lambda: storage)
    service = DatasetService(db, storage=storage)
    dataset = service.create_dataset(
        "对象配对数据", "structured",
        schema_json={
            "primary_key": "left_id",
            "columns": [{"name": "left_id", "type": "string"},
                        {"name": "right_id", "type": "string"}],
        },
    )
    dataset.id = "dataset-pairs"
    db.commit()
    service.create_version(
        dataset.id,
        _csv([{"left_id": "PAIR-1", "right_id": "PAIR-1"},
              {"left_id": "PAIR-2", "right_id": "PAIR-2"}]),
        rowcount=2,
    )


def test_release_mapping_contract_allows_unmapped_types_but_validates_definitions():
    snapshot = {
        "objectTypes": [
            {
                "id": "ot-order", "name": "Order", "displayName": "订单",
                "primaryKey": "order-id", "properties": [
                    {"id": "order-id", "name": "id", "required": True},
                    {"id": "order-name", "name": "name", "required": True},
                ],
            },
            {
                "id": "ot-customer", "name": "Customer", "displayName": "客户",
                "primaryKey": "customer-id", "properties": [
                    {"id": "customer-id", "name": "id", "required": True},
                ],
            },
        ],
        "linkTypes": [{
            "id": "lt-owner", "name": "owned_by", "displayName": "所属客户",
            "sourceObjectTypeId": "ot-order", "targetObjectTypeId": "ot-customer",
            "cardinality": "many-to-one", "properties": [],
        }],
        "actions": [], "functions": [], "sentinels": [],
        "mappings": [{
            "id": "map-order", "curatedDatasetId": "orders",
            "entityClass": "Order", "targetObjectTypeId": "ot-order",
            "fieldMapping": {"id": "id"}, "status": "draft",
        }],
        "linkMappings": [],
    }

    codes = {item["code"] for item in validate_release_mapping_contract(snapshot)}
    assert "mapping_required_property_missing" in codes
    assert "object_type_mapping_required" not in codes
    assert "link_type_mapping_required" not in codes

    structure_only = dict(snapshot)
    structure_only["mappings"] = []
    structure_only["linkMappings"] = []
    assert validate_release_mapping_contract(structure_only) == []

    snapshot["mappings"][0]["fieldMapping"]["name"] = "name"
    snapshot["mappings"].append({
        "id": "map-customer", "curatedDatasetId": "customers",
        "entityClass": "Customer", "targetObjectTypeId": "ot-customer",
        "fieldMapping": {"id": "id"}, "status": "draft",
    })
    snapshot["linkMappings"].append({
        "id": "link-owner", "linkTypeId": "lt-owner", "relationType": "owned_by",
        "srcDatasetId": "orders", "tgtDatasetId": "customers",
        "srcKey": "customer_id", "tgtKey": "id", "fieldMapping": {},
        "status": "draft",
    })
    assert validate_release_mapping_contract(snapshot) == []


def test_structure_only_trial_and_promotion_publish_zero_instances(
        client, auth_headers, ontology, db):
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    draft = _draft(client, auth_headers, oid, root["id"])
    saved = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace",
        headers=auth_headers, json=_workspace(draft),
    )
    assert saved.status_code == 200, saved.text

    trial = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers, json={},
    )
    assert trial.status_code == 201, trial.text
    run = trial.json()["data"]
    assert run["status"] == "passed"
    assert run["result"]["counts"] == {
        "objects": 0, "links": 0, "facts": 0, "datasets": 0,
    }
    assert {item["code"] for item in run["result"]["warnings"]} == {
        "object_type_unmapped",
    }
    impact = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/impact",
        headers=auth_headers).json()["data"]

    promoted = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/promote",
        headers=auth_headers,
        json={"trialRunId": run["id"], "impactHash": impact["impactHash"]},
    )
    assert promoted.status_code == 201, promoted.text
    release = promoted.json()["data"]
    db.expire_all()
    assert db.query(OntologyProject).filter_by(
        id=oid).one().current_release_id == release["id"]
    assert db.query(ObjectInstance).filter_by(ontology_id=oid).count() == 0
    official = client.get(
        f"/api/v2/formal/ontologies/{oid}/instances",
        headers=auth_headers)
    assert official.status_code == 200, official.text
    assert official.json()["data"] == []


def test_version_tree_uses_complete_snapshots_and_dependency_numbering(
        client, auth_headers, ontology, db):
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    first = _draft(client, auth_headers, oid, root["id"])
    assert first["version_number"] == "v0.1"
    configured = _configure_draft(client, auth_headers, oid, first)

    nested = _draft(client, auth_headers, oid, configured["id"])
    sibling = _draft(client, auth_headers, oid, root["id"])
    assert nested["version_number"] == "v0.1.1"
    assert sibling["version_number"] == "v0.2"

    release_workspace = client.get(
        f"/api/v2/ontologies/{oid}/versions/{root['id']}/workspace",
        headers=auth_headers,
    )
    assert release_workspace.status_code == 200, release_workspace.text
    assert release_workspace.json()["data"]["workspaceMode"] == "release"
    assert release_workspace.json()["data"]["editable"] is False

    draft_workspace = client.get(
        f"/api/v2/ontologies/{oid}/versions/{nested['id']}/workspace",
        headers=auth_headers,
    )
    assert draft_workspace.status_code == 200, draft_workspace.text
    assert draft_workspace.json()["data"]["workspaceMode"] == "draft"
    assert draft_workspace.json()["data"]["editable"] is True

    detail = client.get(
        f"/api/v2/ontologies/{oid}/versions/{nested['id']}",
        headers=auth_headers).json()["data"]
    formal = detail["snapshot"]["formal"]
    assert set(formal) == {
        "objectTypes", "linkTypes", "actions", "functions",
        "sentinels", "mappings", "linkMappings",
    }
    assert formal["objectTypes"][0]["id"] == "ot-order"
    assert formal["mappings"][0]["id"] == "mapping-order"
    assert db.query(ObjectInstance).filter_by(ontology_id=oid).count() == 0


def test_legacy_current_version_is_frozen_as_complete_migration_baseline(
        client, auth_headers, ontology, db):
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    row = db.query(OntologyVersion).filter_by(id=root["id"]).one()
    row.snapshot_formal = None
    row.snapshot_hash = None
    db.commit()

    tree_response = client.get(
        f"/api/v2/ontologies/{oid}/version-tree", headers=auth_headers)
    assert tree_response.status_code == 200, tree_response.text
    db.expire_all()
    migrated = db.query(OntologyVersion).filter_by(id=root["id"]).one()
    assert set(migrated.snapshot_formal) == {
        "objectTypes", "linkTypes", "actions", "functions",
        "sentinels", "mappings", "linkMappings",
    }
    assert len(migrated.snapshot_hash) == 64


def test_trial_uses_real_pinned_data_but_isolates_runtime_and_side_effects(
        client, auth_headers, ontology, db, monkeypatch):
    oid = ontology["id"]
    _dataset(db, monkeypatch)
    draft = _configure_draft(
        client, auth_headers, oid,
        _draft(client, auth_headers, oid, _root(client, auth_headers, oid)["id"]),
    )

    trial = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers, json={},
    )
    assert trial.status_code == 201, trial.text
    run = trial.json()["data"]
    assert run["status"] == "passed", run
    assert run["result"]["counts"] == {
        "objects": 2, "links": 0, "facts": 4, "datasets": 1}
    assert run["result"]["actionsExecuted"] == 0
    assert run["result"]["sideEffects"] == "blocked"
    assert len(run["dataset_versions"]) == 1
    assert db.query(ObjectInstance).filter_by(ontology_id=oid).count() == 0
    assert db.query(PropertyFact).filter_by(ontology_id=oid).count() == 0
    assert db.query(OntologyTrialObject).filter_by(trial_run_id=run["id"]).count() == 2


def test_trial_keeps_same_dataset_mappings_separate_by_endpoint_type(
        client, auth_headers, ontology, db, monkeypatch):
    """同一资产映射多个对象类型时，关系端点不能被最后一个映射覆盖。"""
    oid = ontology["id"]
    _paired_dataset(db, monkeypatch)
    draft = _draft(client, auth_headers, oid, _root(
        client, auth_headers, oid)["id"])
    workspace = {
        "baseRevision": f"{draft['revision']}:{draft['snapshot_hash']}",
        "version": draft["version_number"],
        "objectTypes": [
            {"id": "ot-left", "name": "Left", "displayName": "左对象",
             "primaryKey": "left-id", "properties": [
                 {"id": "left-id", "name": "left_id", "displayName": "左ID",
                  "type": "string", "required": True}]},
            {"id": "ot-right", "name": "Right", "displayName": "右对象",
             "primaryKey": "right-id", "properties": [
                 {"id": "right-id", "name": "right_id", "displayName": "右ID",
                  "type": "string", "required": True}]},
        ],
        "linkTypes": [{
            "id": "lt-pair", "name": "paired_with", "displayName": "配对",
            "sourceObjectTypeId": "ot-left", "targetObjectTypeId": "ot-right",
            "cardinality": "one-to-one", "properties": [],
        }],
        "actions": [], "functions": [], "instances": [], "linkInstances": [],
    }
    saved = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace",
        headers=auth_headers, json=workspace)
    assert saved.status_code == 200, saved.text
    revision = saved.json()["data"]["revision"]
    mapped = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace/mappings",
        headers=auth_headers, json={
            "baseRevision": revision,
            "mappings": [
                {"id": "map-left", "curatedDatasetId": "dataset-pairs",
                 "entityClass": "Left", "targetObjectTypeId": "ot-left",
                 "fieldMapping": {"left_id": "left_id", "__primary_key__": "left_id"}},
                {"id": "map-right", "curatedDatasetId": "dataset-pairs",
                 "entityClass": "Right", "targetObjectTypeId": "ot-right",
                 "fieldMapping": {"right_id": "right_id", "__primary_key__": "right_id"}},
            ],
            "linkMappings": [{
                "id": "lm-pairs", "linkTypeId": "lt-pair",
                "relationType": "paired_with", "srcDatasetId": "dataset-pairs",
                "tgtDatasetId": "dataset-pairs", "edgeDatasetId": None,
                "srcKey": "left_id", "tgtKey": "right_id", "fieldMapping": {},
            }],
            "sentinels": [],
        })
    assert mapped.status_code == 200, mapped.text

    trial = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers, json={})
    assert trial.status_code == 201, trial.text
    run = trial.json()["data"]
    assert run["status"] == "passed", run
    assert run["result"]["counts"]["objects"] == 4
    assert run["result"]["counts"]["links"] == 2
    assert db.query(OntologyTrialLink).filter_by(trial_run_id=run["id"]).count() == 2


def test_passed_trial_is_frozen_and_can_only_continue_in_a_new_branch(
        client, auth_headers, ontology, db, monkeypatch):
    oid = ontology["id"]
    _dataset(db, monkeypatch)
    draft = _configure_draft(
        client, auth_headers, oid,
        _draft(client, auth_headers, oid, _root(client, auth_headers, oid)["id"]),
    )
    run = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers, json={}).json()["data"]
    workspace = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace",
        headers=auth_headers).json()["data"]
    assert workspace["workspaceMode"] == "trial"
    assert workspace["editable"] is False
    workspace["baseRevision"] = workspace["revision"]
    workspace["objectTypes"][0]["displayName"] = "订单（已修改）"
    frozen = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace",
        headers=auth_headers, json=workspace)
    assert frozen.status_code == 409, frozen.text
    assert frozen.json()["detail"]["code"] == "trial_snapshot_frozen"

    frozen_mapping = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace/mappings",
        headers=auth_headers, json={"baseRevision": workspace["revision"], "mappings": []})
    assert frozen_mapping.status_code == 409, frozen_mapping.text
    assert frozen_mapping.json()["detail"]["code"] == "trial_snapshot_frozen"

    # 画布坐标属于独立展示元数据：试跑快照冻结后仍可调整，且不会推进
    # revision、改变 snapshot_hash 或污染被试跑验证的正式模型快照。
    frozen_row = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    frozen_revision = frozen_row.revision
    frozen_hash = frozen_row.snapshot_hash
    saved_layout = client.put(
        f"/api/v2/ontologies/{oid}/layout",
        headers=auth_headers,
        json={
            "versionId": draft["id"],
            "positions": {
                "ot-order": {"x": 640, "y": 360},
                "property:ot-order:p-name": {"x": 920, "y": 430},
                "l1:ot-order": {"x": 180, "y": 140},
                "l2:property:ot-order:p-name": {"x": 480, "y": 320},
            },
        },
    )
    assert saved_layout.status_code == 200, saved_layout.text
    assert saved_layout.json()["data"]["positions"]["property:ot-order:p-name"] == {
        "x": 920.0, "y": 430.0,
    }
    assert saved_layout.json()["data"]["positions"]["l1:ot-order"] == {
        "x": 180.0, "y": 140.0,
    }
    assert saved_layout.json()["data"]["positions"]["l2:property:ot-order:p-name"] == {
        "x": 480.0, "y": 320.0,
    }
    moved_workspace = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace",
        headers=auth_headers,
    ).json()["data"]
    assert moved_workspace["objectTypes"][0]["positionX"] == 640
    assert moved_workspace["objectTypes"][0]["positionY"] == 360
    assert moved_workspace["canvasLayout"]["property:ot-order:p-name"] == {
        "x": 920.0, "y": 430.0,
    }
    assert moved_workspace["canvasLayout"]["l1:ot-order"] == {
        "x": 180.0, "y": 140.0,
    }
    assert moved_workspace["canvasLayout"]["l2:property:ot-order:p-name"] == {
        "x": 480.0, "y": 320.0,
    }
    db.expire_all()
    frozen_row = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    assert frozen_row.revision == frozen_revision
    assert frozen_row.snapshot_hash == frozen_hash
    assert frozen_row.snapshot_formal["objectTypes"][0]["positionX"] == 10
    assert frozen_row.snapshot_formal["objectTypes"][0]["positionY"] == 20

    rerun = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers, json={})
    assert rerun.status_code == 409, rerun.text
    assert rerun.json()["detail"]["code"] == "trial_snapshot_frozen"

    db.expire_all()
    assert db.query(OntologyTrialRun).filter_by(id=run["id"]).one().status == "passed"

    next_draft = _draft(client, auth_headers, oid, draft["id"])
    assert next_draft["version_number"] == "v0.1.1"
    next_workspace = client.get(
        f"/api/v2/ontologies/{oid}/versions/{next_draft['id']}/workspace",
        headers=auth_headers).json()["data"]
    assert next_workspace["workspaceMode"] == "draft"
    assert next_workspace["editable"] is True
    assert next_workspace["objectTypes"][0]["positionX"] == 640
    assert next_workspace["objectTypes"][0]["positionY"] == 360
    assert next_workspace["canvasLayout"]["property:ot-order:p-name"] == {
        "x": 920.0, "y": 430.0,
    }
    assert next_workspace["canvasLayout"]["l1:ot-order"] == {
        "x": 180.0, "y": 140.0,
    }
    assert next_workspace["canvasLayout"]["l2:property:ot-order:p-name"] == {
        "x": 480.0, "y": 320.0,
    }
    next_row = db.query(OntologyVersion).filter_by(id=next_draft["id"]).one()
    assert next_row.snapshot_formal["objectTypes"][0]["positionX"] == 10
    assert next_row.snapshot_formal["objectTypes"][0]["positionY"] == 20
    assert next_row.canvas_layout["ot-order"] == {"x": 640.0, "y": 360.0}
    assert next_row.canvas_layout["property:ot-order:p-name"] == {
        "x": 920.0, "y": 430.0,
    }
    assert next_row.canvas_layout["l1:ot-order"] == {"x": 180.0, "y": 140.0}
    assert next_row.canvas_layout["l2:property:ot-order:p-name"] == {
        "x": 480.0, "y": 320.0,
    }


def test_promotion_switches_exact_trial_projection_and_keeps_fact_history(
        client, auth_headers, ontology, db, monkeypatch):
    oid = ontology["id"]
    _dataset(db, monkeypatch)
    root = _root(client, auth_headers, oid)
    draft = _configure_draft(
        client, auth_headers, oid,
        _draft(client, auth_headers, oid, root["id"]),
    )
    run = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers, json={}).json()["data"]
    impact = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/impact",
        headers=auth_headers).json()["data"]
    unsafe_pointer_flushes: list[str] = []

    def capture_release_pointer_order(session, _flush_context, _instances):
        new_release_ids = {
            item.id for item in session.new
            if isinstance(item, OntologyVersion) and item.node_kind == "release"
        }
        for item in session.dirty:
            if (isinstance(item, OntologyProject)
                    and item.current_release_id in new_release_ids):
                unsafe_pointer_flushes.append(item.current_release_id)

    event.listen(db, "before_flush", capture_release_pointer_order)
    try:
        promoted = client.post(
            f"/api/v2/ontologies/{oid}/versions/{draft['id']}/promote",
            headers=auth_headers,
            json={"trialRunId": run["id"], "impactHash": impact["impactHash"]},
        )
    finally:
        event.remove(db, "before_flush", capture_release_pointer_order)
    assert promoted.status_code == 201, promoted.text
    assert unsafe_pointer_flushes == []
    release = promoted.json()["data"]
    assert release["version_number"] == "v1"
    assert release["node_kind"] == "release"
    assert release["promoted_from_id"] == draft["id"]

    db.expire_all()
    project = db.query(OntologyProject).filter_by(id=oid).one()
    assert project.current_release_id == release["id"] and project.version == "v1"
    assert db.query(ObjectInstance).filter_by(ontology_id=oid).count() == 2
    assert {
        item.ontology_release_id
        for item in db.query(ObjectInstance).filter_by(ontology_id=oid).all()
    } == {release["id"]}
    assert db.query(PropertyFact).filter_by(ontology_id=oid).count() == 4
    assert db.query(OntologyVersion).filter_by(id=root["id"]).one().snapshot_formal is not None
    assert db.query(OntologyVersion).filter_by(id=draft["id"]).one().lifecycle_status == "superseded"

    # 当前发布和历史发布同样允许保存布局；运行时 full 视图读取布局覆盖，
    # 发布快照的哈希和内容保持原样。
    release_row = db.query(OntologyVersion).filter_by(id=release["id"]).one()
    release_hash = release_row.snapshot_hash
    moved_release = client.put(
        f"/api/v2/ontologies/{oid}/layout",
        headers=auth_headers,
        json={"positions": {"ot-order": {"x": 720, "y": 420}}},
    )
    assert moved_release.status_code == 200, moved_release.text
    assert moved_release.json()["data"]["versionId"] == release["id"]
    runtime = client.get(
        f"/api/v2/formal/ontologies/{oid}/full", headers=auth_headers,
    ).json()["data"]
    assert runtime["objectTypes"][0]["positionX"] == 720
    assert runtime["objectTypes"][0]["positionY"] == 420
    db.expire_all()
    release_row = db.query(OntologyVersion).filter_by(id=release["id"]).one()
    assert release_row.snapshot_hash == release_hash
    assert release_row.snapshot_formal["objectTypes"][0]["positionX"] == 10
    assert release_row.snapshot_formal["objectTypes"][0]["positionY"] == 20

    # 同一完整结构和映射可以继续从 v1 分支、试跑并晋级为 v2；复用稳定
    # 定义 ID 时不能和当前生产投影冲突，也不能重建对象身份。
    second_draft = _draft(client, auth_headers, oid, release["id"])
    assert second_draft["version_number"] == "v1.1"
    second_run = client.post(
        f"/api/v2/ontologies/{oid}/versions/{second_draft['id']}/trial-runs",
        headers=auth_headers, json={}).json()["data"]
    assert second_run["status"] == "passed", second_run
    second_impact = client.get(
        f"/api/v2/ontologies/{oid}/versions/{second_draft['id']}/impact",
        headers=auth_headers).json()["data"]
    second_promoted = client.post(
        f"/api/v2/ontologies/{oid}/versions/{second_draft['id']}/promote",
        headers=auth_headers,
        json={"trialRunId": second_run["id"],
              "impactHash": second_impact["impactHash"]},
    )
    assert second_promoted.status_code == 201, second_promoted.text
    release_v2 = second_promoted.json()["data"]
    assert release_v2["version_number"] == "v2"
    assert release_v2["parent_version_id"] == release["id"]
    db.expire_all()
    project = db.query(OntologyProject).filter_by(id=oid).one()
    assert project.current_release_id == release_v2["id"]
    assert db.query(ObjectInstance).filter_by(ontology_id=oid).count() == 2
    assert {
        item.ontology_release_id
        for item in db.query(ObjectInstance).filter_by(ontology_id=oid).all()
    } == {release_v2["id"]}
    assert db.query(PropertyFact).filter_by(ontology_id=oid).count() == 4


def test_new_lake_version_after_trial_requires_rerun(
        client, auth_headers, ontology, db, monkeypatch):
    oid = ontology["id"]
    service, _ = _dataset(db, monkeypatch)
    draft = _configure_draft(
        client, auth_headers, oid,
        _draft(client, auth_headers, oid, _root(client, auth_headers, oid)["id"]),
    )
    run = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers, json={}).json()["data"]
    impact = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/impact",
        headers=auth_headers).json()["data"]
    service.create_version(
        "dataset-orders", _csv([{"id": "O-3", "name": "三号订单"}]),
        rowcount=1)

    promoted = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/promote",
        headers=auth_headers,
        json={"trialRunId": run["id"], "impactHash": impact["impactHash"]},
    )
    assert promoted.status_code == 422
    codes = {item["code"] for item in promoted.json()["detail"]["errors"]}
    assert "trial_dataset_version_stale" in codes
    db.expire_all()
    assert db.query(OntologyProject).filter_by(id=oid).one().current_release_id == _root(
        client, auth_headers, oid)["id"]
    assert db.query(ObjectInstance).filter_by(ontology_id=oid).count() == 0


def test_impact_report_marks_breaking_property_changes(
        client, auth_headers, ontology, db):
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    first = _configure_draft(
        client, auth_headers, oid, _draft(client, auth_headers, oid, root["id"]))
    # 直接把构造好的候选作为当前发布基线，专测语义影响分析，无需引入数据湖。
    root_row = db.query(OntologyVersion).filter_by(id=root["id"]).one()
    candidate = db.query(OntologyVersion).filter_by(id=first["id"]).one()
    root_row.snapshot_formal = candidate.snapshot_formal
    root_row.snapshot_hash = candidate.snapshot_hash
    db.commit()

    second = _draft(client, auth_headers, oid, root["id"])
    workspace = client.get(
        f"/api/v2/ontologies/{oid}/versions/{second['id']}/workspace",
        headers=auth_headers).json()["data"]
    workspace["baseRevision"] = workspace["revision"]
    workspace["objectTypes"][0]["properties"][1]["type"] = "number"
    assert client.put(
        f"/api/v2/ontologies/{oid}/versions/{second['id']}/workspace",
        headers=auth_headers, json=workspace).status_code == 200
    impact = client.get(
        f"/api/v2/ontologies/{oid}/versions/{second['id']}/impact",
        headers=auth_headers).json()["data"]
    assert impact["breakingCount"] == 1
    assert impact["breaking"][0]["code"] == "property_type_changed"
    assert len(impact["impactHash"]) == 64
