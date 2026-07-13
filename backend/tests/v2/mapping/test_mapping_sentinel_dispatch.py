"""Mapping CDC is released only after the projection is marked applied."""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from app.models.ontology import OntologyProject
from app.models.ontology_formal import ObjectInstance, ObjectType
from app.models.v2.mapping import OntologyMapping
from app.ontologies.sentinels.cdc import (
    CAPTURE_SUPPRESSED_KEY,
    SUPPRESS_KEY,
    dispatch_captured_changes,
    discard_captured_changes,
    register_cdc,
)


def test_captured_mapping_change_observes_applied_fence(db, monkeypatch):
    register_cdc()
    project = OntologyProject(
        id="ontology-cdc", name="CDC", domain="test", status="published",
        created_by="user-not-required-by-sqlite",
    )
    object_type = ObjectType(
        id="object-type-cdc", ontology_id=project.id,
        name="Record", display_name="记录", properties=[], interfaces=[],
        position_x=0, position_y=0,
    )
    mapping = OntologyMapping(
        id="mapping-cdc", ontology_id=project.id,
        curated_dataset_id=None, entity_class="Record",
        field_mapping={}, status="projecting",
    )
    db.add_all([project, object_type, mapping])
    db.commit()

    db.info[SUPPRESS_KEY] = True
    db.info[CAPTURE_SUPPRESSED_KEY] = True
    db.add(ObjectInstance(
        id="instance-cdc", ontology_id=project.id,
        object_type_id=object_type.id, properties={"state": "changed"},
        computed={}, source="pipeline", external_id="row-1",
    ))
    db.commit()

    seen_statuses: list[str] = []

    def fake_run_for_change(run_db, ontology_id, object_type_id, changed_keys):
        seen_statuses.append(run_db.query(OntologyMapping).filter_by(
            id=mapping.id).one().status)
        return {"evaluated": 1, "fired": 1, "firings": []}

    from app.services.sentinel import engine as sentinel_engine
    monkeypatch.setattr(sentinel_engine, "run_for_change", fake_run_for_change)
    monkeypatch.setattr(
        "app.database.SessionLocal", sessionmaker(bind=db.get_bind()))

    # The first commit captured the object delta but did not evaluate it.
    assert seen_statuses == []
    mapping.status = "applied"
    db.commit()
    result = dispatch_captured_changes(db)

    assert seen_statuses == ["applied"]
    assert result["evaluated"] == 1 and result["fired"] == 1
    assert db.info.get(SUPPRESS_KEY) is None


def test_failed_mapping_capture_is_discarded_before_session_reuse(db):
    db.info[SUPPRESS_KEY] = True
    db.info[CAPTURE_SUPPRESSED_KEY] = True

    discard_captured_changes(db)

    assert db.info.get(SUPPRESS_KEY) is None
    assert db.info.get(CAPTURE_SUPPRESSED_KEY) is None
    assert dispatch_captured_changes(db) == {
        "evaluated": 0, "fired": 0, "errors": [],
    }
