"""版本业务语义层：会话绑定引导 + 合并进已有本体走版本正门。

  1. 绑定会话创建：语义层画布优先于结构反向投影；版本不存在 404；
     版本/本体不匹配 422；跨主写权限 403；仅给 ontologyId 时锚定当前发布；
     空版本保持空画布（canvasVersion 不前进）
  2. 合并路径（B2）：勾选元素写进目标草稿版本快照而不是 fo_* live 表
     （live 行数不变、不新建 release、不重建图投影）；revision/snapshot_hash/
     change_summary 推进；语义层整体更新（semanticRevision 在既有值上 +1）；
     applied_version_id 与会话锚点回填；响应透出 versionId/versionNumber
  3. 目标草稿有 running 试跑 → 409（trial_running）；既有 passed 试跑置 stale
  4. 重复 apply 幂等（同名跳过，含草稿内未发布元素 —— 校验源为版本快照
     而非 live 表）
"""
from __future__ import annotations

import hashlib
import uuid

import pytest

from app.exploration import canvas as C
from app.exploration.document import canvas_fingerprint
from app.exploration.models import (
    ExplorationDraft,
    ExplorationSession,
)
from app.models.ontology_version import OntologyVersion
from app.models.user import User
from app.ontologies.formal_modeling.models import (
    ActionType,
    LinkType,
    ObjectType,
    OntologyFunction,
)
from app.ontologies.sentinels.models import Sentinel
from app.ontologies.versions.models import OntologyTrialRun
from app.ontologies.versions.snapshot_contract import (
    complete_snapshot,
    snapshot_hash,
)
from app.services.auth_service import hash_password

from tests.exploration.test_exploration import _make_draft

BASE = "/api/v2/exploration"


def _session(client, auth_headers, **body):
    r = client.post(f"{BASE}/sessions", headers=auth_headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]


@pytest.fixture
def session(client, auth_headers):
    return _session(client, auth_headers)


def _current_release(db, ontology_id: str) -> OntologyVersion:
    return db.query(OntologyVersion).filter_by(
        ontology_id=ontology_id, version_number="v0").one()


def _write_release_snapshot(db, ontology_id: str, snapshot: dict,
                            semantic: dict | None = None) -> OntologyVersion:
    release = _current_release(db, ontology_id)
    frozen = complete_snapshot(snapshot)
    release.snapshot_formal = frozen
    release.snapshot_hash = snapshot_hash(frozen)
    if semantic is not None:
        release.snapshot_semantic = semantic
    db.commit()
    return release


def _insert_draft_version(db, ontology_id: str, created_by: str,
                          snapshot: dict, *, number: str = "v0.1",
                          semantic: dict | None = None) -> OntologyVersion:
    frozen = complete_snapshot(snapshot)
    draft = OntologyVersion(
        id=str(uuid.uuid4()), ontology_id=ontology_id, version_number=number,
        version_label="既有草稿", node_kind="draft", lifecycle_status="editing",
        revision=0, snapshot_formal=frozen, snapshot_hash=snapshot_hash(frozen),
        snapshot_semantic=semantic, created_by=created_by,
    )
    db.add(draft)
    db.commit()
    return draft


def _order_snapshot() -> dict:
    return {"objectTypes": [{
        "id": "ot-order", "name": "Order", "displayName": "订单",
        "description": "客户订单", "primaryKey": "p1",
        "properties": [{"id": "p1", "name": "order_no", "displayName": "订单号",
                        "type": "string", "required": True}],
        "positionX": 0, "positionY": 0,
    }]}


def _live_counts(db, ontology_id: str) -> dict:
    return {
        model.__name__: db.query(model).filter(
            model.ontology_id == ontology_id).count()
        for model in (ObjectType, LinkType, ActionType, OntologyFunction, Sentinel)
    }


# ---------------------------------------------------------------- 会话绑定引导


