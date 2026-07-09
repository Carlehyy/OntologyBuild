"""Parquet 产物快照存储测试。

覆盖「CSV→Parquet」切换的三条安全底线：
1. 往返语义与旧 CSV 完全一致（全列字符串化、None/dict/bytes/缺列规范、content 跳过）
2. 分页按 record-batch 下推（offset/limit 正确、只物化窗口）
3. 多格式共存：存量 CSV 版本与新 Parquet 版本在同一数据集内都可读（惰性迁移）
"""
from __future__ import annotations

import csv
import io

import pytest

from app.data_channel.datasets.service import (
    DatasetService,
    _parse_stored_rows,
    rows_to_parquet_bytes,
)


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


# ── 1. 格式与魔数 ─────────────────────────────────────────────
def test_output_is_parquet_magic():
    data = rows_to_parquet_bytes([{"a": "1"}])
    assert data[:4] == b"PAR1" and data[-4:] == b"PAR1"


def test_empty_rows_is_empty_bytes():
    assert rows_to_parquet_bytes([]) == b""
    # 无有效列（仅 content）同样退化为空
    assert rows_to_parquet_bytes([{"content": b"xx"}]) == b""
    assert _parse_stored_rows(b"", limit=None) == []


# ── 2. 往返语义精确复刻 CSV 入湖口径 ──────────────────────────
def test_roundtrip_stringifies_like_csv():
    rows = [
        {"id": 1, "name": "Alice", "score": 9.5, "active": True,
         "meta": {"k": "v"}, "note": None},
        {"id": 2, "name": "Bob", "tags": ["a", "b"]},  # 缺列 + 新列
    ]
    back = _parse_stored_rows(rows_to_parquet_bytes(rows), limit=None)
    # 列序按首现；缺列补 ""；标量/布尔/浮点 str 化；dict/list 压 JSON；None→""
    assert list(back[0].keys()) == ["id", "name", "score", "active", "meta", "note", "tags"]
    assert back[0] == {"id": "1", "name": "Alice", "score": "9.5", "active": "True",
                       "meta": '{"k": "v"}', "note": "", "tags": ""}
    assert back[1] == {"id": "2", "name": "Bob", "score": "", "active": "",
                       "meta": "", "note": "", "tags": '["a", "b"]'}


def test_bytes_column_placeholder_and_content_skipped():
    rows = [{"data": b"abc", "content": b"\x00\x01\x02", "name": "x"}]
    back = _parse_stored_rows(rows_to_parquet_bytes(rows), limit=None)
    assert "content" not in back[0]          # 二进制 content 列不入湖
    assert back[0]["data"] == "<3 bytes>"    # 其余 bytes 列压占位符
    assert back[0]["name"] == "x"


def test_unicode_preserved():
    rows = [{"名称": "中文值", "emoji": "🚀"}]
    back = _parse_stored_rows(rows_to_parquet_bytes(rows), limit=None)
    assert back[0] == {"名称": "中文值", "emoji": "🚀"}


# ── 3. 分页下推（offset / limit）─────────────────────────────
def test_pagination_offset_limit():
    rows = [{"i": str(k)} for k in range(100)]
    blob = rows_to_parquet_bytes(rows)

    assert [r["i"] for r in _parse_stored_rows(blob, limit=10, offset=0)] == [str(k) for k in range(10)]
    assert [r["i"] for r in _parse_stored_rows(blob, limit=10, offset=95)] == [str(k) for k in range(95, 100)]
    assert len(_parse_stored_rows(blob, limit=None, offset=0)) == 100
    assert [r["i"] for r in _parse_stored_rows(blob, limit=None, offset=50)] == [str(k) for k in range(50, 100)]
    assert _parse_stored_rows(blob, limit=10, offset=200) == []  # offset 越界


def test_uncapped_read_returns_everything():
    rows = [{"i": str(k)} for k in range(2500)]  # 跨多个 batch
    back = _parse_stored_rows(rows_to_parquet_bytes(rows), limit=None)
    assert len(back) == 2500 and back[0]["i"] == "0" and back[-1]["i"] == "2499"


# ── 4. 端到端：写版本 → 读回 ──────────────────────────────────
def test_create_version_load_and_preview(svc):
    rows = [{"id": str(k), "v": f"row-{k}"} for k in range(250)]
    ds = svc.create_dataset("parquet产物", "curated")
    svc.create_version(ds.id, rows_to_parquet_bytes(rows), rowcount=len(rows))

    loaded = svc.load_all_rows(ds.id)
    assert len(loaded) == 250 and loaded[0]["v"] == "row-0" and loaded[-1]["v"] == "row-249"

    page = svc.preview(ds.id, None, limit=5, offset=10)
    assert [r["id"] for r in page] == ["10", "11", "12", "13", "14"]


# ── 5. 多格式共存（惰性迁移）─────────────────────────────────
def test_csv_and_parquet_versions_coexist(svc):
    """老版本是 CSV、新版本是 Parquet：最新走 Parquet、历史 CSV 仍可读。

    这是滚动窗口惰性迁移的核心不变式——切换写格式不会让存量版本失联。
    """
    ds = svc.create_dataset("混合格式", "curated")
    svc.create_version(ds.id, _csv_bytes([{"id": "1", "v": "csv-old"}]), rowcount=1)   # v1 CSV
    svc.create_version(ds.id, rows_to_parquet_bytes([{"id": "2", "v": "pq-new"}]), rowcount=1)  # v2 Parquet

    assert svc.load_all_rows(ds.id) == [{"id": "2", "v": "pq-new"}]              # 最新 = Parquet
    assert svc.load_all_rows(ds.id, version_no=1) == [{"id": "1", "v": "csv-old"}]  # 历史 CSV 仍可读
