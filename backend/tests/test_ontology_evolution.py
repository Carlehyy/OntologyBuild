"""完整版本树 → 隔离试跑 → 原子发布的核心回归测试。"""
from __future__ import annotations

import csv
import copy
import io
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import event

from app.data_channel.datasets.service import DatasetService
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.models.relation import Relation
from app.models.ontology_formal import (
    ActionExecutionLog, LinkInstance, LinkType, ObjectInstance, ObjectType,
    PropertyFact,
)
from app.models.ontology_version import (
    OntologyTrialLink, OntologyTrialObject, OntologyTrialRun, OntologyVersion,
)
from app.models.sentinel import Sentinel, SentinelFiring, SentinelMatchState
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.mapping import OntologyMapping
from app.ontologies.versions.evolution_service import (
    _compute_trial_derived, _simulate_sentinels, impact_report, snapshot_hash,
    complete_snapshot, validate_builtin_sentinel_contract,
    validate_manual_mapping_trial_contract,
    validate_release_mapping_contract, validate_snapshot,
    validate_trial_mapping_contract,
)
from app.ontologies.versions.router import (
    _snapshot_sentinel_models, _validate_sentinels,
)
from app.ontologies.versions import router as version_router
from app.ontologies.sentinels.evaluator import RESERVED_SENTINEL_ALIASES
from app.ontologies.mappings.mapping_service import MappingService


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


def _empty_csv(columns: list[str]) -> bytes:
    buffer = io.StringIO()
    csv.DictWriter(buffer, fieldnames=columns).writeheader()
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


def test_legacy_publish_and_unpublish_cannot_bypass_three_state_lifecycle(
        client, auth_headers, ontology, db):
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    before_versions = db.query(OntologyVersion).filter_by(
        ontology_id=oid).count()

    publish = client.post(
        f"/api/v2/ontologies/{oid}/versions",
        headers=auth_headers,
        json={"version_label": "禁止的一键发布"},
    )
    assert publish.status_code == 410, publish.text
    assert publish.json()["detail"] == {
        "code": "legacy_publish_endpoint_retired",
        "message": "一键发布接口已停用；请创建草稿、完成隔离试跑后再调用 promote",
        "currentReleaseId": root["id"],
        "requiredFlow": ["draft", "trial", "promote"],
    }

    unpublish = client.post(
        f"/api/v2/ontologies/{oid}/unpublish", headers=auth_headers)
    assert unpublish.status_code == 410, unpublish.text
    assert unpublish.json()["detail"]["code"] == "unpublish_endpoint_retired"

    db.expire_all()
    project = db.query(OntologyProject).filter_by(id=oid).one()
    assert project.current_release_id == root["id"]
    assert project.version == "v0"
    assert db.query(OntologyVersion).filter_by(
        ontology_id=oid).count() == before_versions


def test_ontology_list_freezes_legacy_project_into_v0_release(
        client, auth_headers, admin_user, db):
    """Every management-list item exposes a current released version.

    Legacy projects created before version trees existed are repaired from
    their complete current formal projection without requiring a version-tree
    page visit first.
    """
    legacy = OntologyProject(
        id="legacy-project-without-release",
        name="旧本体发布基线回填",
        domain="其他",
        version="v0.1",
        status="draft",
        build_mode="manual",
        created_by=admin_user.id,
    )
    db.add(legacy)
    db.flush()
    db.add(ObjectType(
        id="legacy-object-type",
        ontology_id=legacy.id,
        name="LegacyOrder",
        display_name="旧订单",
        properties=[],
        interfaces=[],
        position_x=0,
        position_y=0,
    ))
    db.commit()

    response = client.get(
        "/api/v1/ontologies?page_size=1000", headers=auth_headers)
    assert response.status_code == 200, response.text
    item = next(row for row in response.json()["data"]["items"]
                if row["id"] == legacy.id)
    assert item["current_release_id"]
    assert item["current_release_version"] == "v0"
    assert item["version"] == "v0"
    assert item["entity_count"] == 1

    db.expire_all()
    stored = db.query(OntologyProject).filter_by(id=legacy.id).one()
    release = db.query(OntologyVersion).filter_by(
        id=stored.current_release_id).one()
    assert stored.version == "v0"
    assert release.node_kind == "release"
    assert release.lifecycle_status == "released"
    assert release.base_release_id == release.id
    assert [row["id"] for row in release.snapshot_formal["objectTypes"]] == [
        "legacy-object-type",
    ]


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


def _builtin_sentinel(**overrides) -> dict:
    definition = {
        "id": "sentinel-order-watch",
        "name": "order_watch",
        "displayName": "订单监控",
        "description": "",
        "bindings": [{
            "alias": "order",
            "objectTypeId": "ot-order",
            "filter": None,
        }],
        "links": [],
        "condition": "",
        "conditionRows": [],
        "conditionLogic": "and",
        "primaryAlias": "order",
        "actionIds": [],
        "actionParameters": {},
        "onChange": True,
        "onSchedule": False,
        "scanIntervalSeconds": 300,
        "triggerMode": "on_enter",
        "muted": False,
        "enabled": True,
        "status": "draft",
    }
    definition.update(overrides)
    return definition


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


def test_builtin_sentinel_workspace_rejects_non_strict_runtime_fields(
        client, auth_headers, ontology, db):
    """Raw mapping saves must not defer malformed runtime fields to promotion."""
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    draft = _draft(client, auth_headers, oid, root["id"])
    saved = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace",
        headers=auth_headers,
        json=_workspace(draft),
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["data"]["revision"]
    endpoint = (
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace/mappings"
    )

    invalid_cases = [
        ([_builtin_sentinel(id=" ")], "invalid_sentinel_id"),
        ([
            _builtin_sentinel(),
            _builtin_sentinel(displayName="重复 ID"),
        ], "duplicate_sentinel_id"),
        ([_builtin_sentinel(onChange="false")], "invalid_sentinel_boolean"),
        ([_builtin_sentinel(onSchedule=1)], "invalid_sentinel_boolean"),
        ([_builtin_sentinel(muted=0)], "invalid_sentinel_boolean"),
        ([_builtin_sentinel(enabled=None)], "invalid_sentinel_boolean"),
        ([
            _builtin_sentinel(triggerMode="sometimes"),
        ], "invalid_sentinel_trigger_mode"),
        ([
            _builtin_sentinel(scanIntervalSeconds="300"),
        ], "invalid_sentinel_scan_interval_type"),
        ([
            _builtin_sentinel(scanIntervalSeconds=59),
        ], "invalid_sentinel_scan_interval_range"),
        ([
            _builtin_sentinel(scanIntervalSeconds=86_401),
        ], "invalid_sentinel_scan_interval_range"),
    ]
    for sentinels, expected_code in invalid_cases:
        response = client.put(
            endpoint,
            headers=auth_headers,
            json={"baseRevision": revision, "sentinels": sentinels},
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "publish_validation_failed"
        assert expected_code in {
            item["code"] for item in detail["errors"]
        }, detail

    # The graph editor explicitly exposes this as "仅手动".  Preserve that
    # established built-in contract while still requiring both fields to be
    # genuine booleans.
    manual_only = _builtin_sentinel(onChange=False, onSchedule=False)
    assert validate_builtin_sentinel_contract([manual_only]) == []
    accepted = client.put(
        endpoint,
        headers=auth_headers,
        json={"baseRevision": revision, "sentinels": [manual_only]},
    )
    assert accepted.status_code == 200, accepted.text

    db.expire_all()
    stored = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    assert stored.snapshot_formal["sentinels"][0]["onChange"] is False
    assert stored.snapshot_formal["sentinels"][0]["onSchedule"] is False


def test_builtin_sentinel_id_cannot_collide_with_assistant_overlay(
        client, auth_headers, ontology, db):
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    draft = _draft(client, auth_headers, oid, root["id"])
    saved = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace",
        headers=auth_headers,
        json=_workspace(draft),
    )
    assert saved.status_code == 200, saved.text

    db.add(Sentinel(
        id="sentinel-shared-id",
        ontology_id=oid,
        name="assistant_watch",
        display_name="助手动态哨兵",
        bindings=[{"alias": "order", "objectTypeId": "ot-order"}],
        links=[],
        primary_alias="order",
        action_ids=[],
        action_parameters={},
        enabled=False,
        status="published",
        origin="assistant_dynamic",
    ))
    db.commit()

    response = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace/mappings",
        headers=auth_headers,
        json={
            "baseRevision": saved.json()["data"]["revision"],
            "sentinels": [_builtin_sentinel(id="sentinel-shared-id")],
        },
    )
    assert response.status_code == 422, response.text
    assert "sentinel_id_conflicts_dynamic" in {
        item["code"] for item in response.json()["detail"]["errors"]
    }


