"""资产湖存储层加固测试：严格全量读 / 写锁 / 版本唯一 / 保留策略。

对应四个已确认的底层隐患：
1. 合并基座 100 万行静默截断 → load_all_rows 无上限
2. 读失败被吞成空基座（湖被静默清空）→ DatasetReadError 硬报错
3. 并发读改写丢更新 → v2_dataset_write_locks 行锁
4. 全量快照无限堆积 → dataset_version_keep 保留策略
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.data_channel.datasets.lock import DatasetLockTimeout, dataset_write_lock
from app.data_channel.datasets.service import DatasetReadError, DatasetService, _parse_stored_rows
from app.data_channel.pipeline_tasks.merge import load_latest_rows
from app.models.v2.dataset import DatasetVersion, DatasetWriteLock, MediaItem


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


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


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


def test_load_all_rows_raises_on_storage_failure(svc, storage):
    ds = svc.create_dataset("读失败", "structured")
    svc.create_version(ds.id, _csv_bytes([{"a": "1"}]), rowcount=1)
    storage.objects.clear()  # 模拟对象丢失/存储不可用
    with pytest.raises(DatasetReadError):
        svc.load_all_rows(ds.id)


def test_merge_base_propagates_read_failure(db, svc, storage, monkeypatch):
    """合并基座读失败必须抛错——静默空基座会让湖被本次增量覆盖。"""
    ds = svc.create_dataset("合并基座", "curated")
    svc.create_version(ds.id, _csv_bytes([{"a": "1"}]), rowcount=1)
    storage.objects.clear()
    # load_latest_rows 内部自建 DatasetService，注入同一个 FakeStorage
    monkeypatch.setattr("app.data_channel.datasets.service.get_storage_service", lambda: storage)
    with pytest.raises(DatasetReadError):
        load_latest_rows(db, ds.id)


def test_preview_stays_lenient_for_ui(svc, storage):
    ds = svc.create_dataset("预览容错", "structured")
    svc.create_version(ds.id, _csv_bytes([{"a": "1"}]), rowcount=1)
    storage.objects.clear()
    assert svc.preview(ds.id, None) == []  # UI 展示路径保持容错语义


# ── 2. 校验和覆盖全文 ─────────────────────────────────────────
def test_checksum_covers_full_content(svc):
    ds = svc.create_dataset("校验和", "structured")
    v1 = svc.create_version(ds.id, b"A" * 2048)
    v2 = svc.create_version(ds.id, b"A" * 1024 + b"B" * 1024)  # 前 1KB 相同
    assert v1.checksum != v2.checksum


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
    assert len(storage.deleted) == 2  # v1、v2 的对象已从存储删除
    assert svc.get_dataset(ds.id).latest_version_id == versions[-1].id
    # 最新版本内容完好
    assert svc.load_all_rows(ds.id) == [{"n": "4"}]


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
