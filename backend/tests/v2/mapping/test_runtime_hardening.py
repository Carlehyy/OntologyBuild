"""Mapping 生产执行边界：归属、血缘、全量读取与失败状态。"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.ontology import OntologyProject
from app.models.ontology_version import OntologyVersion
from app.models.v2.dataset import Dataset
from app.models.v2.mapping import OntologyLinkMapping, OntologyMapping
from app.ontologies.mappings.router import (
    CreateMappingRequest,
    LinkMappingCreate,
    UpdateMappingRequest,
    apply_mapping as raw_apply_mapping,
    apply_mapping_from_dataset,
    create_link_mapping,
    create_mapping,
    delete_link_mapping,
    delete_mapping,
    update_mapping,
)
from app.services.v2.mapping.mapping_service import (
    MappingApplyError,
    MappingSentinelDispatchError,
    MappingSourceError,
    MappingService,
    load_mapping_source_rows,
)
from app.data_channel.datasets.service import DatasetService
from app.services.v2.curated.review_service import ReviewService
from app.ontologies.mappings.mapping_service import _ontology_build_lock


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, bucket, key, data, content_type=""):
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data
        return uri

    def get_object(self, uri):
        return self.objects[uri]

    def delete_object(self, uri):
        self.objects.pop(uri, None)


@pytest.fixture
def lake_storage(monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(
        "app.data_channel.datasets.service.get_storage_service", lambda: storage)
    return storage


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


def _source_graph(db, admin_user, *, rows: list[dict], kind: str = "structured"):
    ontology = OntologyProject(
        name=f"mapping-hardening-{uuid.uuid4().hex[:8]}",
        domain="test", created_by=admin_user.id)
    ds_id = str(uuid.uuid4())
    dataset = Dataset(
        id=ds_id, name=f"source-{uuid.uuid4().hex[:8]}", kind=kind,
        schema_json={"primary_key": "id"})
    db.add_all([ontology, dataset])
    db.commit()
    DatasetService(db).create_version(
        dataset.id, _csv_bytes(rows), rowcount=len(rows))
    mapping = OntologyMapping(
        ontology_id=ontology.id, curated_dataset_id=dataset.id,
        entity_class="SourceRow", field_mapping={"__primary_key__": "id"},
        status="draft")
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return ontology, dataset, mapping


def test_postgres_mapping_build_lock_spans_business_commits_on_dedicated_connection():
    calls: list[tuple[object, str, dict]] = []

    class Result:
        @staticmethod
        def scalar():
            return True

    class FakeConnection:
        def __init__(self):
            self.closed = False
            self.commits = 0
            self.invalidated = False

        def execute(self, statement, params):
            calls.append((self, str(statement), params))
            return Result()

        def commit(self):
            self.commits += 1

        def invalidate(self):
            self.invalidated = True

        def close(self):
            self.closed = True

    connection = FakeConnection()

    class FakeEngine:
        dialect = SimpleNamespace(name="postgresql")

        @staticmethod
        def connect():
            return connection

    class FakeSession:
        commits = 0

        @staticmethod
        def get_bind():
            return FakeEngine()

        @staticmethod
        def execute(*_args, **_kwargs):
            raise AssertionError(
                "advisory lock must not use the business Session")

        def commit(self):
            self.commits += 1

    session = FakeSession()
    with _ontology_build_lock(session, "ontology-dedicated-lock"):
        # ``build_all`` commits relational truth before rebuilding the external
        # query projections; the advisory owner must survive both commits.
        session.commit()
        # Publication/query-projection helpers may participate in the same
        # boundary. Re-entry in one thread must not open a second PostgreSQL
        # connection and deadlock on its own session lock.
        with _ontology_build_lock(session, "ontology-dedicated-lock"):
            session.commit()

    assert session.commits == 2
    assert len(calls) == 2
    assert "pg_advisory_lock" in calls[0][1]
    assert "pg_advisory_unlock" in calls[1][1]
    assert calls[0][2] == calls[1][2] == {
        "key": "ontology-mapping-build:ontology-dedicated-lock",
    }
    assert connection.commits == 2
    assert connection.invalidated is False
    assert connection.closed is True


def test_postgres_mapping_build_lock_never_waits_on_business_pool(monkeypatch):
    from app.ontologies import runtime_fence
    from sqlalchemy.pool import NullPool

    calls: list[tuple[object, str, dict]] = []

    class Result:
        @staticmethod
        def scalar():
            return True

    class FakeConnection:
        def execute(self, statement, params):
            calls.append((self, str(statement), params))
            return Result()

        @staticmethod
        def commit():
            return None

        @staticmethod
        def invalidate():
            return None

        @staticmethod
        def close():
            return None

    connection = FakeConnection()
    source_creator = object()

    class DedicatedEngine:
        @staticmethod
        def connect():
            return connection

    class BusinessEngine:
        dialect = SimpleNamespace(name="postgresql")
        url = object()
        pool = SimpleNamespace(_creator=source_creator)

        @staticmethod
        def connect():
            raise AssertionError("business pool must not provide advisory owner")

    business_engine = BusinessEngine()

    class FakeSession:
        @staticmethod
        def get_bind():
            return business_engine

    created: list[tuple[object, dict]] = []

    def fake_create_engine(url, **kwargs):
        created.append((url, kwargs))
        return DedicatedEngine()

    monkeypatch.setattr(runtime_fence, "create_engine", fake_create_engine)
    runtime_fence._dispose_advisory_engines()

    with _ontology_build_lock(FakeSession(), "ontology-null-pool-lock"):
        pass

    assert len(created) == 1
    assert created[0][0] is business_engine.url
    assert created[0][1]["creator"] is source_creator
    assert created[0][1]["poolclass"] is NullPool
    assert len(calls) == 2


def test_postgres_mapping_build_lock_bounds_and_disposes_dedicated_connections(
    monkeypatch,
):
    from app.ontologies import runtime_fence

    runtime_fence._dispose_advisory_engines()
    monkeypatch.setattr(
        runtime_fence,
        "_ADVISORY_CONNECTION_SLOTS",
        threading.BoundedSemaphore(2),
    )

    state_guard = threading.Lock()
    two_connections_open = threading.Event()
    release_owners = threading.Event()
    start_barrier = threading.Barrier(5)
    state = {
        "active": 0,
        "maximum": 0,
        "connects": 0,
        "disposed": 0,
    }

    class Result:
        @staticmethod
        def scalar():
            return True

    class FakeConnection:
        closed = False

        @staticmethod
        def execute(_statement, _params):
            return Result()

        @staticmethod
        def commit():
            return None

        @staticmethod
        def invalidate():
            return None

        def close(self):
            if self.closed:
                return
            self.closed = True
            with state_guard:
                state["active"] -= 1

    class DedicatedEngine:
        def connect(self):
            with state_guard:
                state["active"] += 1
                state["connects"] += 1
                state["maximum"] = max(
                    state["maximum"],
                    state["active"],
                )
                if state["active"] == 2:
                    two_connections_open.set()
            return FakeConnection()

        @staticmethod
        def dispose():
            with state_guard:
                state["disposed"] += 1

    source_creator = object()

    class BusinessEngine:
        dialect = SimpleNamespace(name="postgresql")
        url = object()
        pool = SimpleNamespace(_creator=source_creator)

        @staticmethod
        def connect():
            raise AssertionError(
                "business pool must not provide advisory owner"
            )

    business_engine = BusinessEngine()

    class FakeSession:
        @staticmethod
        def get_bind():
            return business_engine

    dedicated_engine = DedicatedEngine()
    create_calls = 0

    def fake_create_engine(_url, **_kwargs):
        nonlocal create_calls
        create_calls += 1
        return dedicated_engine

    monkeypatch.setattr(runtime_fence, "create_engine", fake_create_engine)
    errors: list[BaseException] = []

    def lock_one(index: int) -> None:
        try:
            start_barrier.wait(timeout=5)
            with runtime_fence._ontology_build_lock(
                FakeSession(),
                f"bounded-advisory-{index}",
            ):
                if not release_owners.wait(timeout=5):
                    raise TimeoutError("test did not release advisory owners")
        except BaseException as exc:  # keep thread failures visible to pytest
            errors.append(exc)

    threads = [
        threading.Thread(target=lock_one, args=(index,))
        for index in range(4)
    ]
    for thread in threads:
        thread.start()
    start_barrier.wait(timeout=5)
    assert two_connections_open.wait(timeout=5)
    with state_guard:
        assert state["maximum"] == 2
        assert state["active"] == 2

    release_owners.set()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert errors == []
    assert create_calls == 1
    assert state["connects"] == 4
    assert state["maximum"] == 2
    assert state["active"] == 0

    runtime_fence._dispose_advisory_engines()
    assert state["disposed"] == 1
    assert runtime_fence._ADVISORY_ENGINES == {}


def test_strict_mapping_source_has_no_ten_thousand_row_cap(
        db, admin_user, lake_storage):
    rows = [{"id": str(i), "value": f"v-{i}"} for i in range(10_001)]
    _, _, mapping = _source_graph(db, admin_user, rows=rows)

    loaded, version = load_mapping_source_rows(db, mapping, require_approved=True)

    assert version is not None
    assert len(loaded) == 10_001
    assert loaded[-1]["id"] == "10000"


def test_apply_from_dataset_runs_complete_ontology_reconciliation(
        db, admin_user, lake_storage):
    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1"}])

    def _capture(_self, ontology_id, **kwargs):
        return {"ontology_id": ontology_id, "complete_rebuild": True, **kwargs}

    with patch.object(MappingService, "build_all", _capture):
        result = apply_mapping_from_dataset(ontology.id, mapping.id, db)

    assert result["ontology_id"] == ontology.id
    assert result["require_approved"] is True
    assert result["complete_rebuild"] is True
    assert result["trigger_mapping_id"] == mapping.id


def test_build_all_does_not_slice_strict_source_rows(db, admin_user, lake_storage):
    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "seed"}])
    rows = [{"id": str(i)} for i in range(10_001)]
    version = DatasetService(db).list_versions(mapping.curated_dataset_id)[-1]
    service = MappingService(db)

    with patch(
        "app.ontologies.mappings.mapping_service.load_mapping_source_rows",
        return_value=(rows, version),
    ), patch.object(MappingService, "_write_v1_entities", return_value=len(rows)), \
         patch.object(MappingService, "_infer_and_write_relations", return_value=[]), \
         patch.object(MappingService, "_process_link_mappings", return_value=[]), \
         patch.object(MappingService, "_discover_logic_rules", return_value={"total_v2": 0}), \
         patch.object(MappingService, "_discover_action_types", return_value={"total_v2": 0}), \
         patch(
             "app.services.v2.mapping.formal_projection.project_to_formal_ontology",
             return_value={"instances": len(rows)},
         ):
        result = service.build_all(ontology.id, require_approved=True)

    assert result["total_entities"] == 10_001
    db.refresh(mapping)
    assert mapping.status == "applied"
    assert mapping.field_mapping["__applied_dataset_version_id__"] == version.id


def test_new_curated_version_requires_fresh_approval(
        db, admin_user, lake_storage):
    _, dataset, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1", "value": "v1"}], kind="curated")
    review = ReviewService(db).start_review(dataset.id)
    ReviewService(db).approve(review.id)
    assert len(load_mapping_source_rows(db, mapping, require_approved=True)[0]) == 1

    DatasetService(db).create_version(
        dataset.id, _csv_bytes([{"id": "1", "value": "v2"}]), rowcount=1)

    with pytest.raises(MappingSourceError, match="旧版本审批不会自动继承"):
        load_mapping_source_rows(db, mapping, require_approved=True)


def test_raw_data_apply_bypass_is_blocked_and_ontology_scoped(
        db, admin_user, lake_storage):
    ontology, _, mapping = _source_graph(db, admin_user, rows=[{"id": "1"}])
    other = OntologyProject(
        name=f"other-{uuid.uuid4().hex[:8]}", domain="test",
        created_by=admin_user.id)
    db.add(other)
    db.commit()

    with pytest.raises(HTTPException) as wrong_owner:
        raw_apply_mapping(other.id, mapping.id, [{"id": "untrusted"}], db)
    assert wrong_owner.value.status_code == 404

    with pytest.raises(HTTPException) as raw_bypass:
        raw_apply_mapping(ontology.id, mapping.id, [{"id": "untrusted"}], db)
    assert raw_bypass.value.status_code == 409
    assert "apply-from-dataset" in str(raw_bypass.value.detail)


def test_mapping_reapply_reconciles_deleted_source_rows_with_tombstone(
        db, admin_user):
    from app.models.entity import Entity
    from app.models.ontology_formal import ObjectInstance, PropertyFact

    ontology = OntologyProject(
        id=str(uuid.uuid4()),
        name=f"reconcile-{uuid.uuid4().hex[:8]}", domain="test",
        created_by=admin_user.id)
    mapping = OntologyMapping(
        ontology_id=ontology.id, entity_class="LakeRow",
        field_mapping={"id": "business_id", "value": "value", "__primary_key__": "id"},
        status="draft")
    db.add_all([ontology, mapping])
    db.commit()

    service = MappingService(db)
    service.apply_mapping(mapping.id, [
        {"id": "A", "value": "old-a"},
        {"id": "B", "value": "old-b"},
    ])
    stale_id = service._stable_row_id(mapping, {"id": "A"}, "id")
    formal_instance_id = db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology.id,
        ObjectInstance.external_id == stale_id,
    ).one().id

    result = service.apply_mapping(mapping.id, [
        {"id": "B", "value": "new-b"},
        {"id": "C", "value": "new-c"},
    ])
    assert result["stale_entities_removed"] == 1
    assert db.query(Entity).filter(Entity.id == stale_id).first() is None
    assert db.query(ObjectInstance).filter(
        ObjectInstance.id == formal_instance_id,
    ).first() is None

    service.apply_mapping(mapping.id, [
        {"id": "A", "value": "restored-a"},
        {"id": "B", "value": "new-b"},
        {"id": "C", "value": "new-c"},
    ])
    assert db.query(ObjectInstance).filter(
        ObjectInstance.id == formal_instance_id,
    ).one().properties["value"] == "restored-a"

    second_delete = service.apply_mapping(mapping.id, [
        {"id": "B", "value": "new-b"},
        {"id": "C", "value": "new-c"},
    ])
    assert second_delete["stale_entities_removed"] == 1

    existence_facts = db.query(PropertyFact).filter(
        PropertyFact.ontology_id == ontology.id,
        PropertyFact.instance_id == formal_instance_id,
        PropertyFact.kind == "object",
        PropertyFact.property_name == "exists",
    ).order_by(
        PropertyFact.seq.asc(),
    ).all()
    assert [item.value for item in existence_facts] == [
        {"v": True},
        {"v": False},
        {"v": True},
        {"v": False},
    ]
    assert [
        item.supersedes_id for item in existence_facts
    ] == [
        None,
        existence_facts[0].id,
        existence_facts[1].id,
        existence_facts[2].id,
    ]


def test_link_mapping_delete_reconciles_relation_and_formal_link(
        db, admin_user, monkeypatch):
    from app.models.relation import Relation
    from app.models.ontology_formal import LinkInstance, PropertyFact
    from app.models.v2.mapping import OntologyLinkMapping

    ontology = OntologyProject(
        id=str(uuid.uuid4()), name=f"link-delete-{uuid.uuid4().hex[:8]}",
        domain="test", created_by=admin_user.id, status="draft")
    link_mapping = OntologyLinkMapping(
        id=str(uuid.uuid4()), ontology_id=ontology.id,
        relation_type="OWNS", src_key="owner_id", tgt_key="id",
        status="active", field_mapping={})
    relation = Relation(
        id=str(uuid.uuid4()), ontology_id=ontology.id,
        source_entity="source-1", target_entity="target-1", type="OWNS",
        properties={"mapping_type": "link_mapping",
                    "__link_mapping_id__": link_mapping.id}, confidence=1.0)
    formal_link = LinkInstance(
        id=str(uuid.uuid4()), ontology_id=ontology.id, link_type_id="lt-owns",
        source_object_id="source-instance", target_object_id="target-instance",
        properties={}, source_relation_id=relation.id)
    db.add_all([ontology, link_mapping, relation, formal_link])
    db.commit()
    monkeypatch.setattr(
        MappingService, "_rebuild_neo4j_projection", lambda *_args, **_kwargs: False)

    delete_link_mapping(ontology.id, link_mapping.id, db)

    assert db.query(Relation).filter_by(id=relation.id).first() is None
    assert db.query(LinkInstance).filter_by(id=formal_link.id).first() is None
    fact = db.query(PropertyFact).filter_by(
        instance_id=formal_link.id, kind="link", property_name="exists").first()
    assert fact is not None and fact.value == {"v": False}


def test_formal_projection_failure_marks_mapping_failed(
        db, admin_user, lake_storage):
    ontology, _, mapping = _source_graph(db, admin_user, rows=[{"id": "1"}])
    service = MappingService(db)

    with patch.object(MappingService, "_write_v1_entities", return_value=1), \
         patch(
             "app.services.v2.mapping.formal_projection.project_to_formal_ontology",
             side_effect=RuntimeError("formal unavailable"),
         ):
        with pytest.raises(MappingApplyError, match="未标记为 applied"):
            service.apply_mapping(
                mapping.id, [{"id": "1"}], ontology_id=ontology.id)

    db.expire_all()
    failed = db.query(OntologyMapping).filter_by(id=mapping.id).one()
    assert failed.status == "failed"
    assert "formal unavailable" in failed.field_mapping["__last_apply_error__"]


def test_applied_projection_with_sentinel_failure_is_not_reported_success(
        db, admin_user, lake_storage):
    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1"}])
    service = MappingService(db)
    dispatch = {
        "evaluated": 1,
        "fired": 0,
        "errors": [{
            "eventId": "sentinel-outbox-failed",
            "error": "downstream action failed",
        }],
        "runs": [{"status": "retry"}],
        "barrierCompleted": False,
    }

    with patch.object(
        MappingService, "_write_v1_entities", return_value=1,
    ), patch(
        "app.services.v2.mapping.formal_projection.project_to_formal_ontology",
        return_value={"instances_created": 1},
    ), patch(
        "app.ontologies.sentinels.cdc.dispatch_captured_changes",
        return_value=dispatch,
    ):
        with pytest.raises(
                MappingSentinelDispatchError,
                match="投影已提交.*Sentinel 下游级联失败"):
            service.apply_mapping(
                mapping.id, [{"id": "1"}], ontology_id=ontology.id)

    db.expire_all()
    applied = db.query(OntologyMapping).filter_by(id=mapping.id).one()
    assert applied.status == "applied"
    assert "__last_apply_error__" not in applied.field_mapping


def test_build_all_preserves_applied_fence_on_sentinel_dispatch_failure(
        db, admin_user, lake_storage):
    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1"}])
    dispatch = {
        "errors": [{
            "eventId": "build-all-sentinel-retry",
            "status": "retry",
            "error": "downstream action failed",
        }],
        "barrierCompleted": False,
    }

    def committed_projection_then_dispatch_failure(*_args, **_kwargs):
        applied = db.query(OntologyMapping).filter_by(id=mapping.id).one()
        applied.status = "applied"
        applied.field_mapping = {
            key: value
            for key, value in dict(applied.field_mapping or {}).items()
            if key != "__last_apply_error__"
        }
        db.commit()
        raise MappingSentinelDispatchError(ontology.id, dispatch)

    with patch.object(
            MappingService, "_build_all_transaction",
            side_effect=committed_projection_then_dispatch_failure):
        with pytest.raises(MappingSentinelDispatchError):
            MappingService(db).build_all(
                ontology.id, require_approved=True)

    db.expire_all()
    applied = db.query(OntologyMapping).filter_by(id=mapping.id).one()
    assert applied.status == "applied"
    assert "__last_apply_error__" not in applied.field_mapping


def test_unexpected_sentinel_dispatch_exception_preserves_applied_projection(
        db, admin_user, lake_storage):
    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1"}])

    with patch.object(
        MappingService, "_write_v1_entities", return_value=1,
    ), patch(
        "app.services.v2.mapping.formal_projection.project_to_formal_ontology",
        return_value={"instances_created": 1},
    ), patch(
        "app.ontologies.sentinels.cdc.dispatch_captured_changes",
        side_effect=RuntimeError("CDC worker connection lost"),
    ):
        with pytest.raises(
                MappingSentinelDispatchError,
                match="投影已提交.*Sentinel 下游级联失败") as captured:
            MappingService(db).apply_mapping(
                mapping.id, [{"id": "1"}], ontology_id=ontology.id)

    assert captured.value.dispatch["barrierCompleted"] is False
    assert captured.value.dispatch["errors"][0] == {
        "stage": "dispatch_captured_changes",
        "error": "CDC worker connection lost",
    }
    db.expire_all()
    applied = db.query(OntologyMapping).filter_by(id=mapping.id).one()
    assert applied.status == "applied"
    assert "__last_apply_error__" not in applied.field_mapping


def test_build_all_failure_rolls_back_entities_and_stale_relation_deletes(
        db, admin_user, lake_storage):
    from app.models.entity import Entity
    from app.models.relation import Relation

    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1", "value": "new"}])
    previous_relation = Relation(
        id=str(uuid.uuid4()), ontology_id=ontology.id,
        source_entity="old-source", target_entity="old-target",
        type="OLD_FK", properties={"source": "fk_inference"},
        confidence=1.0)
    db.add(previous_relation)
    db.commit()

    with patch(
        "app.services.v2.mapping.formal_projection.project_to_formal_ontology",
        side_effect=RuntimeError("formal validation rejected"),
    ):
        with pytest.raises(MappingApplyError, match="关系型投影已回滚"):
            MappingService(db).build_all(
                ontology.id, require_approved=True)

    db.expire_all()
    assert db.query(Relation).filter_by(id=previous_relation.id).first() is not None
    assert db.query(Entity).filter(
        Entity.ontology_id == ontology.id).count() == 0
    failed = db.query(OntologyMapping).filter_by(id=mapping.id).one()
    assert failed.status == "failed"
    assert "formal validation rejected" in failed.field_mapping["__last_apply_error__"]


@pytest.mark.parametrize("runtime_environment", ["development", "production"])
def test_runtime_derived_projection_failure_leaves_repairable_fence(
        db, admin_user, lake_storage, monkeypatch, runtime_environment):
    from app.config import settings
    from app.models.entity import Entity

    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1", "value": "committed"}])
    monkeypatch.setattr(settings, "environment", runtime_environment)
    monkeypatch.setattr(
        MappingService, "_rebuild_neo4j_projection",
        lambda *_args, **_kwargs: False)

    with pytest.raises(MappingApplyError, match="已阻断发布和动作执行"):
        MappingService(db).build_all(
            ontology.id, require_approved=True)

    db.expire_all()
    # SQL/Formal is the committed repair source; the failed status is the fence
    # that keeps runtime consumers away until an idempotent rebuild succeeds.
    assert db.query(Entity).filter(
        Entity.ontology_id == ontology.id).count() == 1
    failed = db.query(OntologyMapping).filter_by(id=mapping.id).one()
    assert failed.status == "failed"
    assert "Neo4j projection rebuild failed" in failed.field_mapping["__last_apply_error__"]


def test_development_single_apply_keeps_repairable_fence_when_neo4j_fails(
        db, admin_user, lake_storage, monkeypatch):
    from app.config import settings
    from app.models.entity import Entity

    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1", "value": "committed"}])
    monkeypatch.setattr(settings, "environment", "development")

    with patch.object(
        MappingService,
        "_rebuild_neo4j_projection",
        return_value=False,
    ):
        with pytest.raises(MappingApplyError, match="已阻断发布和动作执行"):
            MappingService(db).apply_mapping(
                mapping.id,
                [{"id": "1", "value": "committed"}],
                ontology_id=ontology.id,
            )

    db.expire_all()
    assert db.query(Entity).filter(
        Entity.ontology_id == ontology.id).count() == 1
    failed = db.query(OntologyMapping).filter_by(id=mapping.id).one()
    assert failed.status == "failed"
    assert "Neo4j projection rebuild failed" in failed.field_mapping["__last_apply_error__"]


def test_mapping_definition_update_invalidates_apply_attestation(
        db, admin_user, lake_storage):
    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1"}])
    mapping.status = "applied"
    mapping.field_mapping = {
        "__primary_key__": "id",
        "__applied_dataset_version_id__": "trusted-version",
    }
    db.commit()

    update_mapping(
        ontology.id, mapping.id,
        UpdateMappingRequest(entity_class="ChangedSourceRow"), db)

    db.refresh(mapping)
    assert mapping.status == "draft"
    assert "__applied_dataset_version_id__" not in mapping.field_mapping


def test_mapping_api_rejects_forged_runtime_lineage_keys(
        db, admin_user, lake_storage):
    ontology, dataset, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1"}])

    with pytest.raises(HTTPException) as update_error:
        update_mapping(
            ontology.id, mapping.id,
            UpdateMappingRequest(field_mapping={
                "__applied_dataset_version_id__": "forged",
            }), db)
    assert update_error.value.status_code == 422
    assert update_error.value.detail["code"] == "reserved_mapping_keys"

    with pytest.raises(HTTPException) as create_error:
        create_mapping(
            ontology.id,
            CreateMappingRequest(
                curated_dataset_id=dataset.id,
                entity_class="Forged",
                field_mapping={"__last_apply_error__": "hidden"},
            ), db)
    assert create_error.value.status_code == 422


def test_mapping_api_derives_primary_key_only_from_lake_contract(
        db, admin_user, lake_storage):
    ontology, dataset, _ = _source_graph(
        db, admin_user, rows=[{"id": "1", "value": "ok"}])

    result = create_mapping(
        ontology.id,
        CreateMappingRequest(
            curated_dataset_id=dataset.id,
            entity_class="CanonicalIdentity",
            field_mapping={"value": "value"},
        ), db)

    created = db.query(OntologyMapping).filter_by(
        id=result["mapping_id"]).one()
    assert created.field_mapping["__primary_key__"] == "id"
    assert created.field_mapping["__pk_source__"] == "lake"


def test_mapping_api_persists_explicit_ignored_fields_and_rejects_overwrite(
    db, admin_user, lake_storage,
):
    ontology, dataset, _ = _source_graph(
        db, admin_user,
        rows=[{"id": "1", "name": "A", "secret": "do-not-project"}],
    )

    result = create_mapping(
        ontology.id,
        CreateMappingRequest(
            curated_dataset_id=dataset.id,
            entity_class="ExplicitProjection",
            field_mapping={"id": "business_id", "name": "name"},
            ignored_fields=["secret"],
        ), db,
    )
    created = db.query(OntologyMapping).filter_by(id=result["mapping_id"]).one()
    MappingService(db)._normalize_mapping(created, [{
        "id": "1", "name": "A", "secret": "do-not-project",
    }])
    assert created.field_mapping["__ignored_fields__"] == ["secret"]
    assert "secret" not in created.field_mapping
    replay = create_mapping(
        ontology.id,
        CreateMappingRequest(
            curated_dataset_id=dataset.id,
            entity_class="ExplicitProjection",
            field_mapping={"id": "business_id", "name": "name"},
            ignored_fields=["secret"],
        ), db,
    )
    assert replay["mapping_id"] == created.id
    assert replay["idempotent_replay"] is True

    with pytest.raises(HTTPException) as duplicate_target:
        create_mapping(
            ontology.id,
            CreateMappingRequest(
                curated_dataset_id=dataset.id,
                entity_class="AmbiguousProjection",
                field_mapping={"id": "same", "name": "same"},
            ), db,
        )
    assert duplicate_target.value.detail["code"] == "duplicate_mapping_targets"

    with pytest.raises(HTTPException) as hidden_identity:
        create_mapping(
            ontology.id,
            CreateMappingRequest(
                curated_dataset_id=dataset.id,
                entity_class="HiddenIdentity",
                field_mapping={"name": "name"},
                ignored_fields=["id"],
            ), db,
        )
    assert hidden_identity.value.detail["code"] == "primary_key_cannot_be_ignored"


def test_mapping_api_rejects_client_primary_key_override(
        db, admin_user, lake_storage):
    ontology, dataset, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1", "other_id": "X"}])

    with pytest.raises(HTTPException) as create_error:
        create_mapping(
            ontology.id,
            CreateMappingRequest(
                curated_dataset_id=dataset.id,
                entity_class="ForgedIdentity",
                field_mapping={},
                primary_key_column="other_id",
            ), db)
    assert create_error.value.status_code == 400
    assert create_error.value.detail["code"] == "primary_key_contract_mismatch"
    assert create_error.value.detail["declared_primary_key"] == "id"

    with pytest.raises(HTTPException) as update_error:
        update_mapping(
            ontology.id, mapping.id,
            UpdateMappingRequest(primary_key_column="other_id"), db)
    assert update_error.value.status_code == 400
    assert update_error.value.detail["code"] == "primary_key_contract_mismatch"


def test_mapping_api_requires_declared_lake_primary_key(db, admin_user):
    ontology = OntologyProject(
        name=f"mapping-no-pk-{uuid.uuid4().hex[:8]}",
        domain="test", created_by=admin_user.id)
    dataset = Dataset(
        name=f"source-no-pk-{uuid.uuid4().hex[:8]}",
        kind="curated", schema_json={"columns": ["value"]})
    db.add_all([ontology, dataset])
    db.commit()

    with pytest.raises(HTTPException) as error:
        create_mapping(
            ontology.id,
            CreateMappingRequest(
                curated_dataset_id=dataset.id,
                entity_class="NoIdentity",
                field_mapping={"value": "value"},
            ), db)
    assert error.value.status_code == 400
    assert error.value.detail["code"] == "primary_key_required"


def test_link_mapping_rejects_composite_endpoint_and_wrong_object_dataset(
    db, admin_user,
):
    from app.models.ontology_formal import LinkType, ObjectType

    ontology = OntologyProject(
        name=f"link-contract-{uuid.uuid4().hex[:8]}",
        domain="test", created_by=admin_user.id,
    )
    db.add(ontology)
    db.flush()
    source_type = ObjectType(
        ontology_id=ontology.id, name="Supplier", display_name="供应商",
        properties=[], interfaces=[],
    )
    target_type = ObjectType(
        ontology_id=ontology.id, name="Order", display_name="订单",
        properties=[], interfaces=[],
    )
    db.add_all([source_type, target_type])
    db.flush()
    link_type = LinkType(
        ontology_id=ontology.id, name="SUPPLIES", display_name="供应",
        source_object_type_id=source_type.id,
        target_object_type_id=target_type.id,
        properties=[],
    )
    composite = Dataset(
        name=f"composite-{uuid.uuid4().hex[:6]}", kind="structured",
        schema_json={"primary_key": "tenant_id,supplier_id"},
    )
    target = Dataset(
        name=f"target-{uuid.uuid4().hex[:6]}", kind="structured",
        schema_json={"primary_key": "order_id"},
    )
    wrong = Dataset(
        name=f"wrong-{uuid.uuid4().hex[:6]}", kind="structured",
        schema_json={"primary_key": "wrong_id"},
    )
    edge = Dataset(
        name=f"edge-{uuid.uuid4().hex[:6]}", kind="structured",
        schema_json={"primary_key": "edge_id"},
    )
    db.add_all([link_type, composite, target, wrong, edge])
    db.flush()
    db.add_all([
        OntologyMapping(
            ontology_id=ontology.id, curated_dataset_id=composite.id,
            entity_class="Supplier", target_object_type_id=source_type.id,
            field_mapping={},
        ),
        OntologyMapping(
            ontology_id=ontology.id, curated_dataset_id=target.id,
            entity_class="Order", target_object_type_id=target_type.id,
            field_mapping={},
        ),
    ])
    db.commit()

    with pytest.raises(HTTPException) as composite_error:
        create_link_mapping(
            ontology.id,
            LinkMappingCreate(
                src_dataset_id=composite.id, tgt_dataset_id=target.id,
                edge_dataset_id=edge.id, relation_type=link_type.name,
                link_type_id=link_type.id, src_key="supplier_fk",
                tgt_key="order_fk",
            ), db,
        )
    assert composite_error.value.detail["code"] == "composite_endpoint_fk_not_supported"

    composite.schema_json = {"primary_key": "supplier_id"}
    db.commit()
    with pytest.raises(HTTPException) as mismatch:
        create_link_mapping(
            ontology.id,
            LinkMappingCreate(
                src_dataset_id=wrong.id, tgt_dataset_id=target.id,
                edge_dataset_id=edge.id, relation_type=link_type.name,
                link_type_id=link_type.id, src_key="supplier_fk",
                tgt_key="order_fk",
            ), db,
        )
    assert mismatch.value.detail["code"] == "link_endpoint_dataset_mismatch"


def test_link_mapping_selects_exact_dataset_when_object_has_multiple_mappings(
    db, admin_user,
):
    from app.models.ontology_formal import LinkType, ObjectType

    ontology = OntologyProject(
        name=f"link-multi-mapping-{uuid.uuid4().hex[:8]}",
        domain="test", created_by=admin_user.id,
    )
    db.add(ontology)
    db.flush()
    source_type = ObjectType(
        ontology_id=ontology.id, name="Supplier", display_name="供应商",
        properties=[], interfaces=[],
    )
    target_type = ObjectType(
        ontology_id=ontology.id, name="Order", display_name="订单",
        properties=[], interfaces=[],
    )
    db.add_all([source_type, target_type])
    db.flush()
    link_type = LinkType(
        ontology_id=ontology.id, name="SUPPLIES", display_name="供应",
        source_object_type_id=source_type.id,
        target_object_type_id=target_type.id,
        properties=[],
    )
    other_source = Dataset(
        name=f"supplier-other-{uuid.uuid4().hex[:6]}", kind="structured",
        schema_json={"primary_key": "supplier_id"},
    )
    selected_source = Dataset(
        name=f"supplier-selected-{uuid.uuid4().hex[:6]}", kind="structured",
        schema_json={"primary_key": "supplier_id"},
    )
    target = Dataset(
        name=f"order-selected-{uuid.uuid4().hex[:6]}", kind="structured",
        schema_json={"primary_key": "order_id"},
    )
    db.add_all([link_type, other_source, selected_source, target])
    db.flush()
    # 先写入同 ObjectType 的另一条映射，覆盖旧实现依赖 ``first()`` 的顺序。
    db.add(OntologyMapping(
        ontology_id=ontology.id, curated_dataset_id=other_source.id,
        entity_class="Supplier", target_object_type_id=source_type.id,
        field_mapping={},
    ))
    db.flush()
    db.add_all([
        OntologyMapping(
            ontology_id=ontology.id, curated_dataset_id=selected_source.id,
            entity_class="Supplier", target_object_type_id=source_type.id,
            field_mapping={},
        ),
        OntologyMapping(
            ontology_id=ontology.id, curated_dataset_id=target.id,
            entity_class="Order", target_object_type_id=target_type.id,
            field_mapping={},
        ),
    ])
    db.commit()

    rows_by_dataset = {
        selected_source.id: [{"supplier_id": "S-1", "order_fk": "O-1"}],
        target.id: [{"order_id": "O-1"}],
    }
    with patch.object(
        DatasetService, "preview",
        side_effect=lambda dataset_id, *_args, **_kwargs: rows_by_dataset[dataset_id],
    ):
        result = create_link_mapping(
            ontology.id,
            LinkMappingCreate(
                src_dataset_id=selected_source.id,
                tgt_dataset_id=target.id,
                relation_type=link_type.name,
                link_type_id=link_type.id,
                src_key="order_fk",
                tgt_key="order_id",
            ),
            db,
        )

    assert result["match_count"] == 1
    assert result["link_mapping_id"]


def test_composite_lake_primary_key_produces_stable_json_identity(
        db, admin_user):
    ontology = OntologyProject(
        id=str(uuid.uuid4()),
        name=f"mapping-composite-{uuid.uuid4().hex[:8]}",
        domain="test", created_by=admin_user.id)
    dataset = Dataset(
        id=str(uuid.uuid4()),
        name=f"source-composite-{uuid.uuid4().hex[:8]}",
        kind="curated",
        schema_json={"primary_key": "tenant_id, order_id"})
    mapping = OntologyMapping(
        ontology_id=ontology.id,
        curated_dataset_id=dataset.id,
        entity_class="TenantOrder",
        # Historical wrong single-column identity is repaired at runtime.
        field_mapping={"__primary_key__": "order_id"},
        status="draft")
    db.add_all([ontology, dataset, mapping])
    db.commit()
    service = MappingService(db)
    old = {"tenant_id": "T-1", "order_id": "O-7", "amount": "10"}
    changed = {"tenant_id": "T-1", "order_id": "O-7", "amount": "99"}

    service._normalize_mapping(mapping, [old, {
        "tenant_id": "T-1", "order_id": "O-8", "amount": "20"}])
    identity = service._row_identity_value(old, "tenant_id,order_id")

    assert mapping.field_mapping["__primary_key__"] == "tenant_id,order_id"
    assert mapping.field_mapping["__pk_source__"] == "lake"
    assert identity.startswith("composite_pk:")
    assert json.loads(identity.removeprefix("composite_pk:")) == {
        "columns": ["tenant_id", "order_id"],
        "values": ["T-1", "O-7"],
    }
    assert service._stable_row_id(mapping, old, "tenant_id,order_id") == (
        service._stable_row_id(mapping, changed, "tenant_id,order_id"))
    assert service._display_name(
        mapping, old, "tenant_id,order_id", 0
    ) == "tenant_id=T-1 / order_id=O-7"

    from app.ontologies.mappings.formal_projection import (
        _build_object_type_properties)
    entities = service._rows_to_entities(mapping, [old, {
        "tenant_id": "T-1", "order_id": "O-8", "amount": "20"}])
    properties, primary_property = _build_object_type_properties(
        entities,
        ["tenant_id", "order_id"],
        mapping.field_mapping["__properties__"],
    )
    by_name = {prop["name"]: prop for prop in properties}
    assert primary_property == by_name["__composite_identity__"]["id"]
    assert by_name["__composite_identity__"]["identityComponents"] == [
        "tenant_id", "order_id"]
    assert by_name["tenant_id"]["required"] is True
    assert by_name["tenant_id"]["primaryKeyPart"] == 1
    assert by_name["order_id"]["required"] is True
    assert by_name["order_id"]["primaryKeyPart"] == 2


def test_published_projection_recovers_explicit_mapped_primary_key_without_metadata(
        db, admin_user):
    """Released mappings execute without optional draft-time enrichment."""
    from app.models.entity import Entity
    from app.models.ontology_formal import ObjectInstance, ObjectType
    from app.ontologies.mappings.formal_projection import (
        project_to_formal_ontology,
        projection_property_mappings,
    )

    ontology = OntologyProject(
        id=str(uuid.uuid4()),
        name=f"released-pk-map-{uuid.uuid4().hex[:8]}",
        domain="test", status="published", created_by=admin_user.id,
    )
    object_type = ObjectType(
        id=str(uuid.uuid4()), ontology_id=ontology.id,
        name="SupplierAlert", display_name="供应商告警",
        primary_key="prop_alert_id",
        properties=[
            {
                "id": "prop_alert_id", "name": "alert_id",
                "displayName": "告警标识", "type": "string",
                "required": True, "source": "stored",
            },
            {
                "id": "prop_status", "name": "status",
                "displayName": "状态", "type": "string",
                "required": True, "source": "stored",
            },
        ],
        interfaces=[],
    )
    mapping = OntologyMapping(
        id=str(uuid.uuid4()), ontology_id=ontology.id,
        entity_class="SupplierAlert",
        target_object_type_id=object_type.id,
        field_mapping={
            "供应商ID": "alert_id",
            "状态": "status",
            "__primary_key__": "供应商ID",
            "__pk_source__": "lake",
        },
        status="applied",
    )
    entity = Entity(
        id=str(uuid.uuid4()), ontology_id=ontology.id,
        name_cn="SUP-007", name_en="SUP-007", type="SupplierAlert",
        properties={
            "alert_id": "SUP-007",
            "status": "待处理",
            "__mapping_ids__": [mapping.id],
        },
        confidence=1.0,
    )
    db.add_all([ontology, object_type, mapping, entity])
    db.commit()

    assert projection_property_mappings(mapping.field_mapping) == [
        {"column": "供应商ID", "property": "alert_id"},
        {"column": "状态", "property": "status"},
    ]
    result = project_to_formal_ontology(
        db,
        ontology.id,
        {
            mapping.id: {
                "mapping_id": mapping.id,
                "entity_class": mapping.entity_class,
                "pk_col": "供应商ID",
                # Reproduce the production failure: the immutable release had
                # no ``__properties__`` draft-time metadata.
                "property_mappings": [],
                "target_object_type_id": object_type.id,
            },
        },
        ontology_release_id="released-lineage",
    )

    projected = db.query(ObjectInstance).filter_by(
        ontology_id=ontology.id,
        object_type_id=object_type.id,
    ).one()
    assert result["object_instances"] == 1
    assert projected.properties["alert_id"] == "SUP-007"
    assert projected.properties["status"] == "待处理"
    assert projected.ontology_release_id == "released-lineage"
    db.refresh(object_type)
    projected_pk = next(
        prop for prop in object_type.properties
        if prop.get("name") == "alert_id"
    )
    assert projected_pk["primaryKeyPart"] == 1


def test_projection_normalizes_blank_non_string_cells_without_erasing_text():
    """Real CSV/XLSX blanks are nullable values, not mistyped strings."""
    from app.ontologies.mappings.formal_projection import _coerce_props_to_type

    coerced = _coerce_props_to_type(
        {
            "optional_score": "",
            "required_score": "  ",
            "optional_flag": "",
            "optional_date": "",
            "free_text": "  ",
        },
        [
            {
                "name": "optional_score",
                "type": "number",
                "required": False,
            },
            {
                "name": "required_score",
                "type": "number",
                "required": True,
            },
            {
                "name": "optional_flag",
                "type": "boolean",
                "required": False,
            },
            {
                "name": "optional_date",
                "type": "date",
                "required": False,
            },
            {
                "name": "free_text",
                "type": "string",
                "required": False,
            },
        ],
    )

    assert coerced == {
        "optional_score": None,
        # Required blanks also become None so the normal required-property
        # contract rejects them instead of reporting a misleading type error.
        "required_score": None,
        "optional_flag": None,
        "optional_date": None,
        "free_text": "  ",
    }


def test_projection_strictly_coerces_json_array_and_object_properties():
    """Lake JSON text becomes native values; malformed/wrong shapes fail closed."""
    from app.ontologies.mappings.formal_projection import _coerce_props_to_type

    native_array = ["already", "native"]
    native_object = {"already": "native"}
    definitions = [
        {"name": "array_json", "type": "array"},
        {"name": "object_json", "type": "object"},
        {"name": "array_native", "type": "array"},
        {"name": "object_native", "type": "object"},
        {"name": "nullable_array", "type": "array"},
        {"name": "nullable_object", "type": "object"},
        {"name": "blank_array", "type": "array"},
    ]

    coerced = _coerce_props_to_type(
        {
            "array_json": '["urgent", 2]',
            "object_json": '{"enabled": true}',
            "array_native": native_array,
            "object_native": native_object,
            "nullable_array": "null",
            "nullable_object": None,
            "blank_array": "  ",
        },
        definitions,
    )

    assert coerced == {
        "array_json": ["urgent", 2],
        "object_json": {"enabled": True},
        "array_native": native_array,
        "object_native": native_object,
        "nullable_array": None,
        "nullable_object": None,
        "blank_array": None,
    }
    assert coerced["array_native"] is native_array
    assert coerced["object_native"] is native_object

    invalid_cases = [
        ("array_json", "array", "not-json"),
        ("array_json", "array", '{"wrong": "shape"}'),
        ("array_json", "array", {"native": "object"}),
        ("object_json", "object", '["wrong", "shape"]'),
        ("object_json", "object", ["native", "array"]),
        ("object_json", "object", "42"),
    ]
    for name, property_type, value in invalid_cases:
        with pytest.raises(ValueError, match=rf"属性 {name} .*{property_type}"):
            _coerce_props_to_type(
                {name: value},
                [{"name": name, "type": property_type}],
            )


def test_apply_refuses_stale_source_version_after_concurrent_publish(
        db, admin_user, lake_storage):
    ontology, dataset, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1", "value": "v1"}])
    version_1 = DatasetService(db).list_versions(dataset.id)[-1]
    DatasetService(db).create_version(
        dataset.id, _csv_bytes([{"id": "1", "value": "v2"}]), rowcount=1)
    service = MappingService(db)

    with patch.object(MappingService, "_write_v1_entities", return_value=1), \
         patch(
             "app.services.v2.mapping.formal_projection.project_to_formal_ontology",
             return_value={"instances": 1},
         ):
        with pytest.raises(MappingApplyError, match="执行期间数据源已更新"):
            service.apply_mapping(
                mapping.id, [{"id": "1", "value": "v1"}],
                ontology_id=ontology.id,
                source_dataset_version_id=version_1.id)

    db.expire_all()
    assert db.query(OntologyMapping).filter_by(id=mapping.id).one().status == "failed"


def test_mapping_service_rejects_cross_ontology_id(
        db, admin_user, lake_storage):
    ontology, _, mapping = _source_graph(db, admin_user, rows=[{"id": "1"}])
    other = OntologyProject(
        name=f"other-{uuid.uuid4().hex[:8]}", domain="test",
        created_by=admin_user.id)
    db.add(other)
    db.commit()

    with pytest.raises(ValueError, match="not found"):
        MappingService(db).apply_mapping(
            mapping.id, [{"id": "1"}], ontology_id=other.id)


def test_published_ontology_freezes_mapping_structure(
        db, admin_user, lake_storage):
    ontology, dataset, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1"}])
    ontology.status = "published"
    db.commit()

    mutations = [
        lambda: create_mapping(ontology.id, CreateMappingRequest(
            curated_dataset_id=dataset.id, entity_class="Blocked",
            field_mapping={}), db),
        lambda: update_mapping(
            ontology.id, mapping.id, UpdateMappingRequest(entity_class="Blocked"), db),
        lambda: delete_mapping(ontology.id, mapping.id, db),
        lambda: create_link_mapping(ontology.id, LinkMappingCreate(
            src_dataset_id=dataset.id, tgt_dataset_id=dataset.id,
            relation_type="BLOCKED", src_key="id", tgt_key="id"), db),
        lambda: delete_link_mapping(ontology.id, "missing-link", db),
    ]
    for mutate in mutations:
        with pytest.raises(HTTPException) as blocked:
            mutate()
        assert blocked.value.status_code == 409
        assert "draft" in str(blocked.value.detail)


def test_published_ontology_rejects_mapping_outside_current_release(
        db, admin_user, lake_storage):
    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1"}])
    release = OntologyVersion(
        id=str(uuid.uuid4()), ontology_id=ontology.id,
        version_number="v0", node_kind="release",
        lifecycle_status="released", revision=0,
        snapshot_formal={
            "objectTypes": [], "linkTypes": [], "actions": [],
            "functions": [], "sentinels": [], "mappings": [],
            "linkMappings": [],
        },
        created_by=admin_user.id,
    )
    db.add(release)
    db.flush()
    ontology.status = "published"
    ontology.current_release_id = release.id
    db.commit()

    with pytest.raises(HTTPException) as blocked:
        apply_mapping_from_dataset(ontology.id, mapping.id, db)

    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == "mapping_not_in_current_release"
    assert "晋级发布" in blocked.value.detail["message"]
    db.expire_all()
    assert db.query(OntologyMapping).filter_by(id=mapping.id).one().status == "draft"


def test_current_release_mapping_can_rebuild_with_immutable_owner(
        db, admin_user, lake_storage):
    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1"}])
    release = OntologyVersion(
        id=str(uuid.uuid4()), ontology_id=ontology.id,
        version_number="v1", node_kind="release",
        lifecycle_status="released", revision=0,
        snapshot_formal={
            "objectTypes": [], "linkTypes": [], "actions": [],
            "functions": [], "sentinels": [],
            "mappings": [{
                "id": mapping.id,
                "curatedDatasetId": mapping.curated_dataset_id,
                "entityClass": mapping.entity_class,
                "fieldMapping": dict(mapping.field_mapping or {}),
                "targetObjectTypeId": None,
                "status": "draft",
                "confidence": None,
            }],
            "linkMappings": [],
        },
        created_by=admin_user.id,
    )
    db.add(release)
    db.flush()
    ontology.status = "published"
    ontology.current_release_id = release.id
    db.commit()

    expected = {"ontology_id": ontology.id, "release_id": release.id}
    with patch.object(
            MappingService, "_build_all_transaction", return_value=expected,
    ) as build:
        result = MappingService(db).build_all(
            ontology.id, require_approved=True)

    assert result == expected
    assert build.call_args.kwargs["ontology_release_id"] == release.id
    assert build.call_args.kwargs["require_approved"] is True


def test_mapping_task_reconciles_complete_ontology(
        db, admin_user, lake_storage):
    from app.tasks.v2.mapping_apply import mapping_apply_task

    ontology, _, mapping = _source_graph(db, admin_user, rows=[{"id": "seed"}])
    captured: dict = {}

    def _capture(_self, ontology_id, **kwargs):
        captured.update(ontology_id=ontology_id, **kwargs)
        return {"complete_rebuild": True}

    # This unit only verifies the task's Mapping contract.  Keep process-global
    # CDC registration for the dedicated cold-worker test below so test order
    # cannot leave a live SQLite consumer behind.
    with patch(
        "app.ontologies.sentinels.cdc.register_cdc",
    ) as register, patch(
        "app.database.SessionLocal", return_value=db,
    ), patch.object(
        MappingService, "build_all", _capture,
    ):
        mapping_apply_task.run(mapping.id, ontology.id)

    register.assert_called_once_with(start_worker=False)
    assert captured == {
        "ontology_id": ontology.id,
        "require_approved": True,
    }


def test_mapping_worker_cold_import_resolves_canonical_cdc_registration():
    """Celery imports Mapping without FastAPI lifespan priming Sentinel modules."""
    backend_dir = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.services.v2.mapping.mapping_service "
                "import MappingService; "
                "from app.ontologies.sentinels.cdc import register_cdc; "
                "from app.services.sentinel "
                "import register_cdc as compatibility_register_cdc; "
                "assert MappingService; "
                "assert register_cdc is compatibility_register_cdc"
            ),
        ],
        cwd=backend_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_mapping_task_cold_worker_path_runs_real_build_all(
        db, admin_user, lake_storage):
    """A cold review worker must persist and dispatch both Sentinel edges."""
    import threading
    from sqlalchemy.orm import sessionmaker
    from app import database as app_database
    from app.data_channel.datasets.service import DatasetService
    from app.models.entity import Entity
    from app.models.ontology_formal import ObjectInstance, ObjectType
    from app.models.sentinel import (
        Sentinel, SentinelCdcOutbox, SentinelFiring,
    )
    from app.ontologies.sentinels import cdc as sentinel_cdc
    from app.ontologies.versions.evolution_service import (
        complete_snapshot,
        snapshot_hash,
    )
    from app.ontologies.versions.router import _snapshot_formal
    from app.tasks.v2.mapping_apply import mapping_apply_task

    ontology, dataset, mapping = _source_graph(
        db, admin_user, rows=[{"record_id": "R-1", "risk": "high"}])
    dataset.schema_json = {
        **dict(dataset.schema_json or {}),
        "primary_key": "record_id",
    }
    object_type = ObjectType(
        id=f"risk-record-{uuid.uuid4()}",
        ontology_id=ontology.id,
        name="RiskRecord",
        display_name="风险记录",
        primary_key="risk_record_id",
        properties=[
            {
                "id": "risk_record_id",
                "name": "record_id",
                "displayName": "记录编号",
                "type": "string",
                "required": True,
                "source": "stored",
            },
            {
                "id": "risk_level",
                "name": "risk",
                "displayName": "风险等级",
                "type": "string",
                "required": True,
                "source": "stored",
            },
        ],
        interfaces=[],
        position_x=0,
        position_y=0,
    )
    sentinel = Sentinel(
        id=f"risk-sentinel-{uuid.uuid4()}",
        ontology_id=ontology.id,
        name="watch_high_risk",
        display_name="高风险边沿哨兵",
        bindings=[{
            "alias": "record",
            "objectTypeId": object_type.id,
        }],
        links=[],
        condition="record.risk == 'high'",
        primary_alias="record",
        action_ids=[],
        action_parameters={},
        on_change=True,
        on_schedule=False,
        trigger_mode="on_enter_leave",
        muted=False,
        enabled=True,
        status="published",
    )
    mapping.entity_class = object_type.name
    mapping.target_object_type_id = object_type.id
    mapping.field_mapping = {
        "record_id": "record_id",
        "risk": "risk",
        "__primary_key__": "record_id",
        "__pk_source__": "lake",
        "__auto_apply_on_review__": True,
    }
    ontology.status = "published"
    ontology.version = "v1.0.0"
    db.add_all([object_type, sentinel])
    db.commit()

    release_id = f"risk-release-{uuid.uuid4()}"
    release_snapshot = complete_snapshot(
        _snapshot_formal(db, ontology.id))
    release = OntologyVersion(
        id=release_id,
        ontology_id=ontology.id,
        version_number=ontology.version,
        version_label="风险边沿回归发布",
        base_release_id=release_id,
        node_kind="release",
        lifecycle_status="released",
        revision=0,
        snapshot_formal=release_snapshot,
        snapshot_hash=snapshot_hash(release_snapshot),
        created_by=admin_user.id,
    )
    db.add(release)
    db.flush()
    ontology.current_release_id = release.id
    db.commit()

    worker_session = sessionmaker(bind=db.get_bind())
    application_session_factory = app_database.SessionLocal
    register_calls = 0
    task_session_calls = 0
    register_worker_modes: list[bool] = []
    real_register_cdc = sentinel_cdc.register_cdc

    def tracked_register_cdc(*, start_worker: bool = True):
        nonlocal register_calls
        register_calls += 1
        register_worker_modes.append(start_worker)
        return real_register_cdc(start_worker=start_worker)

    def worker_session_factory():
        nonlocal task_session_calls
        # A prior TestClient lifespan may have left its process-global daemon
        # alive.  Patching app.database.SessionLocal must not redirect that
        # unrelated thread onto this test's SQLite file, where it would race
        # the synchronous mapping barrier.  Production processes use one
        # configured database; this split only restores that boundary in the
        # multi-engine test process.
        if threading.current_thread() is not threading.main_thread():
            return application_session_factory()
        # Registration must precede Session creation in every Celery process.
        task_session_calls += 1
        assert register_calls >= task_session_calls
        return worker_session()

    # Only replace rebuildable external query projections. MappingService,
    # its transaction, canonical CDC import and the task boundary all run for
    # real, matching auto_apply_on_review worker execution.
    with patch.object(
        sentinel_cdc, "register_cdc", tracked_register_cdc,
    ), patch(
        "app.database.SessionLocal", side_effect=worker_session_factory,
    ), patch.object(
        MappingService, "_rebuild_neo4j_projection", return_value=True,
    ):
        # high establishes the entered edge.
        mapping_apply_task.run(mapping.id, ontology.id)
        DatasetService(db).create_version(
            dataset.id,
            _csv_bytes([{"record_id": "R-1", "risk": "low"}]),
            rowcount=1,
        )
        # high -> low must persist and synchronously dispatch the leave edge.
        mapping_apply_task.run(mapping.id, ontology.id)

    db.expire_all()
    applied = db.query(OntologyMapping).filter_by(id=mapping.id).one()
    instance = db.query(ObjectInstance).filter_by(
        ontology_id=ontology.id,
        object_type_id=object_type.id,
    ).one()
    firings = db.query(SentinelFiring).filter_by(
        ontology_id=ontology.id,
        sentinel_id=sentinel.id,
    ).order_by(SentinelFiring.created_at, SentinelFiring.id).all()
    cdc_events = db.query(SentinelCdcOutbox).filter_by(
        ontology_id=ontology.id,
        event_kind="object_change",
    ).order_by(SentinelCdcOutbox.created_at, SentinelCdcOutbox.id).all()

    assert register_calls == 2
    assert register_worker_modes == [False, False]
    assert task_session_calls == 2
    assert applied.status == "applied"
    assert applied.field_mapping["__applied_dataset_version_id__"]
    assert instance.properties["risk"] == "low"
    assert db.query(Entity).filter_by(ontology_id=ontology.id).count() == 1
    entered_firing = next(item for item in firings if item.entered)
    left_firing = next(item for item in firings if item.left)
    assert entered_firing.trigger_source == "change"
    assert left_firing.trigger_source == "change"
    assert len(cdc_events) >= 2
    assert all(event.status == "completed" for event in cdc_events)
    assert all((event.result_json or {}).get("evaluated") == 1
               for event in cdc_events)


def test_mapping_task_accepts_link_subscription_anchor_and_stays_approved_only(
        db, admin_user):
    """A curated edge review can dispatch from a link-only subscription."""
    from app.tasks.v2.mapping_apply import mapping_apply_task

    ontology = OntologyProject(
        name=f"link-review-task-{uuid.uuid4().hex[:8]}",
        domain="test",
        created_by=admin_user.id,
    )
    dataset = Dataset(
        name=f"link-review-source-{uuid.uuid4().hex[:8]}",
        kind="curated",
        schema_json={"primary_key": "id"},
    )
    db.add_all([ontology, dataset])
    db.flush()
    link_mapping = OntologyLinkMapping(
        ontology_id=ontology.id,
        src_dataset_id=dataset.id,
        tgt_dataset_id=dataset.id,
        edge_dataset_id=dataset.id,
        relation_type="connected_to",
        src_key="source_id",
        tgt_key="target_id",
        field_mapping={"__auto_apply_on_review__": True},
        status="active",
    )
    db.add(link_mapping)
    db.commit()
    captured: dict = {}

    def _capture(_self, ontology_id, **kwargs):
        captured.update(ontology_id=ontology_id, **kwargs)
        return {"complete_rebuild": True}

    with patch("app.database.SessionLocal", return_value=db), patch.object(
        MappingService, "build_all", _capture,
    ):
        mapping_apply_task.run(link_mapping.id, ontology.id)

    assert captured == {
        "ontology_id": ontology.id,
        "require_approved": True,
    }


def test_mapping_task_missing_mapping_is_hard_failure():
    from app.tasks.v2.mapping_apply import mapping_apply_task

    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = None
    with patch("app.database.SessionLocal", return_value=fake_db):
        with pytest.raises(ValueError, match="not found in ontology"):
            mapping_apply_task.run("missing-map", "ont-1")
    fake_db.close.assert_called_once()