def test_legacy_invalid_builtin_sentinel_is_blocked_before_trial_and_promote(
        client, auth_headers, ontology, admin_user, db):
    """Legacy/corrupt snapshots fail with a 422 gate, never promotion_failed."""
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    draft = _draft(client, auth_headers, oid, root["id"])
    saved = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace",
        headers=auth_headers,
        json=_workspace(draft),
    )
    assert saved.status_code == 200, saved.text

    draft_row = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    corrupt = complete_snapshot(draft_row.snapshot_formal)
    corrupt["sentinels"] = [
        _builtin_sentinel(scanIntervalSeconds="not-an-integer"),
    ]
    draft_row.snapshot_formal = corrupt
    draft_row.revision = (draft_row.revision or 0) + 1
    draft_row.snapshot_hash = snapshot_hash(corrupt)
    draft_row.lifecycle_status = "editing"
    db.commit()

    trial = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers,
        json={},
    )
    assert trial.status_code == 422, trial.text
    assert "invalid_sentinel_scan_interval_type" in {
        item["code"] for item in trial.json()["detail"]["errors"]
    }
    assert db.query(OntologyTrialRun).filter_by(
        version_id=draft["id"],
    ).count() == 0

    # Simulate a legacy deployment that already persisted a "passed" trial.
    # The authoritative promote endpoint must repeat the same strict contract
    # before _restore_formal_snapshot reaches bool()/int() coercion.
    current = db.query(OntologyVersion).filter_by(id=root["id"]).one()
    report = impact_report(current.snapshot_formal, corrupt)
    legacy_run = OntologyTrialRun(
        id="legacy-invalid-sentinel-trial",
        ontology_id=oid,
        version_id=draft_row.id,
        revision=draft_row.revision,
        snapshot_hash=draft_row.snapshot_hash,
        status="passed",
        dataset_versions=[],
        result_json={
            "counts": {"objects": 0, "links": 0, "facts": 0, "datasets": 0},
            "errors": [],
        },
        impact_hash=report["impactHash"],
        created_by=admin_user.id,
        completed_at=datetime.now(timezone.utc),
    )
    draft_row.lifecycle_status = "trial_ready"
    db.add(legacy_run)
    db.commit()

    promoted = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/promote",
        headers=auth_headers,
        json={
            "trialRunId": legacy_run.id,
            "impactHash": report["impactHash"],
        },
    )
    assert promoted.status_code == 422, promoted.text
    detail = promoted.json()["detail"]
    assert detail["code"] == "publish_validation_failed"
    assert detail["code"] != "promotion_failed"
    assert "invalid_sentinel_scan_interval_type" in {
        item["code"] for item in detail["errors"]
    }


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


def _paired_dataset(db, monkeypatch) -> DatasetService:
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
    return service


def test_production_manual_mapping_contract_fails_trial_readiness_and_promote(
        client, auth_headers, ontology, db, monkeypatch):
    """The same exact-pin contract must fence all three lifecycle boundaries."""
    oid = ontology["id"]
    _dataset(db, monkeypatch)
    root = _root(client, auth_headers, oid)
    draft = _configure_draft(
        client, auth_headers, oid,
        _draft(client, auth_headers, oid, root["id"]),
    )
    monkeypatch.setattr(version_router.settings, "environment", "production")

    trial_response = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers,
        json={},
    )
    assert trial_response.status_code == 201, trial_response.text
    trial = trial_response.json()["data"]
    assert trial["status"] == "failed"
    trial_error = next(
        item for item in trial["result"]["errors"]
        if item["code"] == "mapping_manual_automation_not_subscribed"
    )
    assert trial_error["field"] == "fieldMapping.__auto_apply_on_version__"
    assert trial_error["datasetId"] == "dataset-orders"
    assert trial_error["datasetRole"] == "object"

    # Simulate a legacy deployment that had already marked this same snapshot
    # as passed without recording its exact dataset pin. Both the read-only
    # readiness preview and authoritative promote path must still fail closed.
    run_row = db.query(OntologyTrialRun).filter_by(id=trial["id"]).one()
    draft_row = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    run_row.status = "passed"
    run_row.dataset_versions = []
    draft_row.lifecycle_status = "trial_ready"
    db.commit()

    impact_response = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/impact",
        headers=auth_headers,
    )
    assert impact_response.status_code == 200, impact_response.text
    impact = impact_response.json()["data"]
    readiness = impact["releaseReadiness"]
    assert readiness["ready"] is False
    readiness_errors = readiness["errors"]
    assert {
        "mapping_manual_automation_not_subscribed",
        "mapping_trial_dataset_pin_missing",
    }.issubset({item["code"] for item in readiness_errors})
    readiness_pin = next(
        item for item in readiness_errors
        if item["code"] == "mapping_trial_dataset_pin_missing"
    )
    assert readiness_pin["field"] == "trial.datasetVersions"
    assert readiness_pin["datasetId"] == "dataset-orders"
    assert readiness_pin["datasetRole"] == "object"

    promoted = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/promote",
        headers=auth_headers,
        json={
            "trialRunId": trial["id"],
            "impactHash": impact["impactHash"],
        },
    )
    assert promoted.status_code == 422, promoted.text
    detail = promoted.json()["detail"]
    assert detail["code"] == "publish_validation_failed"
    promote_errors = detail["errors"]
    assert {
        "mapping_manual_automation_not_subscribed",
        "mapping_trial_dataset_pin_missing",
    }.issubset({item["code"] for item in promote_errors})
    promote_pin = next(
        item for item in promote_errors
        if item["code"] == "mapping_trial_dataset_pin_missing"
    )
    assert promote_pin["field"] == "trial.datasetVersions"
    assert promote_pin["datasetId"] == "dataset-orders"
    assert promote_pin["datasetRole"] == "object"


def test_manual_link_mapping_contract_reports_every_dataset_role(
        db, monkeypatch):
    """Link source, target and edge pins remain independently actionable."""
    _dataset(db, monkeypatch)
    snapshot = complete_snapshot({
        "linkMappings": [{
            "id": "link-order-flow",
            "relationType": "order_flow",
            "srcDatasetId": "dataset-orders",
            "tgtDatasetId": "dataset-orders",
            "edgeDatasetId": "dataset-orders",
            "fieldMapping": {"__auto_apply_on_version__": True},
        }],
    })

    missing_pin_errors = validate_manual_mapping_trial_contract(
        db, snapshot, [],
    )
    assert {
        (item["code"], item["datasetRole"], item["field"])
        for item in missing_pin_errors
    } == {
        (
            "link_mapping_trial_dataset_pin_missing",
            role,
            "trial.datasetVersions",
        )
        for role in ("source", "target", "edge")
    }

    dataset = db.query(Dataset).filter_by(id="dataset-orders").one()
    version = db.query(DatasetVersion).filter_by(
        id=dataset.latest_version_id,
    ).one()
    snapshot["linkMappings"][0]["fieldMapping"] = {}
    subscription_errors = validate_manual_mapping_trial_contract(
        db,
        snapshot,
        [{
            "datasetId": dataset.id,
            "versionId": version.id,
            "checksum": version.checksum,
        }],
    )
    assert {
        (item["code"], item["datasetRole"], item["field"])
        for item in subscription_errors
    } == {
        (
            "link_mapping_manual_automation_not_subscribed",
            role,
            "fieldMapping.__auto_apply_on_version__",
        )
        for role in ("source", "target", "edge")
    }


