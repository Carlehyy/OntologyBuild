"""语义漂移透出、试跑前只读预检与手工保存审计的回归测试。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from app.exploration.document import canvas_fingerprint
from app.exploration.reverse_projection import project_snapshot_to_canvas
from app.models.inference import AuditLog
from app.models.ontology import OntologyProject
from app.models.ontology_version import OntologyTrialRun, OntologyVersion
from app.ontologies.versions.evolution_service import (
    complete_snapshot,
    snapshot_hash,
)

_SEMANTIC_TEST_DOCUMENT_MD = "# 测试需求文档\n\n预检与语义漂移回归用确定性文档。\n"

_ORDER_OBJECT_TYPE = {
    "id": "ot-order", "name": "Order", "displayName": "订单",
    "primaryKey": "p-id", "positionX": 10, "positionY": 20,
    "properties": [
        {"id": "p-id", "name": "id", "displayName": "订单号",
         "type": "string", "required": True},
        {"id": "p-name", "name": "name", "displayName": "名称",
         "type": "string", "required": True},
    ],
}

_CUSTOMER_OBJECT_TYPE = {
    "id": "ot-customer", "name": "Customer", "displayName": "客户",
    "primaryKey": "p-cid", "positionX": 300, "positionY": 20,
    "properties": [
        {"id": "p-cid", "name": "cid", "displayName": "客户号",
         "type": "string", "required": True},
    ],
}

_ORDER_MAPPING = {
    "id": "mapping-order", "curatedDatasetId": "dataset-orders",
    "entityClass": "Order", "targetObjectTypeId": "ot-order",
    "fieldMapping": {"id": "id", "name": "name", "__primary_key__": "id"},
    "status": "draft", "confidence": 1,
}

_CHECK_IDS = (
    "editable_draft", "single_flight", "base_up_to_date",
    "structure", "mapping_contract", "semantic_consistency",
)


def _attach_semantic_layer(db, version_id: str) -> None:
    """为版本写回与当前结构快照自洽的最小业务语义层。"""
    row = db.query(OntologyVersion).filter_by(id=version_id).one()
    canvas = project_snapshot_to_canvas(row.snapshot_formal)
    row.snapshot_semantic = {
        "canvas": canvas,
        "canvasFingerprint": canvas_fingerprint(canvas),
        "documentMd": _SEMANTIC_TEST_DOCUMENT_MD,
        "documentTitle": "测试需求文档",
        "documentFingerprint": hashlib.sha256(
            _SEMANTIC_TEST_DOCUMENT_MD.encode("utf-8")).hexdigest(),
        "semanticRevision": 1,
    }
    db.commit()


def _root(client, headers, ontology_id: str) -> dict:
    response = client.get(
        f"/api/v2/ontologies/{ontology_id}/version-tree", headers=headers)
    assert response.status_code == 200, response.text
    tree = response.json()["data"]
    return next(
        item for item in tree["versions"] if item["version_number"] == "v0")


def _draft(client, headers, ontology_id: str, source_id: str) -> dict:
    response = client.post(
        f"/api/v2/ontologies/{ontology_id}/versions/{source_id}/drafts",
        headers=headers, json={"versionLabel": "预检回归"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _workspace_body(revision: str, object_types: list | None = None) -> dict:
    return {
        "baseRevision": revision,
        "objectTypes": (
            object_types if object_types is not None else [_ORDER_OBJECT_TYPE]
        ),
        "linkTypes": [], "actions": [], "functions": [],
        "instances": [], "linkInstances": [],
    }


def _revision_of(db, version_id: str) -> str:
    row = db.query(OntologyVersion).filter_by(id=version_id).one()
    return f"{row.revision}:{row.snapshot_hash}"


def _save_workspace(client, headers, ontology_id: str, version_id: str,
                    revision: str, object_types: list | None = None):
    return client.put(
        f"/api/v2/ontologies/{ontology_id}/versions/{version_id}/workspace",
        headers=headers,
        json=_workspace_body(revision, object_types),
    )


def _clean_draft(client, headers, ontology_id: str, db) -> dict:
    """结构 + 映射 + 语义层均自洽的草稿，预检应当全绿。"""
    root = _root(client, headers, ontology_id)
    draft = _draft(client, headers, ontology_id, root["id"])
    saved = _save_workspace(
        client, headers, ontology_id, draft["id"],
        f"{draft['revision']}:{draft['snapshot_hash']}",
    )
    assert saved.status_code == 200, saved.text
    mapped = client.put(
        f"/api/v2/ontologies/{ontology_id}/versions/{draft['id']}"
        "/workspace/mappings",
        headers=headers,
        json={
            "baseRevision": saved.json()["data"]["revision"],
            "mappings": [dict(_ORDER_MAPPING)],
            "linkMappings": [], "sentinels": [],
        },
    )
    assert mapped.status_code == 200, mapped.text
    _attach_semantic_layer(db, draft["id"])
    return draft


def _preflight(client, headers, ontology_id: str, version_id: str) -> dict:
    response = client.post(
        f"/api/v2/ontologies/{ontology_id}/versions/{version_id}"
        "/trial-preflight",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _status_by_id(payload: dict) -> dict:
    return {check["id"]: check["status"] for check in payload["checks"]}


def _check(payload: dict, check_id: str) -> dict:
    return next(item for item in payload["checks"] if item["id"] == check_id)


# ---------------------------------------------------------------- 语义漂移透出


def test_semantic_endpoint_returns_per_issue_drift_list(
        client, auth_headers, ontology, db):
    """semantic 读端点透出逐条一致性 issue；无语义层时 issues 为空列表。"""
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    draft = _draft(client, auth_headers, oid, root["id"])
    saved = _save_workspace(
        client, auth_headers, oid, draft["id"],
        f"{draft['revision']}:{draft['snapshot_hash']}",
    )
    assert saved.status_code == 200, saved.text
    url = f"/api/v2/ontologies/{oid}/versions/{draft['id']}/semantic"

    # 语义层尚未沉淀：结构与语义的差异不是漂移，issues 必须为空
    payload = client.get(url, headers=auth_headers).json()["data"]
    assert payload["semantic"] is None
    assert payload["overview"]["hasSemanticLayer"] is False
    assert payload["issues"] == []

    # 语义层与结构自洽：零漂移
    _attach_semantic_layer(db, draft["id"])
    payload = client.get(url, headers=auth_headers).json()["data"]
    assert payload["semantic"]["documentTitle"] == "测试需求文档"
    assert payload["issues"] == []

    # 手工编辑结构后语义层未同步：逐条漂移透出
    saved = _save_workspace(
        client, auth_headers, oid, draft["id"],
        _revision_of(db, draft["id"]),
        [_ORDER_OBJECT_TYPE, _CUSTOMER_OBJECT_TYPE],
    )
    assert saved.status_code == 200, saved.text
    payload = client.get(url, headers=auth_headers).json()["data"]
    assert payload["semantic"] is not None
    assert len(payload["issues"]) == 1
    issue = payload["issues"][0]
    assert issue["code"] == "semantic_business_missing"
    assert issue["kind"] == "objectType"
    assert issue["id"] == "Customer"
    assert "在业务画布中没有对应" in issue["message"]
    assert payload["overview"]["consistency"]["issueCount"] == 1


# ---------------------------------------------------------------- 试跑前只读预检


def test_trial_preflight_passes_on_clean_draft_without_writes(
        client, auth_headers, ontology, db):
    oid = ontology["id"]
    draft = _clean_draft(client, auth_headers, oid, db)

    payload = _preflight(client, auth_headers, oid, draft["id"])

    assert payload["ok"] is True
    assert payload["versionId"] == draft["id"]
    assert payload["revision"] == _revision_of(db, draft["id"])
    statuses = _status_by_id(payload)
    assert statuses == {check_id: "pass" for check_id in _CHECK_IDS}
    assert [check["label"] for check in payload["checks"]] == [
        "草稿可编辑性", "无进行中的试跑", "基线未过期",
        "结构校验", "试跑映射契约", "业务语义一致性",
    ]
    # advisory：不创建试跑记录、不推进 revision、不改变生命周期
    assert db.query(OntologyTrialRun).filter_by(
        version_id=draft["id"]).count() == 0
    row = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    assert row.revision == 2
    assert row.lifecycle_status == "editing"


def test_trial_preflight_requires_existing_ontology_and_version(
        client, auth_headers, ontology):
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    missing_version = client.post(
        f"/api/v2/ontologies/{oid}/versions/no-such-version/trial-preflight",
        headers=auth_headers,
    )
    assert missing_version.status_code == 404, missing_version.text
    missing_ontology = client.post(
        f"/api/v2/ontologies/no-such-ontology/versions/{root['id']}"
        "/trial-preflight",
        headers=auth_headers,
    )
    assert missing_ontology.status_code == 404, missing_ontology.text


def test_trial_preflight_reports_frozen_draft(
        client, auth_headers, ontology, db):
    oid = ontology["id"]
    draft = _clean_draft(client, auth_headers, oid, db)
    row = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    row.lifecycle_status = "trial_ready"
    db.commit()

    payload = _preflight(client, auth_headers, oid, draft["id"])

    assert payload["ok"] is False
    statuses = _status_by_id(payload)
    assert statuses["editable_draft"] == "fail"
    assert all(
        status == "pass"
        for check_id, status in statuses.items()
        if check_id != "editable_draft"
    )
    check = _check(payload, "editable_draft")
    assert check["label"] == "草稿可编辑性"
    assert check["errors"][0]["code"] == "trial_snapshot_frozen"
    assert db.query(OntologyTrialRun).filter_by(
        version_id=draft["id"]).count() == 0


def test_trial_preflight_reports_release_node_as_not_editable(
        client, auth_headers, ontology):
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)

    payload = _preflight(client, auth_headers, oid, root["id"])

    assert payload["ok"] is False
    check = _check(payload, "editable_draft")
    assert check["status"] == "fail"
    assert check["errors"][0]["code"] == "trial_requires_draft"


def test_trial_preflight_reports_running_trial(
        client, auth_headers, ontology, admin_user, db):
    oid = ontology["id"]
    draft = _clean_draft(client, auth_headers, oid, db)
    row = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db.add(OntologyTrialRun(
        id="preflight-running-trial",
        ontology_id=oid, version_id=draft["id"],
        revision=row.revision, snapshot_hash=row.snapshot_hash,
        base_release_id=row.base_release_id,
        claim_token="preflight-claim",
        lease_expires_at=lease_expires_at,
        status="running", dataset_versions=[], result_json={},
        impact_hash="", created_by=admin_user.id,
    ))
    db.commit()

    payload = _preflight(client, auth_headers, oid, draft["id"])

    assert payload["ok"] is False
    statuses = _status_by_id(payload)
    assert statuses["single_flight"] == "fail"
    assert all(
        status == "pass"
        for check_id, status in statuses.items()
        if check_id != "single_flight"
    )
    error = _check(payload, "single_flight")["errors"][0]
    assert error["code"] == "trial_already_running"
    assert error["trialRunId"] == "preflight-running-trial"
    assert error["leaseExpiresAt"] is not None
    # 预检不回收过期租约、不终结在跑试跑
    db.expire_all()
    run = db.query(OntologyTrialRun).filter_by(
        id="preflight-running-trial").one()
    assert run.status == "running"


def test_trial_preflight_reports_outdated_base(
        client, auth_headers, ontology, db):
    oid = ontology["id"]
    draft = _clean_draft(client, auth_headers, oid, db)
    row = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    row.base_release_id = "stale-release-id"
    db.commit()

    payload = _preflight(client, auth_headers, oid, draft["id"])

    assert payload["ok"] is False
    statuses = _status_by_id(payload)
    assert statuses["base_up_to_date"] == "fail"
    assert all(
        status == "pass"
        for check_id, status in statuses.items()
        if check_id != "base_up_to_date"
    )
    error = _check(payload, "base_up_to_date")["errors"][0]
    assert error["code"] == "draft_base_outdated"
    assert error["draftBaseReleaseId"] == "stale-release-id"
    project = db.query(OntologyProject).filter_by(id=oid).one()
    assert error["currentReleaseId"] == project.current_release_id


def test_trial_preflight_reports_structure_errors(
        client, auth_headers, ontology, db):
    oid = ontology["id"]
    draft = _clean_draft(client, auth_headers, oid, db)
    row = db.query(OntologyVersion).filter_by(id=draft["id"]).one()
    corrupt = complete_snapshot(row.snapshot_formal)
    corrupt["sentinels"] = [{
        "id": "sentinel-order-watch", "name": "order_watch",
        "displayName": "订单监控", "description": "",
        "bindings": [{
            "alias": "order", "objectTypeId": "ot-order", "filter": None,
        }],
        "links": [], "condition": "", "conditionRows": [],
        "conditionLogic": "and", "primaryAlias": "order",
        "actionIds": [], "actionParameters": {},
        "onChange": True, "onSchedule": False,
        "scanIntervalSeconds": "not-an-integer",
        "triggerMode": "on_enter", "muted": False, "enabled": True,
        "status": "draft",
    }]
    row.snapshot_formal = corrupt
    row.snapshot_hash = snapshot_hash(corrupt)
    db.commit()
    # 语义层与污染后的结构重新自洽，确保只有结构校验一项失败
    _attach_semantic_layer(db, draft["id"])

    payload = _preflight(client, auth_headers, oid, draft["id"])

    assert payload["ok"] is False
    statuses = _status_by_id(payload)
    assert statuses["structure"] == "fail"
    assert all(
        status == "pass"
        for check_id, status in statuses.items()
        if check_id != "structure"
    )
    codes = {item["code"] for item in _check(payload, "structure")["errors"]}
    assert "invalid_sentinel_scan_interval_type" in codes
    assert db.query(OntologyTrialRun).filter_by(
        version_id=draft["id"]).count() == 0


def test_trial_preflight_reports_mapping_contract_violation(
        client, auth_headers, ontology, db):
    oid = ontology["id"]
    draft = _clean_draft(client, auth_headers, oid, db)
    broken_mapping = dict(_ORDER_MAPPING)
    broken_mapping["fieldMapping"] = {"id": "id", "__primary_key__": "id"}
    mapped = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace/mappings",
        headers=auth_headers,
        json={
            "baseRevision": _revision_of(db, draft["id"]),
            "mappings": [broken_mapping],
            "linkMappings": [], "sentinels": [],
        },
    )
    assert mapped.status_code == 200, mapped.text

    payload = _preflight(client, auth_headers, oid, draft["id"])

    assert payload["ok"] is False
    statuses = _status_by_id(payload)
    assert statuses["mapping_contract"] == "fail"
    assert all(
        status == "pass"
        for check_id, status in statuses.items()
        if check_id != "mapping_contract"
    )
    errors = _check(payload, "mapping_contract")["errors"]
    assert {item["code"] for item in errors} == {"trial_object_mapping_required"}


def test_trial_preflight_reports_semantic_drift(
        client, auth_headers, ontology, db):
    oid = ontology["id"]
    draft = _clean_draft(client, auth_headers, oid, db)
    saved = _save_workspace(
        client, auth_headers, oid, draft["id"],
        _revision_of(db, draft["id"]),
        [_ORDER_OBJECT_TYPE, _CUSTOMER_OBJECT_TYPE],
    )
    assert saved.status_code == 200, saved.text

    payload = _preflight(client, auth_headers, oid, draft["id"])

    assert payload["ok"] is False
    statuses = _status_by_id(payload)
    assert statuses["semantic_consistency"] == "fail"
    assert all(
        status == "pass"
        for check_id, status in statuses.items()
        if check_id != "semantic_consistency"
    )
    errors = _check(payload, "semantic_consistency")["errors"]
    assert {item["code"] for item in errors} == {"semantic_business_missing"}
    assert errors[0]["id"] == "Customer"
    assert db.query(OntologyTrialRun).filter_by(
        version_id=draft["id"]).count() == 0


# ---------------------------------------------------------------- 手工保存审计


def test_workspace_save_writes_exactly_one_audit_log(
        client, auth_headers, ontology, admin_user, db):
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    draft = _draft(client, auth_headers, oid, root["id"])

    saved = _save_workspace(
        client, auth_headers, oid, draft["id"],
        f"{draft['revision']}:{draft['snapshot_hash']}",
    )
    assert saved.status_code == 200, saved.text

    logs = db.query(AuditLog).filter_by(
        ontology_id=oid, event_subtype="workspace_saved").all()
    assert len(logs) == 1
    log = logs[0]
    assert log.event_type == "edit"
    assert log.user_id == admin_user.id
    assert log.user_name == "admin"
    assert log.object_type == "ontology_version"
    assert log.object_id == draft["id"]
    assert draft["version_number"] in log.description
    meta = log.meta
    assert meta["revision"] == 1
    assert meta["snapshotHash"] == saved.json()["data"]["snapshotHash"]
    assert meta["diff"]["objectTypes"] == {
        "added": 1, "modified": 0, "deleted": 0,
        "addedNames": ["Order"], "modifiedNames": [], "deletedNames": [],
    }
    assert meta["diff"]["total"] == {
        "added": 1, "modified": 0, "deleted": 0,
    }
    # 快照原文不进审计 meta
    assert "objectTypes" not in meta

    # 冲突保存被拒绝时不追加审计记录
    conflict = _save_workspace(
        client, auth_headers, oid, draft["id"], "0:stale-hash")
    assert conflict.status_code == 409, conflict.text
    assert db.query(AuditLog).filter_by(
        ontology_id=oid, event_subtype="workspace_saved").count() == 1


def test_workspace_mappings_save_writes_exactly_one_audit_log(
        client, auth_headers, ontology, admin_user, db):
    oid = ontology["id"]
    root = _root(client, auth_headers, oid)
    draft = _draft(client, auth_headers, oid, root["id"])
    saved = _save_workspace(
        client, auth_headers, oid, draft["id"],
        f"{draft['revision']}:{draft['snapshot_hash']}",
    )
    assert saved.status_code == 200, saved.text

    mapped = client.put(
        f"/api/v2/ontologies/{oid}/versions/{draft['id']}/workspace/mappings",
        headers=auth_headers,
        json={
            "baseRevision": saved.json()["data"]["revision"],
            "mappings": [dict(_ORDER_MAPPING)],
            "linkMappings": [], "sentinels": [],
        },
    )
    assert mapped.status_code == 200, mapped.text

    logs = db.query(AuditLog).filter_by(
        ontology_id=oid, event_subtype="workspace_mappings_saved").all()
    assert len(logs) == 1
    log = logs[0]
    assert log.event_type == "edit"
    assert log.user_id == admin_user.id
    assert log.user_name == "admin"
    assert log.object_type == "ontology_version"
    assert log.object_id == draft["id"]
    assert draft["version_number"] in log.description
    meta = log.meta
    assert meta["revision"] == 2
    assert meta["snapshotHash"] == mapped.json()["data"]["snapshotHash"]
    assert meta["diff"]["mappings"] == {
        "added": 1, "modified": 0, "deleted": 0,
        "addedNames": ["mapping-order"], "modifiedNames": [], "deletedNames": [],
    }
