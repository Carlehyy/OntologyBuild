"""Collector type ownership and edge-direction guardrails."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.models.ontology import OntologyProject
from app.models.ontology_formal import (
    LinkInstance,
    LinkType,
    ObjectInstance,
    ObjectType,
    PropertyFact,
)


def _object_type(type_id: str, ontology_id: str, name: str) -> ObjectType:
    return ObjectType(
        id=type_id,
        ontology_id=ontology_id,
        name=name,
        display_name=name,
        properties=[],
        interfaces=[],
    )


def _link_type(
    link_id: str,
    ontology_id: str,
    source_type_id: str,
    target_type_id: str,
) -> LinkType:
    return LinkType(
        id=link_id,
        ontology_id=ontology_id,
        name=link_id,
        display_name=link_id,
        source_object_type_id=source_type_id,
        target_object_type_id=target_type_id,
        properties=[],
    )


def _runtime_counts(db, ontology_id: str) -> tuple[int, int, int]:
    return (
        db.query(ObjectInstance).filter_by(ontology_id=ontology_id).count(),
        db.query(LinkInstance).filter_by(ontology_id=ontology_id).count(),
        db.query(PropertyFact).filter_by(ontology_id=ontology_id).count(),
    )


def _item() -> dict:
    return {
        "id": "news-1",
        "title": "AI platform release",
        "source": "Example News",
        "category": "industry",
        "selected": True,
        "score": 91,
    }


@pytest.mark.parametrize(
    "source_object_type_id,source_link_type_id",
    [
        ("collector-source", None),
        (None, "collector-source-link"),
    ],
)
def test_collector_source_type_ids_must_be_supplied_as_a_pair(
    client,
    auth_headers,
    ontology,
    db,
    monkeypatch,
    source_object_type_id,
    source_link_type_id,
):
    from app.data_channel.connections import collectors_router

    oid = ontology["id"]
    db.add(_object_type("collector-main", oid, "CollectorMain"))
    db.commit()
    fetch_calls: list[str] = []

    def fetch_items(**_kwargs):
        fetch_calls.append("fetch")
        return {"items": [_item()]}

    monkeypatch.setattr(collectors_router.aihot, "fetch_items", fetch_items)
    before = _runtime_counts(db, oid)
    response = client.post(
        f"/api/v2/collectors/aihot/collect/{oid}",
        headers=auth_headers,
        json={
            "object_type_id": "collector-main",
            "source_object_type_id": source_object_type_id,
            "source_link_type_id": source_link_type_id,
        },
    )

    assert response.status_code == 422, response.text
    assert "必须同时提供或同时省略" in response.text
    assert fetch_calls == []
    db.expire_all()
    assert _runtime_counts(db, oid) == before


@pytest.mark.parametrize(
    "case,expected_code",
    [
        ("cross_ontology_object_type", "collector_source_object_type_invalid"),
        ("cross_ontology_link_type", "collector_source_link_type_invalid"),
        ("reversed_link", "collector_source_link_endpoints_invalid"),
        ("wrong_target", "collector_source_link_endpoints_invalid"),
    ],
)
def test_collector_rejects_foreign_or_misdirected_source_types_before_writes(
    client,
    auth_headers,
    ontology,
    db,
    monkeypatch,
    case,
    expected_code,
):
    from app.data_channel.connections import collectors_router

    oid = ontology["id"]
    other_response = client.post(
        "/api/v1/ontologies",
        headers=auth_headers,
        json={"name": f"Collector other {case}", "domain": "其他"},
    )
    assert other_response.status_code == 201, other_response.text
    other_oid = other_response.json()["data"]["id"]

    db.add_all([
        _object_type("collector-main", oid, "CollectorMain"),
        _object_type("collector-source", oid, "CollectorSource"),
        _object_type("collector-wrong", oid, "CollectorWrong"),
        _object_type("collector-foreign-source", other_oid, "ForeignSource"),
    ])

    source_type_id = "collector-source"
    link_type_id = "collector-source-link"
    if case == "cross_ontology_object_type":
        source_type_id = "collector-foreign-source"
        db.add(_link_type(
            link_type_id, oid, "collector-main", "collector-source"))
    elif case == "cross_ontology_link_type":
        link_type_id = "collector-foreign-link"
        db.add(_link_type(
            link_type_id, other_oid, "collector-main", "collector-source"))
    elif case == "reversed_link":
        db.add(_link_type(
            link_type_id, oid, "collector-source", "collector-main"))
    else:
        db.add(_link_type(
            link_type_id, oid, "collector-main", "collector-wrong"))
    db.commit()

    fetch_calls: list[str] = []

    def fetch_items(**_kwargs):
        fetch_calls.append("fetch")
        return {"items": [_item()]}

    monkeypatch.setattr(collectors_router.aihot, "fetch_items", fetch_items)
    before = _runtime_counts(db, oid)
    response = client.post(
        f"/api/v2/collectors/aihot/collect/{oid}",
        headers=auth_headers,
        json={
            "object_type_id": "collector-main",
            "source_object_type_id": source_type_id,
            "source_link_type_id": link_type_id,
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == expected_code
    assert fetch_calls == ["fetch"]
    db.expire_all()
    assert _runtime_counts(db, oid) == before


def test_collector_accepts_valid_source_types_with_no_remote_items(
    client, auth_headers, ontology, db, monkeypatch,
):
    from app.data_channel.connections import collectors_router

    oid = ontology["id"]
    db.add_all([
        _object_type("collector-main", oid, "CollectorMain"),
        _object_type("collector-source", oid, "CollectorSource"),
        _link_type(
            "collector-source-link",
            oid,
            "collector-main",
            "collector-source",
        ),
    ])
    db.commit()
    monkeypatch.setattr(
        collectors_router.aihot, "fetch_items", lambda **_kwargs: {"items": []})

    response = client.post(
        f"/api/v2/collectors/aihot/collect/{oid}",
        headers=auth_headers,
        json={
            "object_type_id": "collector-main",
            "source_object_type_id": "collector-source",
            "source_link_type_id": "collector-source-link",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "collected": 0,
        "created": 0,
        "updated": 0,
        "sources": 0,
    }
    db.expire_all()
    assert _runtime_counts(db, oid) == (0, 0, 0)


def test_collector_valid_source_types_create_objects_edge_and_facts(
    client, auth_headers, ontology, db, monkeypatch,
):
    from app.data_channel.connections import collectors_router

    oid = ontology["id"]
    db.add_all([
        _object_type("collector-main", oid, "CollectorMain"),
        _object_type("collector-source", oid, "CollectorSource"),
        _link_type(
            "collector-source-link",
            oid,
            "collector-main",
            "collector-source",
        ),
    ])
    db.commit()
    monkeypatch.setattr(
        collectors_router.aihot,
        "fetch_items",
        lambda **_kwargs: {"items": [_item()]},
    )

    response = client.post(
        f"/api/v2/collectors/aihot/collect/{oid}",
        headers=auth_headers,
        json={
            "object_type_id": "collector-main",
            "source_object_type_id": "collector-source",
            "source_link_type_id": "collector-source-link",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "collected": 1,
        "created": 1,
        "updated": 0,
        "sources": 1,
    }
    db.expire_all()
    objects = db.query(ObjectInstance).filter_by(ontology_id=oid).all()
    assert {obj.object_type_id for obj in objects} == {
        "collector-main",
        "collector-source",
    }
    by_type = {obj.object_type_id: obj for obj in objects}
    link = db.query(LinkInstance).filter_by(ontology_id=oid).one()
    assert link.link_type_id == "collector-source-link"
    assert link.source_object_id == by_type["collector-main"].id
    assert link.target_object_id == by_type["collector-source"].id
    fact_kinds = {
        fact.kind
        for fact in db.query(PropertyFact).filter_by(ontology_id=oid).all()
    }
    assert {"object", "property", "link"} <= fact_kinds
    project = db.query(OntologyProject).filter_by(id=oid).one()
    assert project.projection_status == "ready"


def test_collector_commits_truth_and_persists_failed_projection_fence(
    client, auth_headers, ontology, db, monkeypatch,
):
    from app.data_channel.connections import collectors_router
    from app.ontologies import projection_state

    oid = ontology["id"]
    lock_held = {"value": False}

    @contextmanager
    def observed_lock(_db, target_ontology_id):
        assert target_ontology_id == oid
        lock_held["value"] = True
        try:
            yield
        finally:
            lock_held["value"] = False

    db.add(_object_type("collector-main", oid, "CollectorMain"))
    db.commit()
    monkeypatch.setattr(
        collectors_router.aihot,
        "fetch_items",
        lambda **_kwargs: {"items": [_item()]},
    )

    def fail_rebuild(session, ontology_id):
        assert lock_held["value"] is True
        return projection_state.rebuild_after_commit(
            session,
            ontology_id,
            rebuild=lambda _ontology_id: False,
            run_in_test=True,
        )

    monkeypatch.setattr(
        collectors_router,
        "rebuild_after_commit",
        fail_rebuild,
    )
    monkeypatch.setattr(
        collectors_router,
        "_ontology_build_lock",
        observed_lock,
    )
    response = client.post(
        f"/api/v2/collectors/aihot/collect/{oid}",
        headers=auth_headers,
        json={"object_type_id": "collector-main"},
    )

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ontology_projection_failed"
    db.expire_all()
    assert db.query(ObjectInstance).filter_by(ontology_id=oid).count() == 1
    project = db.query(OntologyProject).filter_by(id=oid).one()
    assert project.projection_status == "failed"
    assert "incomplete" in (project.projection_error or "")
    assert lock_held["value"] is False