def test_release_mapping_contract_requires_every_type_and_stored_property():
    snapshot = {
        "objectTypes": [
            {
                "id": "ot-order", "name": "Order", "displayName": "订单",
                "primaryKey": "order-id", "properties": [
                    {"id": "order-id", "name": "id", "required": True},
                    # Optional still means persisted; it must be mapped. Only
                    # computed properties are exempt from the lake contract.
                    {"id": "order-name", "name": "name", "required": False},
                    {"id": "order-label", "name": "label", "required": False,
                     "source": "computed", "computed": True},
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
    assert "mapping_property_missing" in codes
    assert "object_type_mapping_required" in codes
    assert "link_type_mapping_required" in codes
    assert {
        item["code"] for item in validate_trial_mapping_contract(snapshot)
    } == {"trial_object_mapping_required"}
    snapshot["mappings"][0]["fieldMapping"]["name"] = "name"
    assert validate_trial_mapping_contract(snapshot) == []

    structure_only = dict(snapshot)
    structure_only["mappings"] = []
    structure_only["linkMappings"] = []
    structure_codes = {
        item["code"] for item in validate_release_mapping_contract(structure_only)
    }
    assert structure_codes == {
        "object_type_mapping_required", "link_type_mapping_required",
    }
    assert {
        item["code"] for item in validate_trial_mapping_contract(structure_only)
    } == {"trial_object_mapping_required"}

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


def test_mapping_contract_blocks_ambiguous_entity_class_before_reprojection():
    """One legacy Entity.type namespace cannot route to two Formal types."""
    snapshot = {
        "objectTypes": [
            {
                "id": "ot-left", "name": "Left", "displayName": "左",
                "primaryKey": "left-id", "properties": [{
                    "id": "left-id", "name": "left_id", "type": "string",
                    "required": True,
                }],
            },
            {
                "id": "ot-right", "name": "Right", "displayName": "右",
                "primaryKey": "right-id", "properties": [{
                    "id": "right-id", "name": "right_id", "type": "string",
                    "required": True,
                }],
            },
        ],
        "linkTypes": [], "actions": [], "functions": [], "sentinels": [],
        "mappings": [
            {
                "id": "map-left", "curatedDatasetId": "left-dataset",
                "entityClass": "SharedClass",
                "targetObjectTypeId": "ot-left",
                "fieldMapping": {
                    "left_id": "left_id", "__primary_key__": "left_id",
                },
            },
            {
                "id": "map-right", "curatedDatasetId": "right-dataset",
                "entityClass": "SharedClass",
                "targetObjectTypeId": "ot-right",
                "fieldMapping": {
                    "right_id": "right_id", "__primary_key__": "right_id",
                },
            },
        ],
        "linkMappings": [],
    }

    assert {
        item["code"] for item in validate_trial_mapping_contract(snapshot)
    } == {"mapping_entity_class_target_ambiguous"}
    assert "mapping_entity_class_target_ambiguous" in {
        item["code"] for item in validate_release_mapping_contract(snapshot)
    }


def test_builtin_sentinel_trial_resolves_parameters_and_all_link_constraints():
    """The isolated trial must model runtime joins and parameter validation.

    A triangle with one missing edge is not a match even though the first two
    links can independently join.  Once complete, the bound value is validated
    with the production Action contract without executing the action.
    """
    snapshot = {
        "objectTypes": [], "linkTypes": [], "functions": [],
        "actions": [{
            "id": "action-threshold", "name": "set_threshold",
            "displayName": "设置阈值", "objectTypeId": "type-a",
            "parameters": [{
                "name": "threshold", "displayName": "阈值",
                "type": "number", "required": True,
            }],
            "rules": [{
                "id": "notify-threshold", "name": "预警通知",
                "type": "notification", "enabled": True, "order": 0,
                "config": {
                    "channel": "internal",
                    "recipientSource": "constant",
                    "recipient": "ops",
                    "messageTemplate": "threshold={{params.threshold}}",
                },
            }],
        }],
        "mappings": [], "linkMappings": [],
        "sentinels": [{
            "id": "sentinel-triangle", "name": "triangle",
            "displayName": "三角约束", "enabled": True, "muted": False,
            "bindings": [
                {"alias": "a", "objectTypeId": "type-a"},
                {"alias": "b", "objectTypeId": "type-b"},
                {"alias": "c", "objectTypeId": "type-c"},
            ],
            "primaryAlias": "a",
            "links": [
                {"from": "a", "to": "b", "linkTypeId": "link-ab"},
                {"from": "a", "to": "c", "linkTypeId": "link-ac"},
                {"from": "b", "to": "c", "linkTypeId": "link-bc"},
            ],
            "condition": "a.threshold >= 10",
            "actionIds": ["action-threshold"],
            "actionParameters": {
                "action-threshold": {
                    "threshold": {
                        "sourceType": "property",
                        "alias": "a",
                        "property": "threshold",
                    },
                },
            },
        }],
    }
    objects = [
        {"objectId": "a-1", "objectTypeId": "type-a",
         "properties": {"threshold": 12}},
        {"objectId": "b-1", "objectTypeId": "type-b", "properties": {}},
        {"objectId": "c-1", "objectTypeId": "type-c", "properties": {}},
    ]
    links = [
        {"linkId": "ab", "linkTypeId": "link-ab",
         "sourceObjectId": "a-1", "targetObjectId": "b-1"},
        {"linkId": "ac", "linkTypeId": "link-ac",
         "sourceObjectId": "a-1", "targetObjectId": "c-1"},
    ]

    incomplete = _simulate_sentinels(snapshot, objects, links)[0]
    assert incomplete["matched"] == 0
    assert incomplete["plannedActions"] == 0
    assert incomplete["errors"] == []

    links.append({
        "linkId": "bc", "linkTypeId": "link-bc",
        "sourceObjectId": "b-1", "targetObjectId": "c-1",
    })
    complete = _simulate_sentinels(snapshot, objects, links)[0]
    assert complete["matched"] == 1
    assert complete["parameterErrorCount"] == 0
    assert complete["plannedActions"] == 1
    sample = complete["plannedActionSamples"][0]
    assert sample["actionId"] == "action-threshold"
    assert sample["actionName"] == "设置阈值"
    assert sample["edge"] == "enter"
    assert sample["targetInstanceId"] == "a-1"
    assert sample["match"] == {
        "a": "a-1", "b": "b-1", "c": "c-1"}
    assert sample["parameters"] == {"threshold": 12}
    assert sample["status"] == "success"
    assert sample["validationErrors"] == []
    assert sample["effects"][0]["type"] == "notification"
    assert sample["effects"][0]["status"] == "preview"
    assert sample["effects"][0]["committed"] is False
    assert complete["sideEffects"] == "none"

    objects[0]["properties"]["threshold"] = "not-a-number"
    snapshot["sentinels"][0]["condition"] = ""
    invalid = _simulate_sentinels(snapshot, objects, links)[0]
    assert invalid["matched"] == 1
    assert invalid["parameterErrorCount"] == 1
    assert "number" in invalid["errors"][0]


def test_builtin_sentinel_trial_validates_enter_and_leave_event_parameters():
    snapshot = {
        "objectTypes": [], "linkTypes": [], "functions": [],
        "actions": [{
            "id": "action-edge", "name": "record_edge",
            "displayName": "记录边沿", "objectTypeId": "type-order",
            "parameters": [{
                "name": "edge", "type": "string", "required": True,
                "options": ["enter", "leave"],
            }],
            "rules": [{
                "id": "notify-edge", "name": "边沿通知",
                "type": "notification", "enabled": True, "order": 0,
                "config": {
                    "channel": "internal",
                    "recipientSource": "constant",
                    "recipient": "ops",
                    "messageTemplate": "edge={{params.edge}}",
                },
            }],
        }],
        "mappings": [], "linkMappings": [],
        "sentinels": [{
            "id": "sentinel-edge", "name": "edge", "displayName": "边沿哨兵",
            "enabled": True, "muted": False,
            "bindings": [{"alias": "order", "objectTypeId": "type-order"}],
            "primaryAlias": "order", "links": [], "condition": "",
            "triggerMode": "on_enter_leave",
            "actionIds": ["action-edge"],
            "actionParameters": {
                "action-edge": {
                    "edge": {"sourceType": "edge"},
                },
            },
        }],
    }
    objects = [{
        "objectId": "order-1", "objectTypeId": "type-order",
        "properties": {"id": "O-1"},
    }]

    result = _simulate_sentinels(snapshot, objects, [])[0]
    assert result["matched"] == 1
    assert result["plannedActions"] == 2
    assert result["plannedActionSamples"][0]["parameters"] == {"edge": "enter"}
    assert result["plannedActionSamples"][1]["parameters"] == {"edge": "leave"}
    assert all(
        item["status"] == "success"
        for item in result["plannedActionSamples"])
    assert result["errors"] == []

    snapshot["actions"][0]["parameters"][0]["options"] = ["enter"]
    invalid_leave = _simulate_sentinels(snapshot, objects, [])[0]
    assert invalid_leave["plannedActions"] == 2
    assert any("leave 参数" in error and "允许选项" in error
               for error in invalid_leave["errors"])


def test_builtin_sentinel_trial_handles_missing_action_and_real_row_properties():
    snapshot = {
        "objectTypes": [], "linkTypes": [], "actions": [], "functions": [],
        "mappings": [], "linkMappings": [],
        "sentinels": [{
            "id": "sentinel-missing", "name": "missing",
            "displayName": "缺失引用哨兵", "enabled": True, "muted": False,
            "bindings": [{
                "alias": "order", "objectTypeId": "type-order",
                "filter": "order.optional_status != 'closed'",
            }],
            "primaryAlias": "order", "links": [], "condition": "",
            "actionIds": ["action-does-not-exist"],
            "actionParameters": {
                "action-does-not-exist": {"value": "{{order.id}}"},
            },
        }],
    }
    objects = [{
        "objectId": "order-1", "objectTypeId": "type-order",
        "properties": {"id": "O-1"},
    }]

    missing_filter_value = _simulate_sentinels(snapshot, objects, [])[0]
    assert missing_filter_value["matched"] == 0
    assert any("optional_status" in error and "不存在" in error
               for error in missing_filter_value["errors"])

    snapshot["sentinels"][0]["bindings"][0]["filter"] = ""
    snapshot["sentinels"][0]["condition"] = (
        "order.optional_status != 'closed'")
    missing_condition_value = _simulate_sentinels(snapshot, objects, [])[0]
    assert missing_condition_value["matched"] == 0
    assert any("optional_status" in error and "不存在" in error
               for error in missing_condition_value["errors"])

    snapshot["sentinels"][0]["condition"] = ""
    missing_action = _simulate_sentinels(snapshot, objects, [])[0]
    assert missing_action["matched"] == 1
    assert missing_action["plannedActions"] == 1
    assert any("动作不存在: action-does-not-exist" in error
               for error in missing_action["errors"])


def test_builtin_sentinel_static_gate_rejects_schema_typos_and_reserved_aliases():
    object_type = SimpleNamespace(
        id="type-order",
        properties=[
            {"name": "amount"},
            {"name": "risk-score", "source": "computed", "computed": True},
        ],
    )
    action = SimpleNamespace(
        id="action-notify", name="notify", display_name="通知",
        object_type_id="type-order",
        parameters=[{"name": "message", "type": "string", "required": True}],
    )
    sentinel = SimpleNamespace(
        id="sentinel-typo", name="typo", display_name="错别字哨兵",
        bindings=[{
            "alias": "order", "objectTypeId": "type-order",
            "filter": "order.amount > 0 and obj['missing-filter'] == 1",
        }],
        primary_alias="order",
        condition="order['risk-score'] > 10 and order.missing_condition == 1",
        links=[],
        action_ids=["action-notify"],
        action_parameters={
            "action-notify": {
                "message": (
                    "订单 {{order.missing_template}} "
                    "于 {{event.unknownEventField}}"),
            },
        },
    )
    errors = _validate_sentinels(
        [sentinel], [object_type], [], [action])
    codes = [item["code"] for item in errors]
    assert codes.count("sentinel_expression_property_not_found") == 2
    assert "sentinel_parameter_property_not_found" in codes
    assert "sentinel_event_property_not_found" in codes

    sentinel.condition = ""
    sentinel.action_ids = []
    sentinel.action_parameters = {}
    assert {
        "event", "obj", "utils", "sum", "avg", "count", "len",
        "min", "max", "round", "abs", "lower", "upper", "contains",
        "now", "True", "False", "None", "true", "false", "null",
    }.issubset(RESERVED_SENTINEL_ALIASES)
    for reserved_alias in ("event", "obj", "sum", "true"):
        sentinel.bindings = [{
            "alias": reserved_alias, "objectTypeId": "type-order",
        }]
        sentinel.primary_alias = reserved_alias
        reserved = _validate_sentinels([sentinel], [object_type], [], [])
        assert "reserved_sentinel_alias" in {
            item["code"] for item in reserved}


def test_builtin_sentinel_static_gate_rejects_parameter_type_mismatch_without_matches():
    object_type = SimpleNamespace(
        id="type-order",
        properties=[
            {"name": "amount", "type": "number"},
            {"name": "active", "type": "boolean"},
        ],
    )
    action = SimpleNamespace(
        id="action-record", name="record", display_name="记录",
        object_type_id="type-order",
        parameters=[
            {"name": "wrongProperty", "type": "boolean", "required": True},
            {"name": "exactTemplate", "type": "number", "required": True},
            {"name": "partialTemplate", "type": "number", "required": True},
            {"name": "eventValue", "type": "number", "required": True},
        ],
    )
    # No ObjectInstance or matching tuple is supplied to this release gate.
    # Compatibility must be proven from immutable schemas alone.
    sentinel = SimpleNamespace(
        id="sentinel-types", name="types", display_name="类型哨兵",
        bindings=[{"alias": "order", "objectTypeId": "type-order"}],
        primary_alias="order", condition="", links=[],
        action_ids=["action-record"],
        action_parameters={
            "action-record": {
                "wrongProperty": {
                    "sourceType": "property",
                    "alias": "order",
                    "property": "amount",
                },
                "exactTemplate": "{{order.amount}}",
                "partialTemplate": "金额={{order.amount}}",
                "eventValue": {
                    "sourceType": "event",
                    "property": "matchKey",
                },
            },
        },
    )

    errors = _validate_sentinels(
        [sentinel], [object_type], [], [action])
    mismatches = [
        item for item in errors
        if item["code"] == "sentinel_parameter_type_mismatch"
    ]

    assert {
        item["field"] for item in mismatches
    } == {
        "actionParameters.action-record.wrongProperty",
        "actionParameters.action-record.partialTemplate",
        "actionParameters.action-record.eventValue",
    }


def test_builtin_sentinel_gate_rejects_required_action_param_from_optional_property():
    object_type = SimpleNamespace(
        id="type-order",
        properties=[
            {"name": "optional_reason", "type": "string", "required": False},
            {"name": "required_reason", "type": "string", "required": True},
        ],
    )
    action = SimpleNamespace(
        id="action-review", name="review", display_name="复核",
        object_type_id="type-order",
        parameters=[
            {"name": "reason", "type": "string", "required": True},
            {"name": "fallback", "type": "string", "required": True,
             "defaultValue": "system"},
        ],
        rules=[],
    )
    sentinel = SimpleNamespace(
        id="sentinel-review", name="review", display_name="复核哨兵",
        bindings=[{"alias": "order", "objectTypeId": "type-order"}],
        primary_alias="order", condition="", links=[],
        action_ids=["action-review"],
        action_parameters={
            "action-review": {
                "reason": {
                    "sourceType": "property",
                    "alias": "order",
                    "property": "optional_reason",
                },
                "fallback": "{{order.optional_reason}}",
            },
        },
        trigger_mode="on_enter",
    )

    errors = _validate_sentinels(
        [sentinel], [object_type], [], [action])
    optional_supply = [
        item for item in errors
        if item["code"] == "sentinel_required_parameter_optional_property"
    ]
    assert [item["field"] for item in optional_supply] == [
        "actionParameters.action-review.reason",
    ]

    sentinel.action_parameters["action-review"]["reason"] = {
        "sourceType": "property",
        "alias": "order",
        "property": "required_reason",
    }
    assert "sentinel_required_parameter_optional_property" not in {
        item["code"] for item in _validate_sentinels(
            [sentinel], [object_type], [], [action])
    }


def test_builtin_sentinel_gate_rejects_leave_action_needing_live_links():
    order_type = SimpleNamespace(
        id="type-order", properties=[{"name": "email", "type": "string"}])
    user_type = SimpleNamespace(
        id="type-user", properties=[{"name": "email", "type": "string"}])
    link_type = SimpleNamespace(
        id="link-owner",
        source_object_type_id="type-order",
        target_object_type_id="type-user",
    )
    action = SimpleNamespace(
        id="action-notify-owner", name="notify_owner", display_name="通知负责人",
        object_type_id="type-order", parameters=[],
        rules=[{
            "id": "notify", "type": "notification", "enabled": True,
            "config": {
                "channel": "internal",
                "recipientSource": "link",
                "linkTypeId": "link-owner",
                "recipientProperty": "email",
                "message": "订单已离开匹配",
            },
        }],
    )
    sentinel = SimpleNamespace(
        id="sentinel-leave", name="leave", display_name="离开哨兵",
        bindings=[{"alias": "order", "objectTypeId": "type-order"}],
        primary_alias="order", condition="", links=[],
        action_ids=["action-notify-owner"], action_parameters={},
        trigger_mode="on_enter_leave",
    )

    errors = _validate_sentinels(
        [sentinel], [order_type, user_type], [link_type], [action])
    assert "sentinel_leave_action_not_snapshot_safe" in {
        item["code"] for item in errors}

    sentinel.trigger_mode = "on_enter"
    enter_only = _validate_sentinels(
        [sentinel], [order_type, user_type], [link_type], [action])
    assert "sentinel_leave_action_not_snapshot_safe" not in {
        item["code"] for item in enter_only}

    snapshot_model = _snapshot_sentinel_models({
        "sentinels": [{
            "id": "sentinel-leave",
            "name": "leave",
            "triggerMode": "on_enter_leave",
        }],
    })[0]
    assert snapshot_model.trigger_mode == "on_enter_leave"


def test_snapshot_gate_rejects_action_that_cannot_execute():
    snapshot = {
        "objectTypes": [{
            "id": "type-order", "name": "Order", "displayName": "订单",
            "primaryKey": "order-id",
            "properties": [{
                "id": "order-id", "name": "id", "type": "string",
                "required": True,
            }],
        }],
        "linkTypes": [],
        "actions": [{
            "id": "action-no-effect", "name": "no_effect",
            "displayName": "无效果动作", "objectTypeId": "type-order",
            "parameters": [], "rules": [],
        }],
        "functions": [], "instances": [], "linkInstances": [],
        "mappings": [], "linkMappings": [], "sentinels": [],
    }

    errors = validate_snapshot(snapshot)

    assert "invalid_action_definition" in {item["code"] for item in errors}
    assert any("没有启用的可执行副作用规则" in item["message"]
               for item in errors)


def test_snapshot_gate_compiles_expression_functions_without_data_rows():
    snapshot = {
        "objectTypes": [{
            "id": "type-order", "name": "Order", "displayName": "订单",
            "primaryKey": "order-id",
            "properties": [{
                "id": "order-id", "name": "id", "type": "string",
                "required": True,
            }],
        }],
        "linkTypes": [], "actions": [],
        "functions": [
            {
                "id": "fn-unknown", "name": "unknown",
                "displayName": "未知变量函数",
                "functionType": "object", "language": "expression",
                "targetObjectTypeId": "type-order", "parameters": [],
                "returnType": "number",
                "body": "unknown_variable + 1",
                "enabled": True,
            },
            {
                "id": "fn-missing-property", "name": "missing_property",
                "displayName": "缺失属性函数",
                "functionType": "object", "language": "expression",
                "targetObjectTypeId": "type-order", "parameters": [],
                "returnType": "number",
                "body": "object.missing_amount + 1",
                "enabled": True,
            },
        ],
        "instances": [], "linkInstances": [],
        "mappings": [], "linkMappings": [], "sentinels": [],
    }

    errors = validate_snapshot(snapshot)

    function_errors = [
        item for item in errors
        if item["code"] == "invalid_expression_function"
    ]
    assert function_errors
    assert any("unknown_variable" in item["message"]
               for item in function_errors)
    assert any("object.missing_amount" in item["message"]
               for item in function_errors)


def test_builtin_sentinel_trial_fails_closed_at_candidate_cap():
    snapshot = {
        "objectTypes": [], "linkTypes": [], "actions": [], "functions": [],
        "mappings": [], "linkMappings": [],
        "sentinels": [{
            "id": "sentinel-cap", "name": "cap", "displayName": "容量保护",
            "enabled": True, "muted": False,
            "bindings": [{"alias": "row", "objectTypeId": "type-row"}],
            "primaryAlias": "row", "links": [], "condition": "",
            "actionIds": [], "actionParameters": {},
        }],
    }
    objects = [{
        "objectId": f"row-{index}", "objectTypeId": "type-row",
        "properties": {"index": index},
    } for index in range(1001)]

    result = _simulate_sentinels(snapshot, objects, [])[0]

    assert result["candidateCapReached"] is True
    assert result["candidateCount"] == 1000
    assert result["errors"] == [
        "跨对象候选组合超过安全上限 1000，请收窄绑定过滤条件后重试",
    ]


def test_builtin_sentinel_trial_uses_expression_derived_properties():
    snapshot = {
        "objectTypes": [{
            "id": "type-order", "name": "Order", "displayName": "订单",
            "primaryKey": "order-id",
            "properties": [
                {"id": "order-id", "name": "id", "type": "string",
                 "required": True},
                {"id": "order-amount", "name": "amount", "type": "number"},
                {"id": "order-score", "name": "score", "type": "number",
                 "source": "computed", "computed": True,
                 "functionId": "fn-score"},
            ],
        }],
        "linkTypes": [],
        "actions": [{
            "id": "action-score", "name": "record_score",
            "displayName": "记录评分", "objectTypeId": "type-order",
            "parameters": [{
                "name": "score", "type": "number", "required": True,
            }],
            "rules": [{
                "id": "notify-score", "name": "评分通知",
                "type": "notification", "enabled": True, "order": 0,
                "config": {
                    "channel": "internal",
                    "recipientSource": "constant",
                    "recipient": "ops",
                    "messageTemplate": "score={{params.score}}",
                },
            }],
        }],
        "functions": [{
            "id": "fn-score", "name": "score", "displayName": "评分",
            "functionType": "object", "language": "expression",
            "targetObjectTypeId": "type-order", "parameters": [],
            "returnType": "number", "body": "object.amount * 2",
            "enabled": True,
        }],
        "mappings": [], "linkMappings": [],
        "sentinels": [{
            "id": "sentinel-score", "name": "high_score",
            "displayName": "高评分", "enabled": True, "muted": False,
            "bindings": [{"alias": "order", "objectTypeId": "type-order"}],
            "primaryAlias": "order", "links": [],
            "condition": "order.score >= 10",
            "actionIds": ["action-score"],
            "actionParameters": {
                "action-score": {
                    "score": "{{order.score}}",
                },
            },
        }],
    }
    objects = [{
        "objectId": "order-1", "objectTypeId": "type-order",
        "properties": {"id": "O-1", "amount": 6},
    }]

    assert _compute_trial_derived(snapshot, objects) == []
    assert objects[0]["computed"] == {"score": 12}
    result = _simulate_sentinels(snapshot, objects, [])[0]
    assert result["matched"] == 1
    assert result["plannedActionSamples"][0]["parameters"] == {"score": 12}
    assert result["errors"] == []


def test_builtin_sentinel_trial_previews_complete_action_plan_without_effects():
    snapshot = {
        "objectTypes": [
            {
                "id": "type-order", "name": "Order",
                "displayName": "订单", "primaryKey": "order-id",
                "properties": [
                    {"id": "order-id", "name": "id", "type": "string",
                     "required": True},
                    {"id": "amount", "name": "amount", "type": "number"},
                    {"id": "status", "name": "status", "type": "string"},
                    {"id": "email", "name": "email", "type": "string"},
                ],
            },
            {
                "id": "type-audit", "name": "Audit",
                "displayName": "审计记录", "primaryKey": "audit-id",
                "properties": [
                    {"id": "audit-id", "name": "id", "type": "string",
                     "required": True},
                ],
            },
            {
                "id": "type-recipient", "name": "Recipient",
                "displayName": "收件人", "primaryKey": "recipient-id",
                "properties": [
                    {"id": "recipient-id", "name": "id", "type": "string",
                     "required": True},
                    {"id": "recipient-email", "name": "email",
                     "type": "string"},
                ],
            },
        ],
        "linkTypes": [
            {
                "id": "link-audit", "name": "has_audit",
                "displayName": "审计链接",
                "sourceObjectTypeId": "type-order",
                "targetObjectTypeId": "type-audit",
                "cardinality": "one-to-many", "properties": [],
            },
            {
                "id": "link-recipient", "name": "has_recipient",
                "displayName": "收件人链接",
                "sourceObjectTypeId": "type-order",
                "targetObjectTypeId": "type-recipient",
                "cardinality": "one-to-many", "properties": [],
            },
        ],
        "functions": [],
        "actions": [{
            "id": "action-review", "name": "review_order",
            "displayName": "复核订单", "objectTypeId": "type-order",
            "parameters": [],
            "rules": [
                {
                    "id": "validate-amount", "name": "金额校验",
                    "type": "validation", "enabled": True, "order": 0,
                    "config": {
                        "condition": "object.amount > 0",
                        "errorMessage": "金额必须为正数",
                    },
                },
                {
                    "id": "create-audit", "name": "创建审计",
                    "type": "create_object", "enabled": True, "order": 1,
                    "config": {
                        "targetObjectTypeId": "type-audit",
                        "propertyMappings": [],
                    },
                },
                {
                    "id": "update-status", "name": "更新状态",
                    "type": "update_property", "enabled": True, "order": 2,
                    "config": {
                        "targetProperty": "status",
                        "valueSource": "constant",
                        "value": "\"reviewed\"",
                    },
                },
                {
                    "id": "link-audit", "name": "关联审计",
                    "type": "create_link", "enabled": True, "order": 3,
                    "config": {
                        "linkTypeId": "link-audit",
                        "targetSource": "created_object",
                        "targetValue": "type-audit",
                    },
                },
                {
                    "id": "delete-old-recipient", "name": "删除旧收件人",
                    "type": "delete_link", "enabled": True, "order": 4,
                    "config": {
                        "linkTypeId": "link-recipient",
                        "condition": (
                            "target.email == 'old@example.com'"),
                    },
                },
                {
                    "id": "notify-owner", "name": "站内通知",
                    "type": "notification", "enabled": True, "order": 5,
                    "config": {
                        "channel": "internal",
                        "recipientSource": "property",
                        "recipient": "email",
                        "messageTemplate": (
                            "order={{object.id}},status={{object.status}}"),
                    },
                },
                {
                    "id": "webhook-review", "name": "Webhook",
                    "type": "webhook", "enabled": True, "order": 6,
                    "config": {
                        "url": "https://8.8.8.8/review",
                        "method": "POST",
                        "bodyTemplate": (
                            "{\"id\":\"{{object.id}}\","
                            "\"status\":\"{{object.status}}\"}"),
                    },
                },
            ],
        }],
        "mappings": [], "linkMappings": [],
        "sentinels": [{
            "id": "sentinel-review", "name": "review",
            "displayName": "订单复核", "enabled": True, "muted": False,
            "bindings": [{
                "alias": "order", "objectTypeId": "type-order",
            }],
            "primaryAlias": "order", "links": [],
            "condition": "order.amount >= 100",
            "actionIds": ["action-review"],
            "actionParameters": {},
        }],
    }
    objects = [
        {
            "objectId": "order-1", "objectTypeId": "type-order",
            "properties": {
                "id": "O-1", "amount": 120, "status": "new",
                "email": "owner@example.com",
            },
        },
        {
            "objectId": "recipient-1",
            "objectTypeId": "type-recipient",
            "properties": {
                "id": "R-1", "email": "old@example.com",
            },
        },
    ]
    links = [{
        "linkId": "old-recipient-link",
        "linkTypeId": "link-recipient",
        "sourceObjectId": "order-1",
        "targetObjectId": "recipient-1",
        "properties": {},
    }]

    result = _simulate_sentinels(snapshot, objects, links)[0]

    assert result["activation"] == "active"
    assert result["errors"] == []
    plan = result["plannedActionSamples"][0]
    assert plan["status"] == "success"
    assert [item["type"] for item in plan["effects"]] == [
        "create_object", "update_property", "create_link",
        "delete_link", "notification", "webhook",
    ]
    assert all(
        item["status"] == "preview"
        and item["committed"] is False
        for item in plan["effects"])
    assert plan["effects"][3]["matchedLinkIds"] == [
        "old-recipient-link"]
    assert plan["effects"][4]["recipient"] == "owner@example.com"
    assert plan["effects"][5]["method"] == "POST"
    assert plan["sideEffects"] == "none"

    # Disabled/muted controls runtime activation only. Their definitions still
    # need a complete trial; otherwise enabling/unmuting later would reveal an
    # action failure that the isolated workspace silently skipped.
    snapshot["sentinels"][0]["enabled"] = False
    snapshot["actions"][0]["rules"][0]["config"]["condition"] = (
        "object.missing_amount > 0")
    disabled = _simulate_sentinels(snapshot, objects, links)[0]
    assert disabled["activation"] == "disabled"
    assert any("missing_amount" in item for item in disabled["errors"])
    snapshot["sentinels"][0]["enabled"] = True
    snapshot["sentinels"][0]["muted"] = True
    muted = _simulate_sentinels(snapshot, objects, links)[0]
    assert muted["activation"] == "muted"
    assert any("missing_amount" in item for item in muted["errors"])


def test_trial_derived_fails_closed_for_missing_and_disabled_functions():
    base_snapshot = {
        "objectTypes": [{
            "id": "type-order", "name": "Order", "displayName": "订单",
            "primaryKey": "order-id",
            "properties": [
                {"id": "order-id", "name": "id", "type": "string",
                 "required": True},
                {"id": "order-score", "name": "score", "type": "number",
                 "source": "computed", "computed": True,
                 "functionId": "fn-score"},
            ],
        }],
        "linkTypes": [], "actions": [], "mappings": [],
        "linkMappings": [], "sentinels": [],
    }

    missing_objects = [{
        "objectId": "order-missing", "objectTypeId": "type-order",
        "properties": {"id": "O-missing"},
    }]
    missing_snapshot = {**base_snapshot, "functions": []}
    missing_errors = _compute_trial_derived(
        missing_snapshot, missing_objects)
    assert missing_objects[0]["computed"] == {}
    assert {
        item["code"] for item in missing_errors
    } == {"derived_property_evaluation_failed"}
    assert "函数不存在" in missing_errors[0]["message"]

    disabled_objects = [{
        "objectId": "order-disabled", "objectTypeId": "type-order",
        "properties": {"id": "O-disabled"},
    }]
    disabled_snapshot = {
        **base_snapshot,
        "functions": [{
            "id": "fn-score", "name": "score", "displayName": "评分",
            "functionType": "object", "language": "expression",
            "targetObjectTypeId": "type-order", "parameters": [],
            "returnType": "number", "body": "1", "enabled": False,
        }],
    }
    disabled_errors = _compute_trial_derived(
        disabled_snapshot, disabled_objects)
    assert disabled_objects[0]["computed"] == {}
    assert {
        item["code"] for item in disabled_errors
    } == {"derived_property_evaluation_failed"}
    assert "已禁用" in disabled_errors[0]["message"]


def test_trial_derived_uses_the_same_object_collection_scope_as_production():
    snapshot = {
        "objectTypes": [
            {
                "id": "type-order", "name": "Order", "displayName": "订单",
                "primaryKey": "order-id",
                "properties": [
                    {"id": "order-id", "name": "id", "type": "string",
                     "required": True},
                    {
                        "id": "order-local-count", "name": "localCount",
                        "type": "number", "source": "computed",
                        "computed": True, "functionId": "fn-local-count",
                    },
                    {
                        "id": "order-set-count", "name": "orderCount",
                        "type": "number", "source": "computed",
                        "computed": True, "functionId": "fn-set-count",
                    },
                ],
            },
            {
                "id": "type-customer", "name": "Customer",
                "displayName": "客户", "primaryKey": "customer-id",
                "properties": [{
                    "id": "customer-id", "name": "id", "type": "string",
                    "required": True,
                }],
            },
        ],
        "linkTypes": [], "actions": [], "mappings": [],
        "linkMappings": [], "sentinels": [],
        "functions": [
            {
                "id": "fn-local-count", "name": "local_count",
                "displayName": "对象函数集合可见数",
                "functionType": "object", "language": "expression",
                "targetObjectTypeId": "type-order", "parameters": [],
                "returnType": "number", "body": "len(objects)",
                "enabled": True,
            },
            {
                "id": "fn-set-count", "name": "set_count",
                "displayName": "订单集合数",
                "functionType": "object_set", "language": "expression",
                "targetObjectTypeId": "type-order", "parameters": [],
                "returnType": "number", "body": "len(objects)",
                "enabled": True,
            },
        ],
    }
    objects = [
        {
            "objectId": "order-1", "objectTypeId": "type-order",
            "properties": {"id": "O-1"},
        },
        {
            "objectId": "order-2", "objectTypeId": "type-order",
            "properties": {"id": "O-2"},
        },
        {
            "objectId": "customer-1", "objectTypeId": "type-customer",
            "properties": {"id": "C-1"},
        },
    ]

    assert _compute_trial_derived(snapshot, objects) == []
    # Production exposes no collection to an object function, while an
    # object_set function sees only its target object type.
    assert objects[0]["computed"] == {
        "localCount": 0,
        "orderCount": 2,
    }
    assert objects[1]["computed"] == {
        "localCount": 0,
        "orderCount": 2,
    }


def test_trial_derived_rejects_result_outside_computed_property_type():
    snapshot = {
        "objectTypes": [{
            "id": "type-order", "name": "Order", "displayName": "订单",
            "primaryKey": "order-id",
            "properties": [
                {"id": "order-id", "name": "id", "type": "string",
                 "required": True},
                {"id": "order-score", "name": "score", "type": "number",
                 "source": "computed", "computed": True,
                 "functionId": "fn-score"},
            ],
        }],
        "functions": [{
            "id": "fn-score", "name": "score", "displayName": "评分",
            "functionType": "object", "language": "expression",
            "targetObjectTypeId": "type-order", "parameters": [],
            "returnType": "number", "body": "'not-a-number'",
            "enabled": True,
        }],
        "linkTypes": [], "actions": [], "mappings": [],
        "linkMappings": [], "sentinels": [],
    }
    objects = [{
        "objectId": "order-1", "objectTypeId": "type-order",
        "properties": {"id": "O-1"},
    }]

    errors = _compute_trial_derived(snapshot, objects)

    assert objects[0]["computed"] == {}
    assert [item["code"] for item in errors] == [
        "property_type_mismatch",
    ]
    assert "期望 number，实际为 str" in errors[0]["message"]


def test_structure_only_draft_cannot_enter_trial(
        client, auth_headers, ontology):
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
    assert trial.status_code == 422, trial.text
    detail = trial.json()["detail"]
    assert detail["code"] == "publish_validation_failed"
    assert {item["code"] for item in detail["errors"]} == {
        "trial_object_mapping_required",
    }


def test_one_mapped_object_can_enter_trial_with_other_types_unmapped(
        client, auth_headers, ontology, db, monkeypatch):
    oid = ontology["id"]
    _dataset(db, monkeypatch)
    draft = _draft(client, auth_headers, oid, _root(
        client, auth_headers, oid)["id"])
    workspace = _workspace(draft)
    workspace["objectTypes"].append({
        "id": "ot-customer", "name": "Customer", "displayName": "客户",
        "primaryKey": "customer-id", "positionX": 360, "positionY": 20,
        "properties": [{
            "id": "customer-id", "name": "id", "displayName": "客户编号",
            "type": "string", "required": True,
        }],
    })
    workspace["linkTypes"].append({
        "id": "lt-owner", "name": "owned_by", "displayName": "所属客户",
        "sourceObjectTypeId": "ot-order",
        "targetObjectTypeId": "ot-customer",
        "cardinality": "many-to-one",
        "properties": [],
    })
    saved = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace",
        headers=auth_headers, json=workspace,
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["data"]["revision"]
    mapped = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace/mappings",
        headers=auth_headers, json={
            "baseRevision": revision,
            "mappings": [{
                "id": "mapping-order", "curatedDatasetId": "dataset-orders",
                "entityClass": "Order", "targetObjectTypeId": "ot-order",
                "fieldMapping": {
                    "id": "id", "name": "name", "__primary_key__": "id",
                },
                "status": "draft", "confidence": 1,
            }],
            "linkMappings": [], "sentinels": [],
        },
    )
    assert mapped.status_code == 200, mapped.text

    trial = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers, json={},
    )
    assert trial.status_code == 201, trial.text
    run = trial.json()["data"]
    assert run["status"] == "passed"
    assert run["result"]["counts"] == {
        "objects": 2, "links": 0, "facts": 4, "datasets": 1,
    }
    assert {item["code"] for item in run["result"]["warnings"]} == {
        "object_type_unmapped",
    }


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


def test_only_unpublished_leaf_branches_can_be_deleted(
        client, auth_headers, ontology, db, monkeypatch):
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    parent = _draft(client, auth_headers, oid, root["id"])
    child = _draft(client, auth_headers, oid, parent["id"])

    non_leaf = client.delete(
        f"/api/v2/ontologies/{oid}/versions/{parent['id']}",
        headers=auth_headers,
    )
    assert non_leaf.status_code == 409, non_leaf.text
    assert non_leaf.json()["detail"]["code"] == "version_not_leaf"

    deleted_child = client.delete(
        f"/api/v2/ontologies/{oid}/versions/{child['id']}",
        headers=auth_headers,
    )
    assert deleted_child.status_code == 200, deleted_child.text
    assert deleted_child.json()["data"]["id"] == child["id"]

    # Deleted version numbers remain reserved in the audit trail. Recreating a
    # child under the same parent must advance instead of reusing v0.1.1.
    replacement = _draft(client, auth_headers, oid, parent["id"])
    assert replacement["version_number"] == "v0.1.2"
    assert client.delete(
        f"/api/v2/ontologies/{oid}/versions/{replacement['id']}",
        headers=auth_headers,
    ).status_code == 200

    # A trial-ready branch is still unpublished and may be deleted when it is
    # a leaf; isolated objects/runs are removed by the FK cascade.
    _dataset(db, monkeypatch)
    configured = _configure_draft(client, auth_headers, oid, parent)
    run = client.post(
        f"/api/v2/ontologies/{oid}/versions/{parent['id']}/trial-runs",
        headers=auth_headers, json={},
    )
    assert run.status_code == 201, run.text
    assert run.json()["data"]["status"] == "passed"
    deleted_trial = client.delete(
        f"/api/v2/ontologies/{oid}/versions/{configured['id']}",
        headers=auth_headers,
    )
    assert deleted_trial.status_code == 200, deleted_trial.text
    db.expire_all()
    assert db.query(OntologyTrialRun).filter_by(version_id=parent["id"]).count() == 0

    immutable_release = client.delete(
        f"/api/v2/ontologies/{oid}/versions/{root['id']}",
        headers=auth_headers,
    )
    assert immutable_release.status_code == 409, immutable_release.text
    assert immutable_release.json()["detail"]["code"] == "version_delete_forbidden"


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


def test_trial_freezes_computed_projection_and_promotion_activates_exact_values(
        client, auth_headers, ontology, db, monkeypatch):
    oid = ontology["id"]
    _dataset(db, monkeypatch)
    root = _root(client, auth_headers, oid)
    draft = _draft(client, auth_headers, oid, root["id"])
    workspace = _workspace(draft)
    workspace["objectTypes"][0]["properties"].append({
        "id": "p-label", "name": "label", "displayName": "派生标签",
        "type": "string", "required": False,
        "source": "computed", "computed": True,
        "functionId": "fn-label",
    })
    workspace["functions"] = [{
        "id": "fn-label", "name": "derive_label",
        "displayName": "生成派生标签", "functionType": "object",
        "language": "expression", "targetObjectTypeId": "ot-order",
        "parameters": [], "returnType": "string",
        "body": "object['name'] + '-derived'", "enabled": True,
    }]
    saved = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace",
        headers=auth_headers, json=workspace,
    )
    assert saved.status_code == 200, saved.text
    mapping = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace/mappings",
        headers=auth_headers,
        json={
            "baseRevision": saved.json()["data"]["revision"],
            "mappings": [{
                "id": "mapping-order",
                "curatedDatasetId": "dataset-orders",
                "entityClass": "Order",
                "targetObjectTypeId": "ot-order",
                "fieldMapping": {
                    "id": "id", "name": "name", "__primary_key__": "id",
                },
                "status": "draft", "confidence": 1,
            }],
            "linkMappings": [], "sentinels": [],
        },
    )
    assert mapping.status_code == 200, mapping.text

    trial = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers, json={},
    )
    assert trial.status_code == 201, trial.text
    run = trial.json()["data"]
    assert run["status"] == "passed", run
    frozen = db.query(OntologyTrialObject).filter_by(
        trial_run_id=run["id"]).order_by(OntologyTrialObject.object_id).all()
    assert {item.computed["label"] for item in frozen} == {
        "一号订单-derived", "二号订单-derived",
    }
    trial_workspace = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace",
        headers=auth_headers,
    )
    assert trial_workspace.status_code == 200, trial_workspace.text
    assert {
        item["computed"]["label"]
        for item in trial_workspace.json()["data"]["instances"]
    } == {"一号订单-derived", "二号订单-derived"}

    impact = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/impact",
        headers=auth_headers,
    ).json()["data"]
    promoted = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/promote",
        headers=auth_headers,
        json={
            "trialRunId": run["id"],
            "impactHash": impact["impactHash"],
        },
    )
    assert promoted.status_code == 201, promoted.text
    release_id = promoted.json()["data"]["id"]
    assert {
        item.computed["label"]
        for item in db.query(ObjectInstance).filter_by(
            ontology_id=oid, ontology_release_id=release_id).all()
    } == {"一号订单-derived", "二号订单-derived"}


