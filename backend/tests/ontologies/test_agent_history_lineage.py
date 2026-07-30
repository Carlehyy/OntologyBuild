"""Release-lineage semantics for the Agent object-history tool."""
from datetime import datetime, timedelta, timezone

from app.models.ontology import OntologyProject
from app.models.ontology_formal import ObjectInstance, ObjectType, PropertyFact
from app.ontologies.agent_runtime.boundary import AgentScope, get_or_create_profile
from app.ontologies.agent_runtime.toolkit import ToolRunner
from app.ontologies.versions.models import OntologyVersion


def _root(db, ontology_id: str) -> OntologyVersion:
    project = db.query(OntologyProject).filter_by(id=ontology_id).one()
    return db.query(OntologyVersion).filter_by(
        id=project.current_release_id).one()


def _release(
        db, root: OntologyVersion, *, release_id: str, version: str,
        parent_id: str | None, ordinary: bool) -> OntologyVersion:
    promoted_from_id = None
    if ordinary:
        promoted_from_id = f"draft-{release_id}"
        db.add(OntologyVersion(
            id=promoted_from_id,
            ontology_id=root.ontology_id,
            version_number=f"{version}.1",
            parent_version_id=parent_id,
            base_release_id=parent_id,
            node_kind="draft",
            lifecycle_status="superseded",
            revision=1,
            created_by=root.created_by,
        ))
        db.flush()
    row = OntologyVersion(
        id=release_id,
        ontology_id=root.ontology_id,
        version_number=version,
        parent_version_id=parent_id,
        base_release_id=release_id,
        promoted_from_id=promoted_from_id,
        node_kind="release",
        lifecycle_status="released",
        revision=0,
        created_by=root.created_by,
    )
    db.add(row)
    db.flush()
    return row


def _fact(
        db, *, ontology_id: str, release_id: str, value,
        at: datetime, present: bool | None = True, source: str = "pipeline",
        fact_id: str | None = None) -> PropertyFact:
    wrapped = {"v": value}
    if present is not None:
        wrapped["present"] = present
    fact = PropertyFact(
        id=fact_id,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        ontology_version=release_id,
        instance_id="history-object",
        object_type_id="history-type",
        property_name="status",
        value=wrapped,
        kind="property",
        source=source,
        seq=1,
        recorded_at=at,
    )
    db.add(fact)
    return fact


def _runner(
        db, ontology_id: str, current_release_id: str | None,
        *, value="pending") -> ToolRunner:
    project = db.query(OntologyProject).filter_by(id=ontology_id).one()
    db.add(ObjectType(
        id="history-type",
        ontology_id=ontology_id,
        name="HistoryObject",
        display_name="历史对象",
        primary_key="id",
        properties=[
            {"id": "id", "name": "id", "displayName": "编号",
             "type": "string", "required": True},
            {"id": "status", "name": "status", "displayName": "状态",
             "type": "string", "required": False},
        ],
    ))
    db.add(ObjectInstance(
        id="history-object",
        ontology_id=ontology_id,
        ontology_release_id=current_release_id,
        object_type_id="history-type",
        properties={"id": "O-1", "status": value},
        computed={},
        source="pipeline",
    ))
    profile = get_or_create_profile(db, ontology_id)
    db.commit()
    scope = AgentScope(db, project, profile)
    scope.release_id = current_release_id
    return ToolRunner(db, scope)


def test_ordinary_current_fact_is_the_only_authority(
        db, ontology):
    oid = ontology["id"]
    root = _root(db, oid)
    current = _release(
        db, root, release_id="release-v1", version="v1",
        parent_id=root.id, ordinary=True)
    now = datetime.now(timezone.utc)
    current_fact = _fact(
        db, ontology_id=oid, release_id=current.id, value="paid",
        at=now, source="action://mark-paid", fact_id="fact-current")
    db.commit()

    result = _runner(db, oid, current.id, value="paid").run(
        "get_object_history",
        {"instance_id": "history-object", "property_name": "status"},
    )

    assert result["releaseContext"] == {
        "currentReleaseId": current.id,
        "authorityReleaseIds": [current.id],
        "baselineReleaseId": current.id,
        "lineageComplete": True,
    }
    assert [item["ontologyReleaseId"] for item in result["facts"]] == [
        current_fact.ontology_release_id]
    assert result["facts"][0]["authoritative"] is True
    assert result["facts"][0]["inherited"] is False
    assert "baselineAdoption" not in result


def test_ordinary_noop_keeps_origin_non_authoritative_and_excludes_sibling(
        db, ontology):
    oid = ontology["id"]
    root = _root(db, oid)
    current = _release(
        db, root, release_id="release-v1", version="v1",
        parent_id=root.id, ordinary=True)
    sibling = _release(
        db, root, release_id="release-sibling", version="v9",
        parent_id=root.id, ordinary=True)
    now = datetime.now(timezone.utc)
    _fact(
        db, ontology_id=oid, release_id=root.id, value="pending",
        at=now, fact_id="fact-origin")
    _fact(
        db, ontology_id=oid, release_id=sibling.id, value="pending",
        at=now + timedelta(seconds=10), source="action://sibling",
        fact_id="fact-sibling")
    db.commit()

    result = _runner(db, oid, current.id).run(
        "get_object_history",
        {"instance_id": "history-object", "property_name": "status"},
    )

    assert result["facts"] == []
    assert result["baselineAdoption"] == {
        "releaseId": current.id,
        "promotedFromId": current.promoted_from_id,
        "objectPresent": True,
        "properties": [{
            "property": "status", "value": "pending", "present": True,
        }],
        "authoritative": True,
        "metadataComplete": False,
    }
    assert len(result["preBaselineOrigin"]) == 1
    assert result["preBaselineOrigin"][0]["ontologyReleaseId"] == root.id
    assert result["preBaselineOrigin"][0]["authoritative"] is False
    assert result["preBaselineOrigin"][0]["adoptedByReleaseId"] == current.id