def test_bound_session_prefers_semantic_canvas_over_projection(
        client, auth_headers, db, ontology):
    semantic_canvas = C.empty_canvas()
    semantic_canvas["objects"] = [{"name": "Order", "display_name": "订单"}]
    release = _write_release_snapshot(
        db, ontology["id"],
        {"objectTypes": [{**_order_snapshot()["objectTypes"][0],
                          "id": "ot-order", "name": "Legacy"}]},
        semantic={"canvas": semantic_canvas, "semanticRevision": 3},
    )
    created = _session(client, auth_headers, ontologyId=ontology["id"],
                       ontologyVersionId=release.id)
    assert created["ontologyId"] == ontology["id"]
    assert created["ontologyVersionId"] == release.id
    assert created["canvasVersion"] == 1

    data = client.get(f"{BASE}/sessions/{created['id']}/canvas",
                      headers=auth_headers).json()["data"]
    # 语义层画布优先：不出现结构快照反向投影的元素
    assert [o["name"] for o in data["canvas"]["objects"]] == ["Order"]

    listed = client.get(f"{BASE}/sessions", headers=auth_headers).json()["data"]
    row = next(s for s in listed if s["id"] == created["id"])
    assert row["ontologyId"] == ontology["id"]
    assert row["ontologyVersionId"] == release.id


def test_bound_session_falls_back_to_reverse_projection(
        client, auth_headers, db, ontology):
    release = _write_release_snapshot(db, ontology["id"], _order_snapshot())
    created = _session(client, auth_headers, ontologyVersionId=release.id)
    assert created["canvasVersion"] == 1
    data = client.get(f"{BASE}/sessions/{created['id']}/canvas",
                      headers=auth_headers).json()["data"]
    names = [o["name"] for o in data["canvas"]["objects"]]
    assert names == ["Order"]


def test_bound_session_with_empty_version_keeps_empty_canvas(
        client, auth_headers, db, ontology):
    release = _current_release(db, ontology["id"])
    created = _session(client, auth_headers, ontologyId=ontology["id"])
    # 仅给 ontologyId 时锚定当前发布；空版本无可引导内容 → 空画布现状
    assert created["ontologyVersionId"] == release.id
    assert created["canvasVersion"] == 0
    data = client.get(f"{BASE}/sessions/{created['id']}/canvas",
                      headers=auth_headers).json()["data"]
    assert all(not data["canvas"][key] for key in C.KIND_KEYS.values())


def test_bound_session_unknown_version_404(client, auth_headers, ontology):
    r = client.post(f"{BASE}/sessions", headers=auth_headers,
                    json={"ontologyId": ontology["id"],
                          "ontologyVersionId": "missing-version"})
    assert r.status_code == 404


def test_bound_session_version_ontology_mismatch_422(
        client, auth_headers, db, ontology):
    other = client.post("/api/v1/ontologies", headers=auth_headers,
                        json={"name": "另一个本体", "domain": "供应链"})
    assert other.status_code == 201, other.text
    release = _current_release(db, ontology["id"])
    r = client.post(f"{BASE}/sessions", headers=auth_headers,
                    json={"ontologyId": other.json()["data"]["id"],
                          "ontologyVersionId": release.id})
    assert r.status_code == 422


def test_bound_session_requires_ontology_write_access(
        client, auth_headers, db, ontology):
    editor = User(id=str(uuid.uuid4()), username="binding-editor",
                  email="binding-editor@test.local",
                  password_hash=hash_password("binding-editor-password"),
                  role="editor")
    db.add(editor)
    db.commit()
    token = client.post("/api/v1/auth/login", json={
        "username": "binding-editor", "password": "binding-editor-password",
    }).json()["data"]["access_token"]
    release = _current_release(db, ontology["id"])
    r = client.post(f"{BASE}/sessions",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"ontologyVersionId": release.id})
    # editor 非本体创建者 → 与草稿落地同一写权限惯例
    assert r.status_code == 403


# ---------------------------------------------------------------- B2：合并走版本正门


