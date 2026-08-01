"""Durable projection fences for retained legacy SQL graph writers."""
from __future__ import annotations

from contextlib import contextmanager

from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.relation import Relation


def _candidate_payload() -> dict:
    return {
        "entities": [
            {"name_cn": "供应商", "name_en": "Supplier", "type": "Company"},
            {"name_cn": "订单", "name_en": "Order", "type": "Order"},
        ],
        "relations": [
            {"source": "供应商", "target": "订单", "type": "FULFILLS"},
        ],
    }


def test_candidate_approval_commits_one_truth_and_closes_projection_fence(
    client, auth_headers, ontology, db, monkeypatch,
):
    from app.data_channel.transforms import router as transforms_router

    oid = ontology["id"]
    events: list[str] = []
    lock_held = {"value": False}
    original_rebuild = transforms_router.rebuild_after_commit

    @contextmanager
    def observed_lock(_db, target_ontology_id):
        assert target_ontology_id == oid
        lock_held["value"] = True
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")
            lock_held["value"] = False

    def observed_rebuild(session, target_ontology_id):
        assert lock_held["value"] is True
        events.append("rebuild")
        return original_rebuild(session, target_ontology_id)

    monkeypatch.setattr(
        transforms_router,
        "_ontology_build_lock",
        observed_lock,
    )
    monkeypatch.setattr(
        transforms_router,
        "rebuild_after_commit",
        observed_rebuild,
    )
    response = client.post(
        f"/api/v2/ontologies/{oid}/candidates/approve",
        headers=auth_headers,
        json=_candidate_payload(),
    )

    assert response.status_code == 200, response.text
    assert db.query(Entity).filter_by(ontology_id=oid).count() == 2
    relation = db.query(Relation).filter_by(ontology_id=oid).one()
    assert relation.source_entity != relation.target_entity
    project = db.query(OntologyProject).filter_by(id=oid).one()
    assert project.projection_status == "ready"
    assert project.projection_error is None
    assert events == ["lock-enter", "rebuild", "lock-exit"]


def test_candidate_approval_keeps_sql_truth_when_projection_rebuild_fails(
    client, auth_headers, ontology, db, monkeypatch,
):
    from app.data_channel.transforms import router as transforms_router
    from app.ontologies import projection_state

    oid = ontology["id"]

    def fail_rebuild(session, ontology_id):
        return projection_state.rebuild_after_commit(
            session,
            ontology_id,
            rebuild=lambda _ontology_id: False,
            run_in_test=True,
        )

    monkeypatch.setattr(
        transforms_router,
        "rebuild_after_commit",
        fail_rebuild,
    )
    response = client.post(
        f"/api/v2/ontologies/{oid}/candidates/approve",
        headers=auth_headers,
        json=_candidate_payload(),
    )

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ontology_projection_failed"
    db.expire_all()
    assert db.query(Entity).filter_by(ontology_id=oid).count() == 2
    assert db.query(Relation).filter_by(ontology_id=oid).count() == 1
    project = db.query(OntologyProject).filter_by(id=oid).one()
    assert project.projection_status == "failed"
    assert "incomplete" in (project.projection_error or "")
