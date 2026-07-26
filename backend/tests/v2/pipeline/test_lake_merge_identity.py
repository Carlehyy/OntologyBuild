"""资产湖合并模式与产物身份回归测试。"""
from types import SimpleNamespace

import pytest

from app.data_channel.pipeline_tasks.merge import compute_lake_impact, merge_rows
from app.data_channel.datasets.lake_gate import LakeGateError, validate_merged_lake
from app.data_channel.datasets.service import (
    DatasetService,
    _parse_stored_rows,
    rows_to_parquet_bytes,
)
from app.models.v2.dataset import Dataset, DatasetVersion
from app.tasks.v2.pipeline_run import _save_curated_dataset, resolve_curated_target


class _Storage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, bucket, key, data, content_type=""):
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data
        return uri

    def get_object(self, uri):
        return self.objects[uri]

    def delete_object(self, uri):
        self.objects.pop(uri, None)


def _pipeline(pid: str, name: str, targets=None):
    return SimpleNamespace(id=pid, name=name, target_curated_ids=targets or [])


def test_unknown_write_mode_never_falls_back_to_overwrite():
    with pytest.raises(ValueError, match="不会把未知方式回退为 overwrite"):
        merge_rows([{"id": "old"}], [{"id": "new"}], {"mode": "overwirte"})


def test_append_dedup_still_blocks_same_pk_with_changed_fields():
    old = [{"id": "1", "value": "old"}]
    new = [{"id": "1", "value": "new"}]
    merged, meta = merge_rows(old, new, {"mode": "append_dedup"})
    assert len(merged) == 2  # 整行不同，整行去重无法识别同一业务对象
    with pytest.raises(LakeGateError, match="主键重复"):
        validate_merged_lake(
            merged, ["id"], dataset_name="客户", write_mode=meta["mode"])


def test_replayed_native_values_match_persisted_snapshot_semantics():
    replayed = [{
        "order_id": "O-1002",
        "amount": 86000,
        "risk_score": 78,
        "violation_flag": True,
        "metadata": {"地区": "华北", "risk": 78},
        "tags": ["延期", "重点"],
        "note": None,
        "content": b"not-tabular-data",
    }]
    persisted = _parse_stored_rows(
        rows_to_parquet_bytes(replayed), limit=None)
    assert persisted == [{
        "order_id": "O-1002",
        "amount": "86000",
        "risk_score": "78",
        "violation_flag": "True",
        "metadata": '{"地区": "华北", "risk": 78}',
        "tags": '["延期", "重点"]',
        "note": "",
    }]

    impact = compute_lake_impact(persisted, replayed, ["order_id"])
    assert impact["updated_count"] == 0
    assert impact["unchanged_count"] == 1

    merged, meta = merge_rows(
        persisted, replayed, {"mode": "append_dedup"})
    assert meta["mode"] == "append_dedup"
    assert merged == persisted


def test_resolve_target_uses_pipeline_and_output_key_even_after_rename(db):
    owned = Dataset(
        id="curated-owned", name="旧名字 curated", kind="curated",
        schema_json={"pipeline_id": "pipe-1", "output_key": "default"})
    db.add(owned)
    db.commit()

    target, proposed_name = resolve_curated_target(
        db, _pipeline("pipe-1", "新名字"),
        {"dataset_id": None, "filename": "x"}, False)
    assert target.id == owned.id
    assert proposed_name == "新名字 curated"


def test_same_name_owned_by_archived_pipeline_gets_distinct_asset_name(db):
    old = Dataset(
        id="curated-old", name="供应商同步 curated", kind="curated",
        schema_json={"pipeline_id": "archived-pipe", "output_key": "default"})
    db.add(old)
    db.commit()

    target, proposed_name = resolve_curated_target(
        db, _pipeline("new-pipe", "供应商同步"),
        {"dataset_id": None, "filename": "x"}, False)
    assert target is None
    assert proposed_name != old.name
    assert proposed_name.startswith("供应商同步 curated [")


def test_legacy_unowned_dataset_is_only_adopted_through_explicit_target_binding(db):
    legacy = Dataset(
        id="legacy-curated", name="历史管道 curated", kind="curated",
        schema_json={"primary_key": "id"})
    db.add(legacy)
    db.commit()

    target, _ = resolve_curated_target(
        db, _pipeline("pipe-legacy", "历史管道", [legacy.id]),
        {"dataset_id": None, "filename": "x"}, False)
    assert target.id == legacy.id

    target, proposed_name = resolve_curated_target(
        db, _pipeline("another-pipe", "历史管道"),
        {"dataset_id": None, "filename": "x"}, False)
    assert target is None
    assert proposed_name != legacy.name


def test_pipeline_append_duplicate_pk_fails_before_new_version_is_written(db, monkeypatch):
    from app.data_channel.datasets import service as dataset_service_module

    storage = _Storage()
    monkeypatch.setattr(dataset_service_module, "get_storage_service", lambda: storage)
    pl = _pipeline("pipe-append", "订单")
    pl.column_definitions = []
    source = {"dataset_id": None, "filename": "orders", "route": "A"}
    ctx = SimpleNamespace(rows_in=1, meta={})
    svc = DatasetService(db, storage=storage)

    first = _save_curated_dataset(
        db, svc, pl, source, [{"order_id": "A-1", "amount": "10"}], ctx, False,
        write_opts={"mode": "append", "primary_key": "order_id", "skip_empty": False})
    pl.target_curated_ids = [first["curated_dataset_id"]]

    with pytest.raises(LakeGateError, match="合并后的全量数据"):
        _save_curated_dataset(
            db, svc, pl, source, [{"order_id": "A-1", "amount": "12"}], ctx, False,
            write_opts={"mode": "append", "primary_key": "order_id", "skip_empty": False})

    versions = db.query(DatasetVersion).filter(
        DatasetVersion.dataset_id == first["curated_dataset_id"]).all()
    assert len(versions) == 1
