"""发布影响预览携带世界模型消费方告警（二期B）。

- 草稿删除的对象类型若被已发布推演服务声明为适用类型，impact 预览必须列出
  受影响服务（发布确认弹窗据此告警）；
- 未删除类型或其他本体的服务不受影响；
- 告警为实时查询，不参与 impact 哈希（哈希只覆盖纯结构 diff）。
"""
import copy
from datetime import datetime, timezone

from app.ontologies.projects.models import OntologyProject
from app.ontologies.versions.models import OntologyVersion
from app.ontologies.versions.snapshot_contract import (
    complete_snapshot,
    snapshot_hash,
)
from app.world_model.models import WorldModelProject, WorldModelScriptVersion, WorldModelService


def _object_types_snapshot():
    return complete_snapshot({
        "objectTypes": [
            {"id": "ot-a", "name": "TypeA", "displayName": "甲类型"},
            {"id": "ot-b", "name": "TypeB", "displayName": "乙类型"},
        ],
    })


def _insert_release(db, oid, admin_user, snap):
    release = OntologyVersion(
        id=f"rel-{oid[:8]}",
        ontology_id=oid,
        version_number="v1",
        version_label="基线",
        node_kind="release",
        lifecycle_status="released",
        revision=0,
        snapshot_formal=copy.deepcopy(snap),
        snapshot_hash=snapshot_hash(snap),
        published_at=datetime.now(timezone.utc),
        created_by=admin_user.id,
    )
    db.add(release)
    db.flush()
    # v1 建库可能已生成空 v0 基线并占用指针：显式切到本测试的基线
    db.query(OntologyProject).filter(
        OntologyProject.id == oid).update(
        {OntologyProject.current_release_id: release.id})
    db.commit()
    return release


def _insert_draft(db, oid, admin_user, base, snap):
    draft = OntologyVersion(
        id=f"draft-{oid[:8]}",
        ontology_id=oid,
        version_number="d1",
        version_label="删除乙类型",
        parent_version_id=base.id,
        base_release_id=base.id,
        node_kind="draft",
        lifecycle_status="editing",
        revision=0,
        snapshot_formal=copy.deepcopy(snap),
        snapshot_hash=snapshot_hash(snap),
        created_by=admin_user.id,
    )
    db.add(draft)
    db.commit()
    return draft


def _insert_service(db, oid, name, type_ids):
    project = WorldModelProject(name=f"{name}-项目", script="def simulate(c,a,h):\n    return {}")
    db.add(project)
    db.flush()
    version = WorldModelScriptVersion(project_id=project.id, version_no=1,
                                      script=project.script, test_input={})
    db.add(version)
    db.flush()
    service = WorldModelService(
        project_id=project.id, version_id=version.id, name=name, status="online",
        applicable_object_types={"ontology_id": oid, "object_type_ids": type_ids},
        preconditions=[],
    )
    db.add(service)
    db.commit()
    return service


def test_draft_impact_reports_affected_world_model_services(
        client, auth_headers, ontology, admin_user, db):
    oid = ontology["id"]
    snap = _object_types_snapshot()
    release = _insert_release(db, oid, admin_user, snap)

    after = copy.deepcopy(snap)
    after["objectTypes"] = [t for t in after["objectTypes"] if t["id"] != "ot-b"]
    draft = _insert_draft(db, oid, admin_user, release, after)

    affected = _insert_service(db, oid, "乙类型推演", ["ot-b"])
    _insert_service(db, oid, "甲类型推演", ["ot-a"])          # 未删除 → 不受影响
    _insert_service(db, "other-ontology", "他本体推演", ["ot-b"])  # 他本体 → 不受影响

    r = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft.id}/impact",
        headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"]["deleted"] >= 1
    assert data["impactHash"]
    assert [item["name"] for item in data["worldModelImpact"]] == ["乙类型推演"]
    assert data["worldModelImpact"][0]["serviceId"] == affected.id
    assert data["worldModelImpact"][0]["missingObjectTypeIds"] == ["ot-b"]


def test_draft_impact_without_deletions_has_no_world_model_section(
        client, auth_headers, ontology, admin_user, db):
    oid = ontology["id"]
    snap = _object_types_snapshot()
    release = _insert_release(db, oid, admin_user, snap)

    after = copy.deepcopy(snap)
    after["objectTypes"] = after["objectTypes"] + [
        {"id": "ot-c", "name": "TypeC", "displayName": "丙类型"}]
    draft = _insert_draft(db, oid, admin_user, release, after)
    _insert_service(db, oid, "甲乙推演", ["ot-a", "ot-b"])

    r = client.get(
        f"/api/v2/ontologies/{oid}/versions/{draft.id}/impact",
        headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"]["deleted"] == 0
    assert data["worldModelImpact"] == []