def test_trial_keeps_same_dataset_mappings_separate_by_endpoint_type(
        client, auth_headers, ontology, db, monkeypatch):
    """同一资产映射多个对象类型时，关系端点不能被最后一个映射覆盖。"""
    oid = ontology["id"]
    dataset_service = _paired_dataset(db, monkeypatch)
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
    trial_objects = db.query(OntologyTrialObject).filter_by(
        trial_run_id=run["id"]).all()
    trial_links = db.query(OntologyTrialLink).filter_by(
        trial_run_id=run["id"]).all()
    trial_object_ids = {item.object_id for item in trial_objects}
    trial_link_ids = {item.link_id for item in trial_links}
    trial_relation_ids = {item.source_relation_id for item in trial_links}
    assert None not in trial_relation_ids
    assert {
        item.external_id for item in trial_objects
    }.isdisjoint({"left_id:PAIR-1", "right_id:PAIR-1"})

    # Real sample-data closure: promote the isolated projection, then run the
    # normal MappingService against the exact same approved lake version.  No
    # object/link may be duplicated or re-keyed and every promoted edge must
    # adopt the real Relation lineage produced by the mapping run.
    impact = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/impact",
        headers=auth_headers,
    ).json()["data"]
    promoted = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/promote",
        headers=auth_headers,
        json={"trialRunId": run["id"], "impactHash": impact["impactHash"]},
    )
    assert promoted.status_code == 201, promoted.text
    release_id = promoted.json()["data"]["id"]
    db.expire_all()
    assert {
        item.id for item in db.query(ObjectInstance).filter_by(
            ontology_id=oid).all()
    } == trial_object_ids
    assert {
        item.id for item in db.query(LinkInstance).filter_by(
            ontology_id=oid).all()
    } == trial_link_ids
    assert {
        item.source_relation_id for item in db.query(LinkInstance).filter_by(
            ontology_id=oid).all()
    } == trial_relation_ids

    db.add_all([
        ObjectInstance(
            id="action-created-alert",
            ontology_id=oid,
            ontology_release_id=release_id,
            object_type_id="ot-left",
            properties={"left_id": "ACTION-ALERT"},
            computed={},
            source="action",
            external_id=None,
        ),
        ObjectInstance(
            id="manual-created-note",
            ontology_id=oid,
            ontology_release_id=release_id,
            object_type_id="ot-right",
            properties={"right_id": "MANUAL-NOTE"},
            computed={},
            source="manual",
            external_id=None,
        ),
    ])
    db.commit()

    # The next real lake version removes *all* rows before any legacy Entity has
    # ever been materialized.  A zero-row version is an authoritative source
    # state, not permission to skip projection.  Only Formal reconciliation can
    # see and tombstone these trial-promoted objects/links; action/manual
    # business state must survive.
    dataset_service.create_version(
        "dataset-pairs",
        _empty_csv(["left_id", "right_id"]),
        rowcount=0,
    )

    projection = MappingService(db).build_all(oid, require_approved=True)
    assert projection["formal_projection"]["object_instances"] == 0
    assert projection["formal_projection"]["link_instances"] == 0
    assert projection["formal_projection"]["removed_object_instances"] == 4
    assert projection["formal_projection"]["removed_link_instances"] == 2
    db.expire_all()
    assert db.query(Entity).filter_by(ontology_id=oid).count() == 0
    assert {
        item.id for item in db.query(ObjectInstance).filter_by(
            ontology_id=oid).all()
    } == {"action-created-alert", "manual-created-note"}
    assert db.query(ObjectInstance).filter_by(
        id="action-created-alert", source="action").one()
    assert db.query(ObjectInstance).filter_by(
        id="manual-created-note", source="manual").one()
    assert {
        item.external_id for item in db.query(ObjectInstance).filter_by(
            ontology_id=oid, source="pipeline").all()
    } == set()
    assert db.query(LinkInstance).filter_by(ontology_id=oid).count() == 0
    assert db.query(Relation).filter_by(ontology_id=oid).count() == 0


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
    assert workspace["trialRun"]["id"] == run["id"]
    assert workspace["trialRun"]["status"] == "passed"
    assert len(workspace["instances"]) == 2
    assert {item["properties"]["id"] for item in workspace["instances"]} == {
        "O-1", "O-2",
    }
    assert workspace["linkInstances"] == []
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
    assert impact["releaseReadiness"] == {
        "ready": True,
        "blockingCount": 0,
        "errors": [],
        "trialRunId": run["id"],
        "repairStrategy": None,
        "repairSourceVersionId": draft["id"],
    }

    # Promotion checks the lifecycle state itself, not merely the existence of
    # a passed run. This protects the transition if persisted state is ever
    # repaired or imported independently of trial records.
    draft_row = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    draft_row.lifecycle_status = "editing"
    db.commit()
    invalid_transition = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/promote",
        headers=auth_headers,
        json={"trialRunId": run["id"], "impactHash": impact["impactHash"]},
    )
    assert invalid_transition.status_code == 409, invalid_transition.text
    assert invalid_transition.json()["detail"]["code"] == "trial_ready_required"
    draft_row.lifecycle_status = "trial_ready"
    db.commit()

    dynamic = Sentinel(
        id="dynamic-before-promote", ontology_id=oid,
        name="assistant_orders", display_name="助手订单哨兵",
        bindings=[{"alias": "order", "objectTypeId": "ot-order"}],
        links=[], condition=None, primary_alias="order",
        action_ids=[], action_parameters={},
        enabled=True, status="published", origin="assistant_dynamic",
        bound_release_id=root["id"], definition_revision=1,
        validation_report={"passed": True},
        last_trial_at=datetime.now(timezone.utc),
        last_trial_release_id=root["id"], last_trial_revision=1,
        last_trial_report={"passed": True},
    )
    db.add_all([
        dynamic,
        SentinelMatchState(
            id="dynamic-before-promote-match", ontology_id=oid,
            sentinel_id=dynamic.id, match_key="order=old",
            match_detail={"order": "old"}, runtime_status="completed",
        ),
    ])
    db.commit()

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
    release_row = db.query(OntologyVersion).filter_by(id=release["id"]).one()
    frozen_mapping = release_row.snapshot_formal["mappings"][0]
    assert frozen_mapping["status"] == "applied"
    assert frozen_mapping["fieldMapping"][
        "__applied_dataset_version_id__"
    ] == run["dataset_versions"][0]["versionId"]
    db.refresh(dynamic)
    assert dynamic.enabled is False
    assert dynamic.bound_release_id == root["id"]
    assert dynamic.last_trial_release_id is None
    assert dynamic.last_trial_revision is None
    assert dynamic.last_trial_report is None
    assert db.query(SentinelMatchState).filter_by(
        sentinel_id=dynamic.id).count() == 0

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