def test_merge_apply_writes_draft_snapshot_not_live_tables(
        client, auth_headers, session, db, ontology):
    oid = ontology["id"]
    release = _write_release_snapshot(
        db, oid, _order_snapshot(),
        semantic={"canvas": C.empty_canvas(), "semanticRevision": 5})
    live_before = _live_counts(db, oid)

    draft = _make_draft(client, auth_headers, session["id"], db,
                        target_ontology_id=oid)
    r = client.post(f"{BASE}/drafts/{draft['id']}/apply",
                    headers=auth_headers, json={})
    assert r.status_code == 200, r.text
    result = r.json()["data"]
    assert result["ontologyId"] == oid
    assert result["created"] == {"objectTypes": 2, "linkTypes": 1, "actions": 1,
                                 "functions": 1, "sentinels": 3}
    assert any("Order" in s["reason"] for s in result["skipped"])

    # 断点修复回归：live 表行数不变、不新建 release
    assert _live_counts(db, oid) == live_before
    assert db.query(OntologyVersion).filter_by(
        ontology_id=oid, node_kind="release").count() == 1

    target = db.query(OntologyVersion).filter_by(id=result["versionId"]).one()
    assert target.node_kind == "draft" and target.lifecycle_status == "editing"
    assert result["versionNumber"] == target.version_number
    assert target.parent_version_id == release.id
    merged = complete_snapshot(target.snapshot_formal)
    assert {o["name"] for o in merged["objectTypes"]} == {
        "Order", "Supplier", "Finance"}
    link = merged["linkTypes"][0]
    assert link["sourceObjectTypeId"] == "ot-order"
    # 三重闸门与血缘随快照元素保留
    assert all(f["enabled"] is False for f in merged["functions"])
    assert all(s["muted"] and not s["enabled"] and s["status"] == "draft"
               for s in merged["sentinels"])
    assert all((o.get("source") or {}).get("kind") == "business_exploration"
               for o in merged["objectTypes"] if o["name"] != "Order")

    # revision / snapshot_hash / change_summary 推进
    assert target.revision == 1
    assert target.snapshot_hash == snapshot_hash(merged)
    assert target.change_summary["total"]["added"] == 8

    # 语义层整体更新为文档同款内容，semanticRevision 在既有值上 +1
    semantic = target.snapshot_semantic
    assert semantic["semanticRevision"] == 6
    assert semantic["sourceSessionId"] == session["id"]
    assert semantic["sourceDocumentId"] == draft["documentId"]
    assert "_document_source" not in semantic["canvas"]
    assert semantic["canvasFingerprint"] == canvas_fingerprint(
        semantic["canvas"])
    assert semantic["documentFingerprint"] == hashlib.sha256(
        semantic["documentMd"].encode("utf-8")).hexdigest()

    # 草稿与会话锚点回填
    stored_draft = db.query(ExplorationDraft).filter_by(id=draft["id"]).one()
    assert stored_draft.status == "applied"
    assert stored_draft.applied_ontology_id == oid
    assert stored_draft.applied_version_id == target.id
    stored_session = db.query(ExplorationSession).filter_by(
        id=session["id"]).one()
    assert stored_session.ontology_id == oid
    assert stored_session.ontology_version_id == target.id


def test_merge_apply_reapply_is_idempotent_on_bound_draft(
        client, auth_headers, session, db, ontology):
    oid = ontology["id"]
    _write_release_snapshot(db, oid, _order_snapshot())
    draft = _make_draft(client, auth_headers, session["id"], db,
                        target_ontology_id=oid)
    first = client.post(f"{BASE}/drafts/{draft['id']}/apply",
                        headers=auth_headers, json={})
    assert first.status_code == 200, first.text
    version_id = first.json()["data"]["versionId"]

    second = client.post(f"{BASE}/drafts/{draft['id']}/apply",
                         headers=auth_headers, json={})
    assert second.status_code == 200, second.text
    result = second.json()["data"]
    # 会话锚点使二次应用合并进同一草稿版本；同名全部跳过（幂等收敛）
    assert result["versionId"] == version_id
    assert all(count == 0 for count in result["created"].values())
    assert result["skipped"]

    target = db.query(OntologyVersion).filter_by(id=version_id).one()
    assert target.revision == 2
    merged = complete_snapshot(target.snapshot_formal)
    for collection in ("objectTypes", "linkTypes", "actions", "functions",
                       "sentinels"):
        names = [item["name"] for item in merged[collection]]
        assert len(names) == len(set(names))
    assert _live_counts(db, oid) == {
        "ObjectType": 0, "LinkType": 0, "ActionType": 0,
        "OntologyFunction": 0, "Sentinel": 0,
    }


