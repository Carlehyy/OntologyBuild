"""版本血缘指针摘除与删除死结回归测试。

v2_pipeline_runs.dataset_version_id 只写不读，但 FK（NO ACTION）曾让：
- keep 版本清理在 best-effort 兜底下静默失效；
- 成品数据集删除 500。

流水线删除已改为统一归档语义（不物理删除），生产者 FK 不再构成删除死结。
"""
from app.data_channel.curated import lifecycle_service
from app.data_channel.datasets.service import DatasetService, rows_to_parquet_bytes
from app.data_channel.pipelines.management_service import delete_pipeline
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.pipeline import Pipeline, PipelineRun


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


def _make_curated_with_versions(db, n: int):
    svc = DatasetService(db, storage=_Storage())
    ds = svc.create_dataset(name="订单 curated", kind="curated", schema_json={})
    for i in range(1, n + 1):
        svc.create_version(
            ds.id, rows_to_parquet_bytes([{"order_id": f"O-{i}"}]),
            rowcount=1, schema_json={})
    return ds


def test_prune_detaches_run_lineage_instead_of_silent_failure(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "dataset_version_keep", 3)
    db.add(Pipeline(id="pipe-detach", name="流水线", spec={},
                    status="published", enabled=True))
    ds = _make_curated_with_versions(db, 2)
    pinned_v1 = db.query(DatasetVersion).filter(
        DatasetVersion.dataset_id == ds.id, DatasetVersion.version_no == 1).one()
    db.add(PipelineRun(id="run-detach", pipeline_id="pipe-detach",
                       status="success", dataset_version_id=pinned_v1.id))
    db.commit()

    # 再产 2 个版本触发清理：v1 被 run 引用，修复前清理静默失败
    svc = DatasetService(db, storage=_Storage())
    for i in range(3, 5):
        svc.create_version(
            ds.id, rows_to_parquet_bytes([{"order_id": f"O-{i}"}]),
            rowcount=1, schema_json={})

    remaining = db.query(DatasetVersion).filter(
        DatasetVersion.dataset_id == ds.id).count()
    assert remaining == 3
    run = db.query(PipelineRun).get("run-detach")
    assert run is not None and run.dataset_version_id is None


def test_delete_curated_detaches_run_lineage(db):
    ds = _make_curated_with_versions(db, 1)
    db.add(Pipeline(id="pipe-detach2", name="流水线2", spec={},
                    status="published", enabled=True))
    db.commit()
    ver = db.query(DatasetVersion).filter(
        DatasetVersion.dataset_id == ds.id).one()
    db.add(PipelineRun(id="run-detach2", pipeline_id="pipe-detach2",
                       status="success", dataset_version_id=ver.id))
    db.commit()

    lifecycle_service.delete_curated(db, ds.id, force=False)

    assert db.query(Dataset).get(ds.id) is None
    run = db.query(PipelineRun).get("run-detach2")
    assert run is not None and run.dataset_version_id is None


def test_delete_pipeline_archives_even_with_produced_curated(db):
    """归档语义下产物不再阻断删除：不物理删除，producer FK 不会触发死结。"""
    db.add(Pipeline(id="pipe-producer", name="生产者流水线", spec={},
                    status="published", enabled=True))
    db.add(Dataset(id="curated-produced", name="订单 curated", kind="curated",
                   schema_json={}, producer_pipeline_id="pipe-producer",
                   output_key="default"))
    db.commit()

    result = delete_pipeline(
        "pipe-producer", db,
        is_n8n_pipeline_fn=lambda _p: False,
        pipeline_task_refs_fn=lambda _db, _pid: [],
        reject_sync_chain_refs_fn=lambda *_a, **_kw: None,
    )

    assert result["status"] == "archived"
    archived = db.query(Pipeline).get("pipe-producer")
    assert archived.status == "archived"
    assert archived.enabled is False


def test_delete_pipeline_archives_pipeline_without_produced_assets(db):
    db.add(Pipeline(id="pipe-free", name="自由流水线", spec={},
                    status="draft", enabled=False))
    db.commit()

    result = delete_pipeline(
        "pipe-free", db,
        is_n8n_pipeline_fn=lambda _p: False,
        pipeline_task_refs_fn=lambda _db, _pid: [],
        reject_sync_chain_refs_fn=lambda *_a, **_kw: None,
    )
    assert result["status"] == "archived"
    archived = db.query(Pipeline).get("pipe-free")
    assert archived is not None and archived.status == "archived"
