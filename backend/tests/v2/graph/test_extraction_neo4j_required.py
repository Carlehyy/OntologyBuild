from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.entity import Entity
from app.models.extraction_task import ExtractionTask
from app.models.ontology import OntologyProject
from app.tasks.extraction import (
    _commit_truth_and_rebuild_projection,
    _sync_neo4j,
)


def _seed_task(db, admin_user, *, suffix: str) -> tuple[OntologyProject, ExtractionTask]:
    project = OntologyProject(
        id=f"ontology-extraction-{suffix}",
        name="提取事务测试",
        domain="test",
        created_by=admin_user.id,
        status="creating",
    )
    task = ExtractionTask(
        id=f"extraction-{suffix}",
        ontology_id=project.id,
        status="running",
        parameters={},
        progress={"stage": "saving results", "pct": 85},
    )
    db.add_all([project, task])
    db.commit()
    return project, task


def _observer(db):
    return sessionmaker(bind=db.get_bind())()


def test_extraction_rebuild_delegates_to_idempotent_mapping_projection():
    mapping_service = MagicMock()
    mapping_service._rebuild_neo4j_projection.return_value = True

    with patch(
        "app.ontologies.mappings.mapping_service.MappingService",
        return_value=mapping_service,
    ):
        _sync_neo4j(MagicMock(), "ont-1")

    mapping_service._rebuild_neo4j_projection.assert_called_once_with("ont-1")


def test_extraction_rebuild_fails_when_full_projection_is_not_ready():
    mapping_service = MagicMock()
    mapping_service._rebuild_neo4j_projection.return_value = False

    with patch(
        "app.ontologies.mappings.mapping_service.MappingService",
        return_value=mapping_service,
    ), pytest.raises(RuntimeError, match="neo4j_projection_rebuild_failed"):
        _sync_neo4j(MagicMock(), "ont-1")


def test_sql_truth_and_projecting_fence_commit_before_neo4j(
    db,
    admin_user,
):
    project, task = _seed_task(db, admin_user, suffix="ordered")
    db.add(Entity(
        id="entity-ordered",
        ontology_id=project.id,
        name_cn="已提交实体",
        type="Company",
    ))

    observed: list[tuple[str, str, int]] = []

    def rebuild(_db, ontology_id: str) -> None:
        observer = _observer(db)
        try:
            persisted_task = observer.get(ExtractionTask, task.id)
            entity_count = observer.query(Entity).filter(
                Entity.ontology_id == ontology_id,
            ).count()
            persisted_project = observer.get(OntologyProject, ontology_id)
            observed.append((
                persisted_task.status,
                persisted_project.status,
                entity_count,
            ))
        finally:
            observer.close()

    _commit_truth_and_rebuild_projection(
        db,
        task.id,
        project.id,
        rebuild_projection=rebuild,
    )

    assert observed == [("projecting", "creating", 1)]
    db.expire_all()
    persisted_task = db.get(ExtractionTask, task.id)
    assert persisted_task.status == "completed"
    assert persisted_task.error is None
    assert persisted_task.progress == {"stage": "done", "pct": 100}
    assert db.get(OntologyProject, project.id).status == "created"


def test_relational_commit_failure_never_touches_neo4j_and_rolls_back_truth(
    db,
    admin_user,
    monkeypatch,
):
    project, task = _seed_task(db, admin_user, suffix="commit-failure")
    db.add(Entity(
        id="entity-must-rollback",
        ontology_id=project.id,
        name_cn="不得残留",
        type="Company",
    ))

    real_commit = db.commit
    commit_calls = 0

    def fail_first_commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 1:
            raise RuntimeError("injected relational commit failure")
        return real_commit()

    monkeypatch.setattr(db, "commit", fail_first_commit)
    rebuild_calls: list[str] = []

    with pytest.raises(RuntimeError, match="injected relational commit failure"):
        _commit_truth_and_rebuild_projection(
            db,
            task.id,
            project.id,
            rebuild_projection=lambda _db, ontology_id: rebuild_calls.append(
                ontology_id,
            ),
        )

    assert rebuild_calls == []
    observer = _observer(db)
    try:
        assert observer.get(Entity, "entity-must-rollback") is None
        persisted_task = observer.get(ExtractionTask, task.id)
        assert persisted_task.status == "failed"
        assert persisted_task.progress == {
            "stage": "relational_commit_failed",
            "pct": 95,
        }
        assert observer.get(OntologyProject, project.id).status == "creating"
    finally:
        observer.close()


def test_projection_failure_keeps_committed_truth_fenced_and_retry_repairs_it(
    db,
    admin_user,
):
    project, task = _seed_task(db, admin_user, suffix="projection-failure")
    db.add(Entity(
        id="entity-committed-truth",
        ontology_id=project.id,
        name_cn="关系型真相",
        type="Company",
    ))

    def fail_rebuild(_db, ontology_id: str) -> None:
        observer = _observer(db)
        try:
            assert observer.get(Entity, "entity-committed-truth") is not None
            assert observer.get(ExtractionTask, task.id).status == "projecting"
        finally:
            observer.close()
        raise RuntimeError(f"injected projection failure:{ontology_id}")

    with pytest.raises(RuntimeError, match="injected projection failure"):
        _commit_truth_and_rebuild_projection(
            db,
            task.id,
            project.id,
            rebuild_projection=fail_rebuild,
        )

    observer = _observer(db)
    try:
        assert observer.get(Entity, "entity-committed-truth") is not None
        failed_task = observer.get(ExtractionTask, task.id)
        assert failed_task.status == "failed"
        assert failed_task.progress == {"stage": "projection_failed", "pct": 95}
        assert observer.get(OntologyProject, project.id).status == "creating"
    finally:
        observer.close()

    repaired_states: list[str] = []

    def repair(_db, _ontology_id: str) -> None:
        observer = _observer(db)
        try:
            repaired_states.append(observer.get(ExtractionTask, task.id).status)
        finally:
            observer.close()

    _commit_truth_and_rebuild_projection(
        db,
        task.id,
        project.id,
        rebuild_projection=repair,
    )

    assert repaired_states == ["projecting"]
    db.expire_all()
    repaired_task = db.get(ExtractionTask, task.id)
    assert repaired_task.status == "completed"
    assert repaired_task.error is None
    assert db.get(OntologyProject, project.id).status == "created"