def test_production_promotion_observes_restored_mapping_before_runtime_gate(
        client, auth_headers, ontology, db, monkeypatch):
    """Autoflush-off sessions must still publish and pin candidate mappings."""
    oid = ontology["id"]
    _dataset(db, monkeypatch)
    root = _root(client, auth_headers, oid)
    draft = _configure_draft(
        client, auth_headers, oid,
        _draft(client, auth_headers, oid, root["id"]),
    )
    draft_row = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    candidate = copy.deepcopy(draft_row.snapshot_formal)
    candidate["mappings"][0]["fieldMapping"][
        "__auto_apply_on_version__"
    ] = True
    draft_row.snapshot_formal = candidate
    draft_row.snapshot_hash = snapshot_hash(candidate)
    db.commit()

    monkeypatch.setattr(version_router.settings, "environment", "production")
    monkeypatch.setattr(
        version_router,
        "_rebuild_required_query_projections",
        lambda *_args, **_kwargs: {
            "ready": True,
            "neo4j": "ok",
            "chroma": "ok",
            "chroma_count": 2,
        },
    )
    run_response = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers,
        json={},
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()["data"]
    assert run["status"] == "passed", run
    impact = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/impact",
        headers=auth_headers,
    ).json()["data"]

    promoted = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/promote",
        headers=auth_headers,
        json={
            "trialRunId": run["id"],
            "impactHash": impact["impactHash"],
        },
    )

    assert promoted.status_code == 201, promoted.text
    db.expire_all()
    mapping = db.query(OntologyMapping).filter_by(
        ontology_id=oid,
        id="mapping-order",
    ).one()
    assert mapping.status == "applied"
    assert mapping.field_mapping["__auto_apply_on_version__"] is True
    assert mapping.field_mapping[
        "__applied_dataset_version_id__"
    ] == run["dataset_versions"][0]["versionId"]