def test_rollback_inherits_direct_lineage_and_limit_keeps_global_sort(
        db, ontology):
    oid = ontology["id"]
    root = _root(db, oid)
    baseline = _release(
        db, root, release_id="release-v1", version="v1",
        parent_id=root.id, ordinary=True)
    rollback_one = _release(
        db, root, release_id="release-rb1", version="v2",
        parent_id=baseline.id, ordinary=False)
    rollback_two = _release(
        db, root, release_id="release-rb2", version="v3",
        parent_id=rollback_one.id, ordinary=False)
    sibling = _release(
        db, root, release_id="release-other", version="v8",
        parent_id=baseline.id, ordinary=False)
    now = datetime.now(timezone.utc)
    _fact(
        db, ontology_id=oid, release_id=baseline.id, value="baseline",
        at=now, fact_id="fact-baseline")
    inherited_action = _fact(
        db, ontology_id=oid, release_id=rollback_one.id, value="review",
        at=now + timedelta(seconds=1), source="action://review",
        fact_id="fact-rb1")
    current_action = _fact(
        db, ontology_id=oid, release_id=rollback_two.id, value="approved",
        at=now + timedelta(seconds=2), source="action://approve",
        fact_id="fact-rb2")
    _fact(
        db, ontology_id=oid, release_id=sibling.id, value="sibling",
        at=now + timedelta(seconds=3), fact_id="fact-sibling")
    db.commit()

    runner = _runner(db, oid, rollback_two.id, value="approved")
    full = runner.run(
        "get_object_history",
        {"instance_id": "history-object", "property_name": "status"},
    )
    limited = runner.run(
        "get_object_history",
        {
            "instance_id": "history-object",
            "property_name": "status",
            "limit": 2,
        },
    )

    assert full["releaseContext"] == {
        "currentReleaseId": rollback_two.id,
        "authorityReleaseIds": [
            rollback_two.id, rollback_one.id, baseline.id],
        "baselineReleaseId": baseline.id,
        "lineageComplete": True,
    }
    assert [item["ontologyReleaseId"] for item in full["facts"]] == [
        rollback_two.id, rollback_one.id, baseline.id]
    assert [item["ontologyReleaseId"] for item in limited["facts"]] == [
        current_action.ontology_release_id,
        inherited_action.ontology_release_id,
    ]
    assert [item["inherited"] for item in limited["facts"]] == [False, True]


def test_history_distinguishes_tombstone_from_explicit_null(
        db, ontology):
    oid = ontology["id"]
    root = _root(db, oid)
    baseline = _release(
        db, root, release_id="release-v1", version="v1",
        parent_id=root.id, ordinary=True)
    rollback = _release(
        db, root, release_id="release-rb", version="v2",
        parent_id=baseline.id, ordinary=False)
    now = datetime.now(timezone.utc)
    _fact(
        db, ontology_id=oid, release_id=baseline.id, value=None,
        present=True, at=now, fact_id="fact-explicit-null")
    _fact(
        db, ontology_id=oid, release_id=rollback.id, value=None,
        present=False, at=now + timedelta(seconds=1),
        fact_id="fact-removed")
    db.commit()

    result = _runner(db, oid, rollback.id, value=None).run(
        "get_object_history",
        {"instance_id": "history-object", "property_name": "status"},
    )

    assert [(item["value"], item["present"]) for item in result["facts"]] == [
        (None, False), (None, True)]


def test_broken_rollback_lineage_fails_closed_to_current_release(
        db, ontology):
    oid = ontology["id"]
    root = _root(db, oid)
    rollback_one = _release(
        db, root, release_id="release-rb1", version="v1",
        parent_id=root.id, ordinary=False)
    rollback_two = _release(
        db, root, release_id="release-rb2", version="v2",
        parent_id=rollback_one.id, ordinary=False)
    rollback_one.parent_version_id = rollback_two.id
    now = datetime.now(timezone.utc)
    inherited = _fact(
        db, ontology_id=oid, release_id=rollback_one.id, value="unsafe",
        at=now, fact_id="fact-partial")
    current = _fact(
        db, ontology_id=oid, release_id=rollback_two.id, value="safe",
        at=now + timedelta(seconds=1), fact_id="fact-safe")
    db.commit()

    result = _runner(db, oid, rollback_two.id, value="safe").run(
        "get_object_history",
        {"instance_id": "history-object", "property_name": "status"},
    )

    assert result["releaseContext"] == {
        "currentReleaseId": rollback_two.id,
        "authorityReleaseIds": [rollback_two.id],
        "baselineReleaseId": None,
        "lineageComplete": False,
    }
    assert [item["ontologyReleaseId"] for item in result["facts"]] == [
        current.ontology_release_id]
    assert inherited.ontology_release_id not in {
        item["ontologyReleaseId"] for item in result["facts"]}


def test_unscoped_history_preserves_legacy_output_contract(
        db, ontology):
    oid = ontology["id"]
    root = _root(db, oid)
    now = datetime.now(timezone.utc)
    _fact(
        db, ontology_id=oid, release_id=root.id, value="pending",
        at=now, fact_id="fact-unscoped")
    db.commit()

    result = _runner(db, oid, None).run(
        "get_object_history",
        {"instance_id": "history-object", "property_name": "status"},
    )

    assert len(result["facts"]) == 1
    assert "releaseContext" not in result
    assert "baselineAdoption" not in result
    assert "ontologyReleaseId" not in result["facts"][0]
    assert result["facts"][0]["present"] is True
