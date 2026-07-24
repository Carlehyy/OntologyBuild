"""资产湖存储层加固测试：严格全量读 / 写锁 / 版本唯一 / 保留策略。

对应四个已确认的底层隐患：
1. 合并基座 100 万行静默截断 → load_all_rows 无上限
2. 读失败被吞成空基座（湖被静默清空）→ DatasetReadError 硬报错
3. 并发读改写丢更新 → v2_dataset_write_locks 行锁
4. 全量快照无限堆积 → dataset_version_keep 保留策略
"""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.data_channel.datasets.lock import DatasetLockTimeout, dataset_write_lock
from app.data_channel.datasets.router import _estimate_rowcount
from app.data_channel.datasets.service import (
    DatasetReadError,
    DatasetService,
    TabularParseError,
    _parse_stored_rows,
    stored_columns,
    version_has_content,
)
from app.data_channel.file_assets.models import PipelineFileAsset
from app.data_channel.pipeline_tasks.merge import load_latest_rows
from app.models.v2.dataset import DatasetVersion, DatasetWriteLock, MediaItem
from app.shared.storage import StorageService


class FakeStorage:
    """内存对象存储：隔离 MinIO 与本地文件系统。"""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str = "") -> str:
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data
        return uri

    def get_object(self, uri: str) -> bytes:
        if uri not in self.objects:
            raise FileNotFoundError(f"Object not found: {uri}")
        return self.objects[uri]

    def delete_object(self, uri: str) -> None:
        self.objects.pop(uri, None)
        self.deleted.append(uri)


def test_production_storage_failure_is_not_local_success():
    """A missing shared object store must fail before reporting an s3 URI."""
    storage = StorageService.__new__(StorageService)
    storage._available = False
    storage._client = None
    storage._allow_local_fallback = False

    with pytest.raises(RuntimeError, match="对象存储不可用"):
        storage.put_bytes("raw-datasets", "unsafe.bin", b"data")
    with pytest.raises(RuntimeError, match="对象存储不可用"):
        storage.get_object("s3://raw-datasets/unsafe.bin")


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _seed_legacy_version(db, dataset, storage, raw: bytes) -> DatasetVersion:
    """Create a pre-database version whose bytes live in object storage."""
    version = DatasetVersion(
        dataset_id=dataset.id,
        version_no=1,
        rowcount=1,
        storage_uri=storage.put_bytes(
            "raw-datasets", f"datasets/{dataset.id}/v1/data.bin", raw),
        checksum=hashlib.sha256(raw).hexdigest(),
    )
    db.add(version)
    db.flush()
    dataset.latest_version_id = version.id
    db.commit()
    return version


@pytest.fixture
def storage():
    return FakeStorage()


@pytest.fixture
def svc(db, storage):
    return DatasetService(db, storage=storage)


# ── 1. 严格全量读 ──────────────────────────────────────────────
def test_load_all_rows_empty_dataset_is_legal(svc):
    ds = svc.create_dataset("空数据集", "structured")
    assert svc.load_all_rows(ds.id) == []  # 无版本 = 合法初始状态，不是错误


def test_load_all_rows_returns_every_row(svc):
    rows = [{"id": str(i), "v": f"row-{i}"} for i in range(250)]
    ds = svc.create_dataset("全量读", "structured")
    svc.create_version(ds.id, _csv_bytes(rows), rowcount=len(rows))
    loaded = svc.load_all_rows(ds.id)
    assert len(loaded) == 250
    assert loaded[0]["v"] == "row-0" and loaded[-1]["v"] == "row-249"


def test_parse_stored_rows_supports_uncapped_limit():
    rows = [{"id": str(i)} for i in range(50)]
    assert len(_parse_stored_rows(_csv_bytes(rows), limit=None)) == 50
    assert len(_parse_stored_rows(_csv_bytes(rows), limit=10)) == 10


def test_csv_parser_supports_old_mac_newlines_and_quoted_multiline_cells():
    raw = 'id,note\rA1,"第一行\n第二行"\rA2,正常\r'.encode("utf-8")

    rows = _parse_stored_rows(raw, limit=None)

    assert stored_columns(raw) == ["id", "note"]
    assert rows == [
        {"id": "A1", "note": "第一行\n第二行"},
        {"id": "A2", "note": "正常"},
    ]


