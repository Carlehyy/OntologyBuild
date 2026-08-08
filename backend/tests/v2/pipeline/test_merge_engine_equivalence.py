"""合并引擎等价性测试：merge_engine（DuckDB 下推）vs 参考实现（merge.py）。

覆盖矩阵：write_mode × 主键（无/单列/复合）× 软删除 × 空增量 × 空基座 ×
值类型混合（int/float/bool/None/嵌套 dict/list/中文/超长字符串）× 主键违例
× 基座格式（parquet/csv/json）。每个用例同时跑 legacy 链（merge_rows +
validate_upsert_base + validate_merged_lake + compute_lake_impact +
rows_to_parquet_bytes）与新引擎，断言：

- 合并读回行集精确相等（顺序敏感）；物化合并行逐 dict 相等
- merge_meta 相等；columns_typed 相等
- lake_impact 除 __deleted_at__ 外逐键相等（样本内容+顺序）
- 违例场景两边抛同类型同文案错误

唯一掩码项：__deleted_at__ 时间戳——逐次运行本就不同（legacy 逐行取
utcnow()，引擎一次运行共用一个），不属于可观测语义差异。
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from app.data_channel.datasets.lake_gate import (
    LakeGateError,
    infer_columns_typed,
    validate_merged_lake,
    validate_upsert_base,
)
from app.data_channel.datasets.service import (
    DatasetService,
    _parse_stored_rows,
    rows_to_csv_bytes,
    rows_to_parquet_bytes,
)
from app.data_channel.pipeline_tasks.merge import compute_lake_impact, merge_rows
from app.data_channel.pipeline_tasks.merge_engine import merge_lake_increment
from app.models.v2.dataset import Dataset, DatasetVersion
from app.tasks.v2.pipeline_run import _save_curated_dataset

DATASET_NAME = "等价数据集"


def _scrub(value):
    """递归掩码 __deleted_at__ 时间戳，其余逐字节比较。"""
    if isinstance(value, dict):
        return {k: ("<ts>" if k == "__deleted_at__" else _scrub(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _base_bytes(rows: list[dict] | None, fmt: str) -> bytes | None:
    if rows is None:
        return None
    if fmt == "parquet":
        return rows_to_parquet_bytes(rows)
    if fmt == "csv":
        cols: list[str] = []
        seen: set[str] = set()
        for r in rows:
            for k in r:
                if k != "content" and k not in seen:
                    seen.add(k)
                    cols.append(k)
        return rows_to_csv_bytes(rows, cols)
    if fmt == "json":
        return json.dumps(rows, ensure_ascii=False).encode("utf-8")
    raise AssertionError(f"未知基座格式 {fmt}")


def _legacy_chain(base_rows: list[dict], new_rows: list[dict], mode: str,
                  pk_cols: list[str], soft: str | None):
    """复刻 _save_curated_dataset_in_lock 的合并段（参考实现路径）。"""
    new_rows = copy.deepcopy(new_rows)
    opts: dict = {"mode": mode, "primary_key": ",".join(pk_cols)}
    if soft:
        opts["soft_delete_column"] = soft
    old = [] if mode == "overwrite" else base_rows
    if mode == "upsert":
        validate_upsert_base(old, pk_cols, dataset_name=DATASET_NAME)
    merged, meta = merge_rows(old, new_rows, opts)
    validate_merged_lake(merged, pk_cols, dataset_name=DATASET_NAME,
                         write_mode=meta["mode"])
    impact = compute_lake_impact(base_rows, merged, pk_cols)
    typed = infer_columns_typed(merged)
    blob = rows_to_parquet_bytes(merged)
    readback = _parse_stored_rows(blob, limit=None) if blob else []
    return {
        "meta": meta, "impact": impact, "typed": typed, "blob_empty": not blob,
        "readback": readback, "merged": merged, "mutated": new_rows,
        "rowcount": len(merged),
    }


def _engine_chain(base_bytes: bytes | None, new_rows: list[dict], mode: str,
                  pk_cols: list[str], soft: str | None):
    new_rows = copy.deepcopy(new_rows)
    opts: dict = {"mode": mode, "primary_key": ",".join(pk_cols)}
    if soft:
        opts["soft_delete_column"] = soft
    outcome = merge_lake_increment(
        base_bytes=base_bytes, new_rows=new_rows, write_opts=opts,
        pk_cols=pk_cols, dataset_name=DATASET_NAME, dataset_id="ds-eq",
        base_version_no=7, need_merged_rows=True)
    readback = (_parse_stored_rows(outcome.parquet_bytes, limit=None)
                if outcome.parquet_bytes else [])
    return {
        "meta": outcome.merge_meta, "impact": outcome.lake_impact,
        "typed": outcome.columns_typed, "blob_empty": not outcome.parquet_bytes,
        "readback": readback, "merged": outcome.merged_rows,
        "mutated": new_rows, "rowcount": outcome.rowcount,
    }


def assert_equivalent(base_rows, new_rows, mode, pk_cols, soft=None,
                      base_format="parquet"):
    """同一场景双跑：先 legacy 后引擎；报错比类型与文案，否则比全部产物。"""
    base_bytes = _base_bytes(base_rows, base_format)
    parsed_base = _parse_stored_rows(base_bytes, limit=None) if base_bytes else []

    legacy_exc = engine_exc = None
    legacy = engine = None
    try:
        legacy = _legacy_chain(parsed_base, new_rows, mode, pk_cols, soft)
    except Exception as exc:  # noqa: BLE001 — 等价性比较需要捕获任意类型
        legacy_exc = exc
    try:
        engine = _engine_chain(base_bytes, new_rows, mode, pk_cols, soft)
    except Exception as exc:  # noqa: BLE001
        engine_exc = exc

    if legacy_exc is not None or engine_exc is not None:
        assert type(legacy_exc) is type(engine_exc), (
            f"错误类型不一致：legacy={type(legacy_exc).__name__} "
            f"engine={type(engine_exc).__name__}")
        assert str(legacy_exc) == str(engine_exc), (
            f"错误文案不一致：\nlegacy={legacy_exc}\nengine={engine_exc}")
        return

    assert engine["meta"] == legacy["meta"], "merge_meta 不一致"
    assert engine["rowcount"] == legacy["rowcount"], "rowcount 不一致"
    assert engine["blob_empty"] == legacy["blob_empty"], "空快照语义不一致"
    assert _scrub(engine["readback"]) == _scrub(legacy["readback"]), \
        "快照读回行集不一致（顺序敏感）"
    assert _scrub(engine["merged"]) == _scrub(legacy["merged"]), \
        "物化合并行不一致"
    assert engine["typed"] == legacy["typed"], "columns_typed 不一致"
    assert _scrub(engine["impact"]) == _scrub(legacy["impact"]), \
        "lake_impact 不一致"
    assert _scrub(engine["mutated"]) == _scrub(legacy["mutated"]), \
        "增量行就地打标不一致"


# ── 共享数据 ───────────────────────────────────────────────────
_LONG = "长" * 210          # 超 200 字符，触发 _slim_row 截断
_MIXED_BASE = [
    {"id": "1", "i": "86000", "f": "78.5", "b": "True", "n": "",
     "d": '{"地区": "华北"}', "l": '["延期"]', "c": "中文", "long": _LONG},
    {"id": "2", "i": "2", "f": "", "b": "False", "n": "x",
     "d": "", "l": "", "c": "", "long": ""},
    {"id": "3", "i": "3", "f": "0.5", "b": "", "n": "",
     "d": "", "l": '[]', "c": "值", "long": "短"},
]
_MIXED_NEW = [
    # 与基座 id=1 内容逐项等价（原生类型 vs 存量字符串）→ unchanged
    {"id": "1", "i": 86000, "f": 78.5, "b": True, "n": None,
     "d": {"地区": "华北"}, "l": ["延期"], "c": "中文", "long": _LONG},
    # id=2 内容变化 → updated
    {"id": "2", "i": 2, "f": 9.25, "b": False, "n": "x",
     "d": {"k": [1, 2]}, "l": [], "c": "改", "long": "y" * 260},
    # id=9 新增
    {"id": "9", "i": None, "f": None, "b": None, "n": None,
     "d": None, "l": None, "c": None, "long": None},
]

_VALID_CASES = [
    # ── 四模式 × 主键（无/单列/复合）──────────────
    pytest.param("overwrite", [], None, "parquet", id="overwrite-nopk"),
    pytest.param("overwrite", ["id"], None, "parquet", id="overwrite-singlepk"),
    pytest.param("overwrite", ["id", "c"], None, "parquet", id="overwrite-compositepk"),
    pytest.param("append", [], None, "parquet", id="append-nopk"),
    pytest.param("append", ["id"], None, "parquet", id="append-singlepk"),
    pytest.param("append", ["id", "c"], None, "parquet", id="append-compositepk"),
    pytest.param("append_dedup", [], None, "parquet", id="dedup-nopk"),
    pytest.param("append_dedup", ["id"], None, "parquet", id="dedup-singlepk"),
    pytest.param("append_dedup", ["id", "c"], None, "parquet", id="dedup-compositepk"),
    pytest.param("upsert", [], None, "parquet", id="upsert-nopk"),
    pytest.param("upsert", ["id"], None, "parquet", id="upsert-singlepk"),
    pytest.param("upsert", ["id", "c"], None, "parquet", id="upsert-compositepk"),
    # ── upsert × 软删除 ─────────────────────────
    pytest.param("upsert", ["id"], "b", "parquet", id="upsert-soft-boolcol"),
    pytest.param("upsert", [], "b", "parquet", id="upsert-nopk-soft"),
    # ── 历史基座格式 ────────────────────────────
    pytest.param("append", [], None, "csv", id="append-nopk-csvbase"),
    pytest.param("upsert", ["id"], "b", "csv", id="upsert-soft-csvbase"),
    pytest.param("append_dedup", [], None, "json", id="dedup-nopk-jsonbase"),
    pytest.param("upsert", ["id"], None, "json", id="upsert-singlepk-jsonbase"),
]


@pytest.mark.parametrize(
    "mode,pk_cols,soft,base_format", _VALID_CASES)
def test_equivalence_mixed_dataset(mode, pk_cols, soft, base_format):
    """混合类型数据集（含 unchanged/updated/added 各形态）双跑等价。"""
    base = _MIXED_BASE
    new = _MIXED_NEW
    if base_format in ("csv", "json"):
        # 历史格式基座由文本/JSON 来回，值类型以解析结果为准
        base = _parse_stored_rows(_base_bytes(_MIXED_BASE, base_format), limit=None)
    if soft == "b":
        # 软删除列取值覆盖 truthy 全集与 falsy/空值
        new = [dict(r, b=v) for r, v in zip(
            _MIXED_NEW, ["False", "是", None])]
        base = [dict(r, b=v) for r, v in zip(
            base, ["0", "yes", ""])][:len(base)]
    assert_equivalent(base, new, mode, pk_cols, soft, base_format)


# ── 空增量 / 空基座 ─────────────────────────────────────────
@pytest.mark.parametrize("mode", ["overwrite", "append", "append_dedup", "upsert"])
def test_empty_increment(mode):
    assert_equivalent(_MIXED_BASE, [], mode, ["id"])


@pytest.mark.parametrize("mode", ["overwrite", "append", "append_dedup", "upsert"])
def test_empty_base(mode):
    assert_equivalent(None, _MIXED_NEW, mode, ["id"])
    assert_equivalent([], _MIXED_NEW, mode, ["id"])  # b"" 基座（空快照）


def test_both_empty():
    assert_equivalent(None, [], "append", ["id"])
    assert_equivalent([], [], "upsert", ["id"], soft="b")


# ── 结构/语义边角 ───────────────────────────────────────────
def test_upsert_column_vanishes_when_all_base_rows_superseded():
    """基座行全被取代时，仅基座携带的列从快照与列清单中消失。"""
    base = [{"id": "1", "gone": "g"}, {"id": "2", "gone": "g2"}]
    new = [{"id": "1", "v": 1}, {"id": "2", "v": 2}]
    assert_equivalent(base, new, "upsert", ["id"])


def test_upsert_soft_delete_resurrect_and_previous_markers():
    """基座含历史软删除标记列：仍删除的刷新标记，未删除的摘除（复活）。"""
    base = [
        {"id": "1", "v": "a", "flag": "1",
         "__deleted__": "True", "__deleted_at__": "2024-01-01T00:00:00"},
        {"id": "2", "v": "b", "flag": "0",
         "__deleted__": "True", "__deleted_at__": "2024-01-01T00:00:00"},
    ]
    new = [
        {"id": "1", "v": "a2", "flag": "true"},   # 仍删除：刷新时间戳
        {"id": "2", "v": "b2", "flag": "0"},      # 复活：摘除标记
        {"id": "3", "v": "c", "flag": "no"},
    ]
    assert_equivalent(base, new, "upsert", ["id"], soft="flag")


def test_append_dedup_heterogeneous_keys_and_cross_side_duplicates():
    """整行去重：缺键与空串不等价；跨基座/增量重复保留基座首次出现。"""
    base = [{"a": "1"}, {"a": "1", "b": ""}, {"a": "2", "b": "x"}]
    new = [{"a": "1"}, {"a": "3", "b": None}, {"a": "1", "b": ""}, {"b": "only"}]
    assert_equivalent(base, new, "append_dedup", [])


def test_upsert_native_int_pk_matches_stored_string():
    """增量原生 int 主键与湖中字符串主键按 str() 文本匹配。"""
    base = [{"id": "86000", "v": "old"}]
    new = [{"id": 86000, "v": "new"}]
    assert_equivalent(base, new, "upsert", ["id"])


def test_increment_new_column_and_content_column():
    """content 列不进快照/签名/列清单；新增列按首现序追加。"""
    base = [{"id": "1", "v": "a"}]
    new = [
        {"id": "2", "extra": "新列", "content": b"binary"},
        {"id": "1", "v": "a"},
    ]
    assert_equivalent(base, new, "append_dedup", [])
    assert_equivalent(base, new, "append", [])
    assert_equivalent(base, new, "upsert", ["id"])


def test_soft_delete_truthy_spectrum():
    """truthy 词表逐项 + 大小写/空白/None/布尔/数字变体。"""
    base = [{"id": str(i), "flag": v} for i, v in enumerate([
        "1", "true", "TRUE", "yes", "y", "t", "是", "删除", "已删除",
        "0", "false", "no", "", "  ", " True ", "2", "真", "none"])]
    new = [{"id": "100", "flag": True}, {"id": "101", "flag": 1},
           {"id": "102", "flag": None}, {"id": "103", "flag": False}]
    assert_equivalent(base, new, "upsert", ["id"], soft="flag")


def test_impact_sample_truncation_over_50():
    """added 超 50：计数完整、样本截断、sample_truncated 置位、顺序敏感。"""
    base = [{"id": f"b{i}", "v": "x"} for i in range(3)]
    new = [{"id": f"n{i}", "v": i} for i in range(60)]
    assert_equivalent(base, new, "upsert", ["id"])
    assert_equivalent(base, new, "append", [])
    assert_equivalent(base, new, "overwrite", ["id"])


def test_updated_sample_slim_truncation():
    """updated 样本的 before/after 超长值截断一致。"""
    base = [{"id": "1", "v": "旧" * 250}]
    new = [{"id": "1", "v": "新" * 250}]
    assert_equivalent(base, new, "upsert", ["id"])


def test_overwrite_impact_with_duplicate_keys_in_base():
    """overwrite 不校验基座：审计 diff 的 before_map 末行覆盖 + 首现序。"""
    base = [{"id": "1", "v": "a"}, {"id": "1", "v": "b"}, {"id": "2", "v": "c"}]
    new = [{"id": "1", "v": "a"}, {"id": "9", "v": "d"}]
    assert_equivalent(base, new, "overwrite", ["id"])


def test_nopk_upsert_soft_delete_aliasing_semantics():
    """无主键 upsert：追加 + 软删除打标；before/after 共享打标对象（别名语义）。"""
    base = [{"v": "1", "flag": "0"}, {"v": "2", "flag": "是"}]
    new = [{"v": "3", "flag": "true"}, {"v": "4", "flag": "no"}]
    assert_equivalent(base, new, "upsert", [], soft="flag")


def test_increment_rows_carrying_marker_keys():
    """增量自带 __deleted__ 键：软删除生效时按判定重写或摘除。"""
    base = [{"id": "1", "v": "a", "flag": "0"}]
    new = [
        {"id": "2", "v": "b", "flag": "1", "__deleted__": "False",
         "__deleted_at__": "2020-01-01"},
        {"id": "3", "v": "c", "flag": "0", "__deleted__": "True"},
    ]
    assert_equivalent(base, new, "upsert", ["id"], soft="flag")
    # 软删除未生效时（非 upsert），标记键是普通业务列
    assert_equivalent(base, new, "append", [])


def test_content_only_rows_overwrite_empty_snapshot():
    """仅 content 列的行：快照为 b"" 但 rowcount 保留（与 legacy 一致）。"""
    assert_equivalent(None, [{"content": b"x"}, {"content": b"y"}], "overwrite", [])


def test_unicode_whitespace_pk_values():
    """主键值含 Unicode 空白：strip 语义复刻（\\xa0/全角空格/\\x1c）。"""
    base = [{"id": "\xa01\xa0", "v": "a"}, {"id": "　2　", "v": "b"},
            {"id": "\x1c3\x1d", "v": "c"}]
    new = [{"id": "1", "v": "hit"}]
    assert_equivalent(base, new, "append", ["id"])  # 合并后 strip 重复 → 报错
    assert_equivalent(base, new, "upsert", ["id"])  # 去重按未 strip 文本 → 保留


def test_long_snapshot_order_stable():
    """较大基座的读回顺序逐行一致（DuckDB read_parquet 保序）。"""
    base = [{"id": f"k{i:06d}", "v": f"值{i % 7}", "n": str(i)} for i in range(30_000)]
    new = [{"id": f"k{i:06d}", "v": "新", "n": "0"} for i in range(0, 30_000, 997)]
    assert_equivalent(base, new, "upsert", ["id"])
    assert_equivalent(base, new, "append_dedup", [])


# ── 主键违例：两边抛同类型同文案 ─────────────────────────────
def test_upsert_base_missing_pk_column():
    base = [{"x": "1", "y": "2"}]
    assert_equivalent(base, [{"id": "1"}], "upsert", ["id"])


def test_upsert_base_empty_pk_value():
    base = [{"id": "1"}, {"id": ""}, {"id": "  "}]
    assert_equivalent(base, [{"id": "3"}], "upsert", ["id"])


def test_upsert_base_duplicate_pk():
    base = [{"id": "1", "v": "a"}, {"id": "2"}, {"id": "1", "v": "b"}]
    assert_equivalent(base, [{"id": "3"}], "upsert", ["id"])


def test_upsert_base_duplicate_pk_composite():
    base = [{"t": "a", "s": "1"}, {"t": "a", "s": "1"}]
    assert_equivalent(base, [{"t": "b", "s": "1"}], "upsert", ["t", "s"])


def test_append_merged_duplicate_pk_blocked():
    base = [{"id": "1", "v": "old"}]
    new = [{"id": "1", "v": "new"}]
    assert_equivalent(base, new, "append", ["id"])
    assert_equivalent(base, new, "append_dedup", ["id"])  # 整行不同 → 仍重复


def test_upsert_whitespace_only_pk_difference_blocked():
    """upsert 去重按未 strip 文本保留两行，合并后 strip 唯一性硬失败。"""
    base = [{"id": " 1", "v": "old"}]
    new = [{"id": "1", "v": "new"}]
    assert_equivalent(base, new, "upsert", ["id"])


def test_merged_missing_pk_after_full_increment_dedup():
    """增量被整行去重全部吸收且基座缺主键列：合并后校验报缺列。"""
    base = [{"a": "1", "b": "x"}]
    new = [{"a": "1", "b": "x"}]
    assert_equivalent(base, new, "append_dedup", ["id"])


# ── 经 _save_curated_dataset 的端到端接线 ────────────────────
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


def test_save_curated_dataset_upsert_roundtrip_via_engine(db, monkeypatch):
    """接线冒烟：两次 upsert 经引擎落湖，读回行集/统计/契约与预期一致。"""
    from app.data_channel.datasets import service as dataset_service_module

    storage = _Storage()
    monkeypatch.setattr(dataset_service_module, "get_storage_service", lambda: storage)
    pl = SimpleNamespace(id="pipe-eq", name="订单", target_curated_ids=[],
                         column_definitions=[])
    source = {"dataset_id": None, "filename": "orders", "route": "A"}
    ctx = SimpleNamespace(rows_in=2, meta={})
    svc = DatasetService(db, storage=storage)

    first = _save_curated_dataset(
        db, svc, pl, source,
        [{"order_id": "A-1", "amount": 10, "flag": "0"},
         {"order_id": "A-2", "amount": 20, "flag": "1"}],
        ctx, False,
        write_opts={"mode": "upsert", "primary_key": "order_id",
                    "soft_delete_column": "flag", "skip_empty": False})
    pl.target_curated_ids = [first["curated_dataset_id"]]
    assert first["merge"] == {"mode": "upsert", "rows_before": 0,
                              "rows_new": 2, "rows_after": 2}
    assert first["lake_rows"] == 2
    assert first["lake_impact"]["added_count"] == 2

    second = _save_curated_dataset(
        db, svc, pl, source,
        [{"order_id": "A-2", "amount": 21, "flag": "0"},
         {"order_id": "A-3", "amount": 30, "flag": "0"}],
        ctx, False,
        write_opts={"mode": "upsert", "primary_key": "order_id",
                    "soft_delete_column": "flag", "skip_empty": False})
    assert second["merge"] == {"mode": "upsert", "rows_before": 2,
                               "rows_new": 2, "rows_after": 3}
    assert second["lake_rows"] == 3

    rows = DatasetService(db, storage=storage).load_all_rows(
        first["curated_dataset_id"])
    # 第二版无任何删除行：软删除标记列整体消失（legacy 对非删除行 pop 标记键）
    assert rows == [
        {"order_id": "A-1", "amount": "10", "flag": "0"},
        # A-2 被增量取代：内容更新且摘除软删除标记（复活）
        {"order_id": "A-2", "amount": "21", "flag": "0"},
        {"order_id": "A-3", "amount": "30", "flag": "0"},
    ]
    ds = db.query(Dataset).filter(Dataset.id == first["curated_dataset_id"]).one()
    assert ds.schema_json["primary_key"] == "order_id"
    assert ds.schema_json["columns"] == ["order_id", "amount", "flag"]
    versions = db.query(DatasetVersion).filter(
        DatasetVersion.dataset_id == ds.id).count()
    assert versions == 2