def test_merge_apply_skips_unpublished_names_from_snapshot(
        client, auth_headers, db, ontology, admin_user):
    """草稿版本内未发布元素参与同名跳过：校验/合并源是版本快照而非 live 表。"""
    oid = ontology["id"]
    target = _insert_draft_version(db, oid, admin_user.id, {
        "objectTypes": [{
            "id": "ot-supplier", "name": "Supplier", "displayName": "供应商",
            "primaryKey": "p1",
            "properties": [{"id": "p1", "name": "sname", "displayName": "名称",
                            "type": "string", "required": True}],
            "positionX": 0, "positionY": 0,
        }],
    })
    session = _session(client, auth_headers, ontologyId=oid,
                       ontologyVersionId=target.id)
    draft = _make_draft(client, auth_headers, session["id"], db,
                        target_ontology_id=oid)
    r = client.post(f"{BASE}/drafts/{draft['id']}/apply",
                    headers=auth_headers, json={})
    assert r.status_code == 200, r.text
    result = r.json()["data"]
    # Supplier 在 live 表中不存在，但在目标草稿快照中 → 跳过
    assert result["versionId"] == target.id
    assert result["created"]["objectTypes"] == 2     # Order + Finance
    assert any("Supplier" in s["reason"] for s in result["skipped"])
    merged = complete_snapshot(target.snapshot_formal)
    assert sorted(o["name"] for o in merged["objectTypes"]) == [
        "Finance", "Order", "Supplier"]
    assert _live_counts(db, oid) == {
        "ObjectType": 0, "LinkType": 0, "ActionType": 0,
        "OntologyFunction": 0, "Sentinel": 0,
    }


def test_merge_apply_rejected_while_trial_running(
        client, auth_headers, db, ontology, admin_user):
    oid = ontology["id"]
    target = _insert_draft_version(db, oid, admin_user.id, {})
    db.add(OntologyTrialRun(
        id=str(uuid.uuid4()), ontology_id=oid, version_id=target.id,
        revision=target.revision, snapshot_hash=target.snapshot_hash,
        status="running", created_by=admin_user.id,
    ))
    db.commit()
    session = _session(client, auth_headers, ontologyId=oid,
                       ontologyVersionId=target.id)
    draft = _make_draft(client, auth_headers, session["id"], db,
                        target_ontology_id=oid)
    r = client.post(f"{BASE}/drafts/{draft['id']}/apply",
                    headers=auth_headers, json={})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "trial_running"
    assert "试跑进行中" in detail["message"]
    # 写回未发生：快照与 revision 保持锁定
    db.expire_all()
    locked = db.query(OntologyVersion).filter_by(id=target.id).one()
    assert locked.revision == 0
    assert complete_snapshot(locked.snapshot_formal)["objectTypes"] == []


def test_merge_apply_stales_passed_trials(
        client, auth_headers, db, ontology, admin_user):
    oid = ontology["id"]
    target = _insert_draft_version(db, oid, admin_user.id, {})
    trial = OntologyTrialRun(
        id=str(uuid.uuid4()), ontology_id=oid, version_id=target.id,
        revision=target.revision, snapshot_hash=target.snapshot_hash,
        status="passed", created_by=admin_user.id,
    )
    db.add(trial)
    db.commit()
    session = _session(client, auth_headers, ontologyId=oid,
                       ontologyVersionId=target.id)
    draft = _make_draft(client, auth_headers, session["id"], db,
                        target_ontology_id=oid)
    r = client.post(f"{BASE}/drafts/{draft['id']}/apply",
                    headers=auth_headers, json={})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(OntologyTrialRun).filter_by(id=trial.id).one().status == "stale"