def test_legacy_xls_parser_preserves_integer_cells_and_rowcount(legacy_xls_bytes):
    assert legacy_xls_bytes.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    assert stored_columns(legacy_xls_bytes) == ["id", "quantity", "note"]

    rows = _parse_stored_rows(legacy_xls_bytes, limit=None)

    assert rows == [
        {"id": "A1", "quantity": 3, "note": "第一行\n第二行"},
        {"id": "A2", "quantity": 5, "note": "正常"},
    ]
    assert all(type(row["quantity"]) is int for row in rows)
    assert _estimate_rowcount(legacy_xls_bytes, "xls") == 2


def test_corrupt_xlsx_reports_excel_error_instead_of_csv_newline_error():
    with pytest.raises(TabularParseError, match="Excel 工作簿解析失败") as caught:
        _parse_stored_rows(b"PK\x03\x04not-an-xlsx\rwith-binary-data", limit=None)

    assert "new-line character" not in str(caught.value)


def test_corrupt_xls_reports_excel_error_instead_of_csv_newline_error():
    raw = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1not-an-xls\rwith-binary-data"
    with pytest.raises(TabularParseError, match="旧版 Excel 工作簿解析失败") as caught:
        _parse_stored_rows(raw, limit=None)

    assert "new-line character" not in str(caught.value)


def test_json_upload_rowcount_is_recorded_for_pipeline_validation():
    assert _estimate_rowcount(b'[{"id": 1}, {"id": 2}]', "json") == 2
    assert _estimate_rowcount(b'{"id": 1}', "json") == 1


def test_load_all_rows_raises_on_storage_failure(db, svc, storage):
    ds = svc.create_dataset("读失败", "structured")
    _seed_legacy_version(db, ds, storage, _csv_bytes([{"a": "1"}]))
    storage.objects.clear()  # 模拟对象丢失/存储不可用
    with pytest.raises(DatasetReadError):
        svc.load_all_rows(ds.id)


def test_merge_base_propagates_read_failure(db, svc, storage, monkeypatch):
    """合并基座读失败必须抛错——静默空基座会让湖被本次增量覆盖。"""
    ds = svc.create_dataset("合并基座", "curated")
    _seed_legacy_version(db, ds, storage, _csv_bytes([{"a": "1"}]))
    storage.objects.clear()
    # load_latest_rows 内部自建 DatasetService，注入同一个 FakeStorage
    monkeypatch.setattr("app.data_channel.datasets.service.get_storage_service", lambda: storage)
    monkeypatch.setattr(
        "app.data_channel.datasets.service.get_environment_storage_service",
        lambda: storage,
    )
    with pytest.raises(DatasetReadError):
        load_latest_rows(db, ds.id)


def test_preview_stays_lenient_for_ui(db, svc, storage):
    ds = svc.create_dataset("预览容错", "structured")
    _seed_legacy_version(db, ds, storage, _csv_bytes([{"a": "1"}]))
    storage.objects.clear()
    assert svc.preview(ds.id, None) == []  # UI 展示路径保持容错语义


# ── 2. 校验和覆盖全文 ─────────────────────────────────────────
def test_checksum_covers_full_content(svc):
    ds = svc.create_dataset("校验和", "structured")
    v1 = svc.create_version(ds.id, b"A" * 2048)
    v2 = svc.create_version(ds.id, b"A" * 1024 + b"B" * 1024)  # 前 1KB 相同
    assert v1.checksum != v2.checksum


@pytest.mark.parametrize("kind", ["structured", "semi", "curated"])
def test_tabular_versions_are_database_backed(svc, storage, kind):
    """数据资产版本必须落平台数据库，不能写入管理员 MinIO。"""
    ds = svc.create_dataset(f"数据库版本-{kind}", kind)
    v1 = svc.create_version(ds.id, _csv_bytes([{"id": "1"}]))
    v2 = svc.create_version(ds.id, _csv_bytes([{"id": "2"}]))

    assert v1.storage_uri is None and v2.storage_uri is None
    assert v1.data_blob == _csv_bytes([{"id": "1"}])
    assert v2.data_blob == _csv_bytes([{"id": "2"}])
    assert v1.data_size == len(v1.data_blob)
    assert v2.data_size == len(v2.data_blob)
    assert storage.objects == {}


def test_database_payload_stays_deferred_during_version_listing(db, svc):
    ds = svc.create_dataset("延迟载荷", "curated")
    svc.create_version(ds.id, _csv_bytes([{"id": "1"}]))
    dataset_id = ds.id
    db.expunge_all()

    listed = svc.list_versions(dataset_id)

    assert "data_blob" in inspect(listed[0]).unloaded
    assert version_has_content(listed[0]) is True
    assert "data_blob" in inspect(listed[0]).unloaded


