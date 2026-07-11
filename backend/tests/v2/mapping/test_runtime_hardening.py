"""Mapping 生产执行边界：归属、血缘、全量读取与失败状态。"""
from __future__ import annotations

import csv
import io
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.ontology import OntologyProject
from app.models.v2.dataset import Dataset
from app.models.v2.mapping import OntologyMapping
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
    MappingSourceError,
    MappingService,
    load_mapping_source_rows,
)
from app.data_channel.datasets.service import DatasetService
from app.services.v2.curated.review_service import ReviewService


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
    ), patch.object(MappingService, "_write_neo4j", return_value=len(rows)), \
         patch.object(MappingService, "_write_v1_entities", return_value=len(rows)), \
         patch.object(MappingService, "_infer_and_write_relations", return_value=[]), \
         patch.object(MappingService, "_process_link_mappings", return_value=[]), \
         patch.object(MappingService, "_discover_logic_rules", return_value={"total_v2": 0}), \
         patch.object(MappingService, "_discover_action_types", return_value={"total_v2": 0}), \
         patch("app.services.v2.vector.chroma_service.ChromaService", return_value=MagicMock()), \
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
    with patch.object(MappingService, "_write_neo4j", return_value=0), \
         patch.object(MappingService, "_delete_neo4j_entities", return_value=1):
        service.apply_mapping(mapping.id, [
            {"id": "A", "value": "old-a"},
            {"id": "B", "value": "old-b"},
        ])
        stale_id = service._stable_row_id(mapping, {"id": "A"}, "id")

        result = service.apply_mapping(mapping.id, [
            {"id": "B", "value": "new-b"},
            {"id": "C", "value": "new-c"},
        ])

    assert result["stale_entities_removed"] == 1
    assert db.query(Entity).filter(Entity.id == stale_id).first() is None
    assert db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology.id,
        ObjectInstance.external_id == stale_id,
    ).first() is None
    tombstone = db.query(PropertyFact).filter(
        PropertyFact.ontology_id == ontology.id,
        PropertyFact.kind == "object",
        PropertyFact.property_name == "exists",
    ).order_by(PropertyFact.recorded_at.desc()).first()
    assert tombstone is not None
    assert tombstone.value == {"v": False}


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

    with patch.object(MappingService, "_write_neo4j", return_value=1), \
         patch.object(MappingService, "_write_v1_entities", return_value=1), \
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


def test_production_derived_projection_failure_leaves_repairable_fence(
        db, admin_user, lake_storage, monkeypatch):
    from app.config import settings
    from app.models.entity import Entity

    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1", "value": "committed"}])
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(
        MappingService, "_rebuild_neo4j_projection",
        lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        MappingService, "_rebuild_chroma_projection",
        lambda *_args, **_kwargs: 1)

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


def test_apply_refuses_stale_source_version_after_concurrent_publish(
        db, admin_user, lake_storage):
    ontology, dataset, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1", "value": "v1"}])
    version_1 = DatasetService(db).list_versions(dataset.id)[-1]
    DatasetService(db).create_version(
        dataset.id, _csv_bytes([{"id": "1", "value": "v2"}]), rowcount=1)
    service = MappingService(db)

    with patch.object(MappingService, "_write_neo4j", return_value=1), \
         patch.object(MappingService, "_write_v1_entities", return_value=1), \
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


def test_published_ontology_still_allows_apply_from_dataset(
        db, admin_user, lake_storage):
    ontology, _, mapping = _source_graph(
        db, admin_user, rows=[{"id": "1"}])
    ontology.status = "published"
    db.commit()

    def _capture(_self, ontology_id, **kwargs):
        return {"ontology_id": ontology_id, **kwargs}

    with patch.object(MappingService, "build_all", _capture):
        result = apply_mapping_from_dataset(ontology.id, mapping.id, db)

    assert result["ontology_id"] == ontology.id
    assert result["require_approved"] is True


def test_mapping_task_reconciles_complete_ontology(
        db, admin_user, lake_storage):
    from app.tasks.v2.mapping_apply import mapping_apply_task

    ontology, _, mapping = _source_graph(db, admin_user, rows=[{"id": "seed"}])
    captured: dict = {}

    def _capture(_self, ontology_id, **kwargs):
        captured.update(ontology_id=ontology_id, **kwargs)
        return {"complete_rebuild": True}

    with patch("app.database.SessionLocal", return_value=db), patch.object(
        MappingService, "build_all", _capture):
        mapping_apply_task.run(mapping.id, ontology.id)

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
