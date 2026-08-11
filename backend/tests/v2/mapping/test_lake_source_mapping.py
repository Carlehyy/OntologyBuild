"""本体 Mapping 消费物理湖表 curated 源的端到端与判据测试。

覆盖：入湖（lake_store.upsert_run）→ 审核批准 → durable outbox drain →
IncrementalOrchestrator.on_review_approved → mapping_apply_task →
rebuild_ontology_projection 的全链路（真实数据库与生产代码路径，Neo4j 查询
投影用成功适配器替身）；release gate 对湖表版本（无 blob 载荷）的判据；
iter_rows_with_edits 的批大小边界与批间编辑叠加一致性。
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.data_channel.curated.approved_version_reader import (
    apply_all_row_edits,
    iter_rows_with_edits,
)
from app.data_channel.curated.review_service import ReviewService
from app.data_channel.datasets import lake_store
from app.data_channel.datasets.lake_gate import split_pk
from app.data_channel.datasets.models import Dataset, DatasetVersionEvent
from app.data_channel.datasets.version_events import drain_dataset_version_events
from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.v2.mapping import OntologyMapping
from app.ontologies.mappings.mapping_service import MappingService
from app.ontologies.versions.release_gate_service import (
    validate_production_mappings,
)


@pytest.fixture(autouse=True)
def _drop_lake_tables(db):
    """物理湖表不在 Base.metadata 中（运行时 DDL），逐测试清理避免串库。"""
    yield
    conn = db.connection()
    for name in sa.inspect(conn).get_table_names():
        if name.startswith("lake_ds_"):
            conn.execute(sa.text(f'DROP TABLE IF EXISTS "{name}"'))
    db.commit()


def _make_lake_dataset(db, runs, pk="id", columns=("id", "name")):
    ds = Dataset(
        id=str(uuid.uuid4()), name=f"映射源-{uuid.uuid4().hex[:6]}",
        kind="curated",
        schema_json={"primary_key": pk, "columns": list(columns)})
    db.add(ds)
    db.commit()
    versions = []
    for mode, rows in runs:
        version, _ = lake_store.upsert_run(db, ds, rows, mode, split_pk(pk))
        versions.append(version)
    db.refresh(ds)
    return ds, versions


def test_review_approve_to_mapping_chain_over_lake_source(db, monkeypatch,
                                                          admin_user):
    """湖表 curated 源：审批通过 → outbox → 同步 Mapping 对账全链正确性。"""
    from app.ontologies.sentinels.cdc import register_cdc

    monkeypatch.setattr(
        "app.database.SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(
        MappingService, "_rebuild_neo4j_projection", lambda *_args: True)
    # Mapping 自带同步 CDC 屏障；测试内不再起后台消费者竞争同一 outbox 行
    register_cdc(start_worker=False)

    ds, (v1, v2) = _make_lake_dataset(db, [
        ("overwrite", [{"id": "C-1", "name": "甲"}, {"id": "C-2", "name": "乙"}]),
        ("upsert", [{"id": "C-2", "name": "乙改"}, {"id": "C-3", "name": "丙"}]),
    ])
    ontology = OntologyProject(
        id=f"ont-{uuid.uuid4().hex[:8]}", name="湖表源闭环", domain="test",
        created_by=admin_user.id)
    from app.models.ontology_formal import ObjectType
    object_type = ObjectType(
        id=f"ot-{uuid.uuid4().hex[:8]}", ontology_id=ontology.id,
        name="Customer", display_name="客户",
        primary_key="prop_id",
        properties=[
            {"id": "prop_id", "name": "id", "displayName": "客户编号",
             "type": "string", "required": True, "source": "stored"},
            {"id": "prop_name", "name": "name", "displayName": "客户名称",
             "type": "string", "required": True, "source": "stored"},
        ],
        interfaces=[], position_x=0, position_y=0,
    )
    mapping = OntologyMapping(
        id=f"map-{uuid.uuid4().hex[:8]}",
        ontology_id=ontology.id,
        curated_dataset_id=ds.id,
        entity_class="Customer",
        target_object_type_id=object_type.id,
        status="draft",
        field_mapping={
            "id": "id", "name": "name",
            "__primary_key__": "id", "__pk_source__": "lake",
            "__auto_apply_on_review__": True,
        },
    )
    db.add_all([ontology, object_type, mapping])
    db.commit()

    # 运行时自动化仅限已发布本体：冻结建模与映射定义到不可变发布快照
    #（哨兵屏障要求 current release，发布作用域校验要求映射定义与快照一致）
    from datetime import datetime, timezone

    from app.models.ontology_version import OntologyVersion
    from app.ontologies.versions.evolution_service import (
        complete_snapshot,
        snapshot_hash,
    )
    from app.ontologies.versions.router import _snapshot_formal

    ontology.status = "published"
    ontology.version = "v1.0.0"
    release_snapshot = complete_snapshot(_snapshot_formal(db, ontology.id))
    release = OntologyVersion(
        id=f"rel-{uuid.uuid4().hex[:8]}",
        ontology_id=ontology.id,
        version_number=ontology.version,
        version_label="湖表源闭环发布",
        base_release_id=None,
        node_kind="release",
        lifecycle_status="released",
        revision=0,
        snapshot_formal=release_snapshot,
        snapshot_hash=snapshot_hash(release_snapshot),
        published_at=datetime.now(timezone.utc),
        created_by=ontology.created_by,
    )
    db.add(release)
    db.flush()
    ontology.current_release_id = release.id
    db.commit()

    review = ReviewService(db).start_review(ds.id)
    assert review.dataset_version_id == v2.id
    ReviewService(db).approve(review.id)

    result = drain_dataset_version_events(db, limit=10)

    db.expire_all()
    event = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=v2.id,
        event_type="curated_review_approved").one()
    assert event.status == "completed", event.last_error
    assert result["processed"] >= 1 and result["retried"] == 0

    mapping = db.query(OntologyMapping).filter_by(id=mapping.id).one()
    assert mapping.status == "applied", (
        mapping.field_mapping or {}).get("__last_apply_error__")
    # 血缘：消费的是审批绑定的湖表版本 v2
    assert (mapping.field_mapping or {})[
        "__applied_dataset_version_id__"] == v2.id
    entities = db.query(Entity).filter(
        Entity.ontology_id == ontology.id).all()
    assert len(entities) == 3
    pairs = {
        ((e.properties or {}).get("__business_properties__", {}).get("id"),
         (e.properties or {}).get("__business_properties__", {}).get("name"))
        for e in entities}
    assert pairs == {("C-1", "甲"), ("C-2", "乙改"), ("C-3", "丙")}


def test_release_gate_accepts_lake_version_metadata(db, admin_user):
    """release gate 的 version_has_content 判据：湖表版本（data_size=NULL）
    有内容不误判；空湖版本（rowcount=0）同样是合法有内容状态。"""
    ds, (v1,) = _make_lake_dataset(db, [
        ("overwrite", [{"id": "1", "name": "甲"}]),
    ])
    ontology = OntologyProject(
        id=f"ont-{uuid.uuid4().hex[:8]}", name="门禁", domain="test",
        created_by=admin_user.id)
    review = ReviewService(db).start_review(ds.id)
    ReviewService(db).approve(review.id)
    mapping = OntologyMapping(
        id=f"map-{uuid.uuid4().hex[:8]}",
        ontology_id=ontology.id,
        curated_dataset_id=ds.id,
        entity_class="Customer",
        status="applied",
        field_mapping={
            "__primary_key__": "id",
            "__applied_dataset_version_id__": v1.id,
        },
    )
    db.add_all([ontology, mapping])
    db.commit()
    db.refresh(ds)

    errors = validate_production_mappings(
        db, ontology.id, [mapping], [], [], [])
    assert errors == []


def test_iter_rows_with_edits_batch_boundaries(db):
    """批大小边界（1 / 大于行数 / 非整除）与跨批编辑叠加一致性。"""
    rows = [{"id": str(i), "name": f"n{i}"} for i in range(5)]
    ds, _ = _make_lake_dataset(db, [("overwrite", rows)])
    svc = ReviewService(db)
    review = svc.start_review(ds.id)
    svc.batch_edit_rows(review.id, [
        {"row_pk": "0", "field_name": "name", "old_value": "n0",
         "new_value": "首"},
        {"row_pk": "4", "field_name": "name", "old_value": "n4",
         "new_value": "尾"},
    ])
    svc.approve(review.id)

    full = apply_all_row_edits(db, ds.id, rows, dataset_version_id=None)
    for batch_size in (1, 2, 10):
        batches = list(iter_rows_with_edits(db, ds.id, batch_size=batch_size))
        streamed = [row for batch in batches for row in batch]
        assert streamed == full
        assert [len(b) for b in batches] == {
            1: [1, 1, 1, 1, 1], 2: [2, 2, 1], 10: [5],
        }[batch_size]
    assert full[0]["name"] == "首" and full[4]["name"] == "尾"