def test_first_database_version_is_atomic_on_commit_failure(
    db, svc, monkeypatch,
):
    ds = svc.create_dataset(
        "数据库事务回滚", "structured", commit=False)
    dataset_id = ds.id

    def fail_commit():
        raise RuntimeError("database commit unavailable")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="database commit unavailable"):
        svc.create_version(
            dataset_id, _csv_bytes([{"id": "1"}]), _lock_held=True)

    assert db.query(DatasetVersion).filter_by(dataset_id=dataset_id).count() == 0
    assert svc.get_dataset(dataset_id) is None


def test_legacy_versions_fall_back_from_internal_to_managed_minio(db):
    internal = FakeStorage()
    managed = FakeStorage()
    svc = DatasetService(
        db, storage=managed, legacy_storages=[internal, managed])
    ds = svc.create_dataset("切换期间历史版本", "curated")
    raw = _csv_bytes([{"id": "1", "value": "external"}])
    version = _seed_legacy_version(db, ds, managed, raw)
    # A stale object under the same URI in the original endpoint must not mask
    # the valid regression-era copy in managed MinIO.
    internal.objects[version.storage_uri] = b"stale"

    assert svc.load_all_rows(ds.id) == [{"id": "1", "value": "external"}]


def test_legacy_missing_object_error_groups_identical_storage_failures(db):
    internal = FakeStorage()
    managed = FakeStorage()
    svc = DatasetService(
        db, storage=managed, legacy_storages=[internal, managed])
    ds = svc.create_dataset("历史对象已丢失", "curated")
    version = _seed_legacy_version(
        db, ds, internal, _csv_bytes([{"id": "1"}]))
    internal.objects.clear()

    with pytest.raises(DatasetReadError) as captured:
        svc.load_all_rows(ds.id)

    message = str(captured.value)
    assert f"数据集 {ds.id} v1 历史存储对象读取失败" in message
    assert "候选历史存储 1、候选历史存储 2" in message
    assert message.count(f"FileNotFoundError: Object not found: {version.storage_uri}") == 1


def test_unstructured_versions_remain_in_minio(svc, storage):
    ds = svc.create_dataset("文件版本", "unstructured")
    version = svc.create_version(ds.id, b"%PDF-test")

    assert version.data_blob is None
    assert version.data_size is None
    assert version.storage_uri in storage.objects
    assert f"/objects/{version.id}.bin" in version.storage_uri


def test_checksum_mismatch_is_strict_failure_but_preview_is_lenient(svc, storage):
    ds = svc.create_dataset("对象篡改", "structured")
    ver = svc.create_version(ds.id, _csv_bytes([{"id": "1", "value": "safe"}]))
    ver.data_blob = _csv_bytes([{"id": "1", "value": "tampered"}])
    svc._db.commit()

    with pytest.raises(DatasetReadError, match="校验和不匹配"):
        svc.load_all_rows(ds.id)
    assert svc.preview(ds.id, None) == []


# ── 3. 版本号唯一约束 ─────────────────────────────────────────
def test_duplicate_version_no_rejected(db, svc):
    ds = svc.create_dataset("唯一约束", "structured")
    db.add(DatasetVersion(dataset_id=ds.id, version_no=1))
    db.commit()
    db.add(DatasetVersion(dataset_id=ds.id, version_no=1))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ── 4. 版本保留策略 ───────────────────────────────────────────