def test_rollback_creates_new_activation_and_preserves_historical_runtime_lineage(
        client, auth_headers, ontology, db, admin_user, monkeypatch):
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)

    def snapshot(display_name: str) -> dict:
        suffix = "old" if display_name == "历史" else "new"
        return {
            "objectTypes": [
                {
                    "id": "ot-source", "name": "Source",
                    "displayName": f"{display_name}源",
                    "primaryKey": "p-source-id",
                    "properties": [{
                        "id": "p-source-id", "name": "id",
                        "displayName": "源编号", "type": "string",
                        "required": True,
                    }, {
                        "id": "p-source-label", "name": "label",
                        "displayName": "派生标签", "type": "string",
                        "required": False, "source": "computed",
                        "computed": True, "functionId": "fn-source-label",
                    }],
                    "interfaces": [], "positionX": 0, "positionY": 0,
                },
                {
                    "id": "ot-target", "name": "Target",
                    "displayName": f"{display_name}目标",
                    "primaryKey": "p-target-id",
                    "properties": [{
                        "id": "p-target-id", "name": "id",
                        "displayName": "目标编号", "type": "string",
                        "required": True,
                    }],
                    "interfaces": [], "positionX": 0, "positionY": 0,
                },
            ],
            "linkTypes": [{
                "id": "lt-related", "name": "related",
                "displayName": "关联",
                "sourceObjectTypeId": "ot-source",
                "targetObjectTypeId": "ot-target",
                "cardinality": "many-to-many", "properties": [],
            }],
            "actions": [], "functions": [{
                "id": "fn-source-label", "name": "source_label",
                "displayName": "源标签", "functionType": "object",
                "language": "expression",
                "targetObjectTypeId": "ot-source",
                "parameters": [], "returnType": "string",
                "body": f"object['id'] + '-{suffix}'",
                "enabled": True,
            }],
            "sentinels": [{
                "id": "sentinel-builtin", "name": "watch_source",
                "displayName": f"{display_name}内置哨兵",
                "bindings": [{
                    "alias": "source", "objectTypeId": "ot-source",
                }],
                "links": [], "condition": None, "conditionRows": [],
                "conditionLogic": "and", "primaryAlias": "source",
                "actionIds": [], "actionParameters": {},
                "onChange": True, "onSchedule": False,
                "scanIntervalSeconds": 300,
                "triggerMode": "on_enter_leave",
                "muted": False, "enabled": True, "status": "published",
            }],
            "mappings": [], "linkMappings": [],
        }

    target_snapshot = snapshot("历史")
    current_snapshot = snapshot("当前")
    target = OntologyVersion(
        id="release-v1-target", ontology_id=oid, version_number="v1",
        version_label="历史发布", parent_version_id=root["id"],
        node_kind="release", lifecycle_status="released", revision=0,
        snapshot_formal=target_snapshot,
        snapshot_hash=snapshot_hash(target_snapshot),
        published_at=datetime.now(timezone.utc),
        created_by=admin_user.id,
    )
    db.add(target)
    db.flush()
    target.base_release_id = target.id
    target_id = target.id
    current = OntologyVersion(
        id="release-v2-current", ontology_id=oid, version_number="v2",
        version_label="当前发布", parent_version_id=target.id,
        node_kind="release", lifecycle_status="released", revision=0,
        snapshot_formal=current_snapshot,
        snapshot_hash=snapshot_hash(current_snapshot),
        published_at=datetime.now(timezone.utc),
        created_by=admin_user.id,
    )
    db.add(current)
    db.flush()
    current.base_release_id = current.id
    current_id = current.id
    # Reproduce a legacy pointer-reuse history: a later release number exists,
    # while the authoritative pointer was moved back to v2. New activation
    # numbering must append globally instead of generating an out-of-order v3.
    dormant = OntologyVersion(
        id="release-v7-historical", ontology_id=oid, version_number="v7",
        version_label="历史上更晚的节点", parent_version_id=current.id,
        node_kind="release", lifecycle_status="released", revision=0,
        snapshot_formal=current_snapshot,
        snapshot_hash=snapshot_hash(current_snapshot),
        published_at=datetime.now(timezone.utc),
        created_by=admin_user.id,
    )
    db.add(dormant)
    db.flush()
    dormant.base_release_id = dormant.id
    project = db.query(OntologyProject).filter_by(id=oid).one()
    project.current_release_id = current.id
    project.version = current.version_number
    project.status = "published"
    version_router._restore_formal_snapshot(db, oid, current_snapshot)
    db.flush()
    db.add_all([
        ObjectInstance(
            id="source-1", ontology_id=oid,
            ontology_release_id=current.id,
            object_type_id="ot-source", properties={"id": "S-1"},
            computed={"label": "S-1-new"},
            source="pipeline", external_id="S-1",
        ),
        ObjectInstance(
            id="target-1", ontology_id=oid,
            ontology_release_id=current.id,
            object_type_id="ot-target", properties={"id": "T-1"},
            computed={}, source="pipeline", external_id="T-1",
        ),
        LinkInstance(
            id="related-1", ontology_id=oid,
            ontology_release_id=current.id,
            link_type_id="lt-related",
            source_object_id="source-1", target_object_id="target-1",
            properties={},
        ),
        Sentinel(
            id="sentinel-dynamic", ontology_id=oid,
            name="assistant_watch", display_name="助手动态哨兵",
            bindings=[{"alias": "source", "objectTypeId": "ot-source"}],
            links=[], condition=None, primary_alias="source",
            action_ids=[], action_parameters={},
            enabled=True, status="published", origin="assistant_dynamic",
            bound_release_id=current.id, definition_revision=3,
            validation_report={"passed": True},
            last_trial_at=datetime.now(timezone.utc),
            last_trial_release_id=current.id, last_trial_revision=3,
            last_trial_report={"passed": True},
        ),
        SentinelMatchState(
            id="match-builtin", ontology_id=oid,
            sentinel_id="sentinel-builtin", match_key="source-1",
            match_detail={"source": "source-1"}, runtime_status="completed",
        ),
        SentinelMatchState(
            id="match-dynamic", ontology_id=oid,
            sentinel_id="sentinel-dynamic", match_key="source-1",
            match_detail={"source": "source-1"}, runtime_status="completed",
        ),
        SentinelFiring(
            id="firing-v2", ontology_id=oid,
            sentinel_id="sentinel-dynamic", sentinel_name="助手动态哨兵",
            trigger_source="change", matches=[], entered=[], left=[],
            action_results=[], status="fired",
            ontology_version="v2", ontology_release_id=current.id,
        ),
        ActionExecutionLog(
            id="pending-v2", ontology_id=oid, action_id="historical-action",
            object_type_id="ot-source", object_instance_id="source-1",
            parameters={}, status="pending", validation_errors=[], effects=[],
            dry_run=False, ontology_version="v2",
            ontology_release_id=current.id,
        ),
    ])
    db.commit()
    db.expunge_all()
    monkeypatch.setattr(
        version_router, "_rebuild_required_query_projections",
        lambda *_args, **_kwargs: {
            "ready": True, "neo4j": "ok", "chroma": "ok",
            "chroma_count": 2,
        },
    )

    response = client.post(
        f"/api/v2/ontologies/{oid}/versions/{target_id}/rollback",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    activation = response.json()["data"]
    assert activation["id"] not in {target_id, current_id}
    assert activation["version_number"] == "v8"
    assert activation["parent_version_id"] == current_id
    assert activation["rolled_back_to_id"] == target_id

    db.expire_all()
    project = db.query(OntologyProject).filter_by(id=oid).one()
    assert project.current_release_id == activation["id"]
    assert project.version == "v8"
    assert {
        item.ontology_release_id
        for item in db.query(ObjectInstance).filter_by(ontology_id=oid).all()
    } == {activation["id"]}
    assert db.query(ObjectInstance).filter_by(
        id="source-1").one().computed == {"label": "S-1-old"}
    assert {
        item.ontology_release_id
        for item in db.query(LinkInstance).filter_by(ontology_id=oid).all()
    } == {activation["id"]}
    builtin = db.query(Sentinel).filter_by(id="sentinel-builtin").one()
    assert builtin.status == "published"
    assert builtin.trigger_mode == "on_enter_leave"
    dynamic = db.query(Sentinel).filter_by(id="sentinel-dynamic").one()
    assert dynamic.enabled is False
    assert dynamic.bound_release_id == current_id
    assert dynamic.last_trial_at is None
    assert dynamic.last_trial_release_id is None
    assert dynamic.last_trial_revision is None
    assert dynamic.last_trial_report is None
    assert db.query(SentinelMatchState).filter_by(ontology_id=oid).count() == 0

    firing = db.query(SentinelFiring).filter_by(id="firing-v2").one()
    pending = db.query(ActionExecutionLog).filter_by(id="pending-v2").one()
    assert firing.ontology_release_id == current_id
    assert pending.ontology_release_id == current_id
    derived = db.query(PropertyFact).filter_by(
        ontology_id=oid, instance_id="source-1",
        property_name="label", kind="derived").one()
    assert derived.ontology_release_id == activation["id"]
    decision = client.post(
        f"/api/v2/formal/ontologies/{oid}/action-logs/pending-v2/decide",
        headers=auth_headers,
        json={"decision": "approved", "releaseId": activation["id"]},
    )
    assert decision.status_code == 409, decision.text
    assert "发布节点不一致" in str(decision.json()["detail"])


def test_release_readiness_groups_legacy_missing_property_mappings(
        client, auth_headers, ontology, db, monkeypatch):
    """Legacy passed trials still receive a friendly, fail-closed preflight.

    Older deployments could freeze a trial while one or more persisted fields
    were not mapped. The impact endpoint exposes structured target/field
    metadata so the UI can group blockers instead of dumping messages.
    """
    oid = ontology["id"]
    _dataset(db, monkeypatch)
    root = _root(client, auth_headers, oid)
    draft = _configure_draft(
        client, auth_headers, oid,
        _draft(client, auth_headers, oid, root["id"]),
    )
    run_payload = client.post(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/trial-runs",
        headers=auth_headers, json={},
    ).json()["data"]

    current_row = db.query(OntologyVersion).filter_by(id=root["id"]).one()
    draft_row = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    run_row = db.query(OntologyTrialRun).filter_by(id=run_payload["id"]).one()
    legacy_snapshot = copy.deepcopy(draft_row.snapshot_formal)
    legacy_snapshot["mappings"][0]["fieldMapping"].pop("name")
    legacy_hash = snapshot_hash(legacy_snapshot)
    draft_row.snapshot_formal = legacy_snapshot
    draft_row.snapshot_hash = legacy_hash
    draft_row.lifecycle_status = "trial_ready"
    run_row.snapshot_hash = legacy_hash
    run_row.impact_hash = impact_report(
        current_row.snapshot_formal, legacy_snapshot)["impactHash"]
    db.commit()

    response = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/impact",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    readiness = response.json()["data"]["releaseReadiness"]
    assert readiness["ready"] is False
    assert readiness["blockingCount"] == 1
    assert readiness["trialRunId"] == run_payload["id"]
    assert readiness["repairStrategy"] == "create_draft"
    assert readiness["repairSourceVersionId"] == draft["id"]
    assert readiness["errors"] == [{
        "code": "mapping_property_missing",
        "kind": "mapping",
        "id": "mapping-order",
        "name": "Order",
        "targetId": "ot-order",
        "targetName": "订单",
        "message": "Mapping「Order」未覆盖 ObjectType「订单」的存储属性「name」",
        "field": "name",
    }]


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