def test_retention_prunes_old_snapshots(svc, storage, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dataset_version_keep", 3)

    ds = svc.create_dataset("保留策略", "curated")
    for i in range(5):
        svc.create_version(ds.id, _csv_bytes([{"n": str(i)}]), rowcount=1)

    versions = svc.list_versions(ds.id)
    assert [v.version_no for v in versions] == [3, 4, 5]
    assert storage.deleted == []  # 数据库存储随版本行一起删除，不产生 MinIO 清理
    assert svc.get_dataset(ds.id).latest_version_id == versions[-1].id
    # 最新版本内容完好
    assert svc.load_all_rows(ds.id) == [{"n": "4"}]


def test_retention_prunes_file_assets_bound_to_old_version(
    db, svc, storage, monkeypatch,
):
    """Version retention must not leave committed pipeline attachments in MinIO."""
    from app.config import settings

    monkeypatch.setattr(settings, "dataset_version_keep", 1)
    ds = svc.create_dataset("附件随版本回收", "curated")
    v1 = svc.create_version(ds.id, _csv_bytes([{"id": "old"}]), rowcount=1)
    file_uri = "s3://media/pipeline-files/test/committed.pdf"
    storage.objects[file_uri] = b"attachment"
    db.add(PipelineFileAsset(
        pipeline_id=None,
        workflow_id="wf-retention",
        invocation_id="run-retention",
        purpose="run",
        status="committed",
        idempotency_key="attachment-1",
        original_name="committed.pdf",
        object_key="pipeline-files/test/committed.pdf",
        storage_uri=file_uri,
        size=len(b"attachment"),
        content_type="application/pdf",
        sha256="a" * 64,
        dataset_version_id=v1.id,
    ))
    db.commit()

    svc.create_version(ds.id, _csv_bytes([{"id": "new"}]), rowcount=1)

    assert db.query(PipelineFileAsset).filter_by(dataset_version_id=v1.id).first() is None
    assert file_uri not in storage.objects
    assert file_uri in storage.deleted


def test_retention_spares_versions_with_media(db, svc, storage, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dataset_version_keep", 1)

    ds = svc.create_dataset("媒体豁免", "unstructured")
    v1 = svc.create_version(ds.id, b"doc-v1")
    db.add(MediaItem(dataset_version_id=v1.id, media_type="pdf", storage_uri="s3://media/a.pdf"))
    db.commit()
    svc.create_version(ds.id, b"doc-v2")
    svc.create_version(ds.id, b"doc-v3")

    remaining = {v.version_no for v in svc.list_versions(ds.id)}
    assert 1 in remaining      # 挂媒体的版本不清理
    assert 3 in remaining      # 最新版本保留
    assert 2 not in remaining  # 无媒体的旧版本被清理


def test_retention_spares_versions_bound_to_review(db, svc, storage, monkeypatch):
    """审核记录引用的不可变证据版本不能先被对象存储清理。"""
    from app.config import settings
    from app.models.v2.curated import CuratedReview

    monkeypatch.setattr(settings, "dataset_version_keep", 1)
    ds = svc.create_dataset("审核证据保留", "curated")
    v1 = svc.create_version(ds.id, _csv_bytes([{"id": "A", "v": "1"}]))
    db.add(CuratedReview(
        curated_dataset_id=ds.id,
        dataset_version_id=v1.id,
        status="approved",
    ))
    db.commit()

    svc.create_version(ds.id, _csv_bytes([{"id": "A", "v": "2"}]))
    svc.create_version(ds.id, _csv_bytes([{"id": "A", "v": "3"}]))

    remaining = {v.id for v in svc.list_versions(ds.id)}
    assert v1.id in remaining
    assert svc.load_all_rows(ds.id, v1.version_no) == [{"id": "A", "v": "1"}]


def test_retention_disabled_when_keep_is_zero(svc, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dataset_version_keep", 0)

    ds = svc.create_dataset("不清理", "structured")
    for i in range(4):
        svc.create_version(ds.id, _csv_bytes([{"n": str(i)}]))
    assert len(svc.list_versions(ds.id)) == 4


# ── 5. 数据集写锁 ─────────────────────────────────────────────
def test_write_lock_mutual_exclusion(db):
    bind = db.get_bind()
    with dataset_write_lock("curated::测试产物", bind=bind):
        with pytest.raises(DatasetLockTimeout):
            with dataset_write_lock("curated::测试产物", bind=bind,
                                    wait_timeout=0.3, poll_interval=0.05):
                pass


def test_write_lock_released_after_exit(db):
    bind = db.get_bind()
    with dataset_write_lock("curated::释放", bind=bind):
        pass
    # 正常退出后立刻可再次获取
    with dataset_write_lock("curated::释放", bind=bind, wait_timeout=1):
        pass


def test_write_lock_released_on_exception(db):
    bind = db.get_bind()
    with pytest.raises(ValueError):
        with dataset_write_lock("curated::异常", bind=bind):
            raise ValueError("业务失败")
    with dataset_write_lock("curated::异常", bind=bind, wait_timeout=1):
        pass


def test_write_lock_independent_keys_dont_block(db):
    bind = db.get_bind()
    with dataset_write_lock("curated::甲", bind=bind):
        with dataset_write_lock("curated::乙", bind=bind, wait_timeout=1):
            pass


def test_write_lock_stale_takeover(db):
    bind = db.get_bind()
    db.add(DatasetWriteLock(
        lock_key="curated::僵尸",
        owner="dead-process",
        acquired_at=datetime.now(timezone.utc) - timedelta(hours=1),
    ))
    db.commit()
    # 持有者早已超过 stale_after，可被接管
    with dataset_write_lock("curated::僵尸", bind=bind, wait_timeout=2, stale_after=60):
        holder = db.query(DatasetWriteLock).filter_by(lock_key="curated::僵尸").first()
        db.refresh(holder)
        assert holder.owner != "dead-process"
