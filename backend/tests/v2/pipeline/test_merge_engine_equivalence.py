"""合并等价性测试：lake_store 物理湖表 vs 参考实现（merge.py legacy 链）。

入湖运行时路径已从「merge_engine（DuckDB）合并 + 整份 Parquet 快照」切换为
lake_store 物理表行级 upsert；本矩阵钉住新旧链的可观测等价性。每个用例双跑：

- legacy 链（语义权威）：merge_rows + validate_merged_lake +
  compute_lake_impact + rows_to_parquet_bytes/_parse_stored_rows 读回；
- lake 链：overwrite 灌基座建表 → upsert_run（含列演化/软删除/撞键预检）
  → stream_rows 读回 + 由变更集生成 lake_impact（复用 pipeline_run 的
  _lake_impact_from_changeset，即 run.stats 的真实生成路径）。

断言：
- 读回行集按主键映射相等（有主键）或按整行 JSON 多重集相等（无主键）——
  物理行序不是契约；两链的值都已按快照文本语义规范化
- merge_meta 逐键相等；lake_impact 除 __deleted_at__ 时间戳与样本顺序外
  逐键相等（样本两侧排序后比较）；增量行就地打标一致
- 违例场景两边同抛 LakeGateError

相对 DuckDB 引擎时代矩阵的有意调整（物理表模型的语义边界，均经确认）：
- 基座格式矩阵（parquet/csv/json）不再需要：物理表没有基座字节；
- 基座主键违例（缺列/空值/重复）不再可达：物理表 PK 约束在建表/入库边界
  即拒绝，等价比对失去意义；
- 主键值的 Unicode 空白：物理表在入库边界即 strip（与审核 encode_row_pk
  同一身份口径），遗留链「未 strip 存储 + 校验才 strip」的双口径场景不再
  适用（append 撞键失败一侧在 test_lake_store 保留）；
- upsert 全量取代基座行时，仅基座携带的列不再从湖中消失：物理表列并集
  持久（「湖中列=历史并集」口径），旧列保留为空串列，见下方单列断言；
- 仅 content 列的行：content 从不入湖（与快照语义一致），纯 content 输出
  在物理表无列可建，由存储层明确报错而不是写空快照。
"""
from __future__ import annotations

import copy
import json
import uuid

import pytest
import sqlalchemy as sa

from app.data_channel.datasets.lake_gate import (
    LakeGateError,
    validate_merged_lake,
    validate_upsert_base,
)
from app.data_channel.datasets.lake_store import (
    count_rows,
    lake_columns_from_rows,
    stream_rows,
    upsert_run,
)
from app.data_channel.datasets.service import (
    DatasetService,
    _parse_stored_rows,
    rows_to_parquet_bytes,
    snapshot_cell_text,
)
from app.data_channel.pipeline_tasks.merge import compute_lake_impact, merge_rows
from app.models.v2.dataset import Dataset, DatasetVersion
from app.tasks.v2.pipeline_run import (
    _lake_impact_from_changeset,
    _save_curated_dataset,
)

DATASET_NAME = "等价数据集"


@pytest.fixture(autouse=True)
def _drop_lake_tables(db):
    """物理湖表不在 Base.metadata 中（运行时 DDL），逐测试清理避免串库。"""
    yield
    conn = db.connection()
    for name in sa.inspect(conn).get_table_names():
        if name.startswith("lake_ds_"):
            conn.execute(sa.text(f'DROP TABLE IF EXISTS "{name}"'))
    db.commit()


def _scrub(value):
    """递归掩码 __deleted_at__ 时间戳，其余逐字节比较。"""
    if isinstance(value, dict):
        return {k: ("<ts>" if k == "__deleted_at__" else _scrub(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _legacy_chain(base_rows: list[dict], new_rows: list[dict], mode: str,
                  pk_cols: list[str], soft: str | None):
    """复刻 _save_curated_dataset_in_lock 的合并段（参考实现路径）。"""
    new_rows = copy.deepcopy(new_rows)
    opts: dict = {"mode": mode, "primary_key": ",".join(pk_cols)}
    if soft:
        opts["soft_delete_column"] = soft
    # 注意：merge_rows 的软删除就地打标会经由共享对象污染 base_rows（别名），
    # 使 compute_lake_impact 看不到存量行的打标变化（旧链因此从不把存量
    # 重评估记入审计）。这里先快照未污染基座再合并——物理表路径的变更集
    # 如实记录存量打标（updated），比对以语义正确的一侧为准。
    before = copy.deepcopy(base_rows)
    old = [] if mode == "overwrite" else base_rows
    if mode == "upsert":
        validate_upsert_base(old, pk_cols, dataset_name=DATASET_NAME)
    merged, meta = merge_rows(old, new_rows, opts)
    validate_merged_lake(merged, pk_cols, dataset_name=DATASET_NAME,
                         write_mode=meta["mode"])
    impact = compute_lake_impact(before, merged, pk_cols)
    blob = rows_to_parquet_bytes(merged)
    readback = _parse_stored_rows(blob, limit=None) if blob else []
    return {
        "meta": meta, "impact": impact,
        "readback": readback, "mutated": new_rows,
        "rowcount": len(merged),
    }


def _lake_chain(db, base_rows: list[dict] | None, new_rows: list[dict],
                mode: str, pk_cols: list[str], soft: str | None):
    """lake_store 物理表路径，契约列预算模拟 pipeline_run 的 persist 段。"""
    new_rows = copy.deepcopy(new_rows)
    schema: dict = {"columns": lake_columns_from_rows(base_rows or [])}
    if pk_cols:
        schema["primary_key"] = ",".join(pk_cols)
    ds = Dataset(
        id=str(uuid.uuid4()), name=f"等价-{uuid.uuid4().hex[:8]}",
        kind="curated", schema_json=schema)
    db.add(ds)
    db.commit()
    if base_rows:
        upsert_run(db, ds, copy.deepcopy(base_rows), "overwrite", pk_cols)

    # 模拟 pipeline 的契约列预算：overwrite=本批输出列；增量=历史并集
    batch_cols = lake_columns_from_rows(new_rows)
    if mode == "overwrite":
        target_cols = batch_cols
    else:
        existing = list(ds.schema_json.get("columns") or [])
        target_cols = existing + [c for c in batch_cols if c not in existing]
    ds = db.query(Dataset).filter(Dataset.id == ds.id).first()
    ds.schema_json = {**ds.schema_json, "columns": target_cols}
    db.commit()

    rows_before = count_rows(db, ds)
    version, changeset = upsert_run(
        db, ds, new_rows, mode, pk_cols,
        soft_delete_column=soft or "")
    readback = [row for batch in stream_rows(db, ds, batch_size=997)
                for row in batch]
    return {
        "meta": {"mode": mode,
                 "rows_before": 0 if mode == "overwrite" else rows_before,
                 "rows_new": len(new_rows),
                 "rows_after": int(version.rowcount or 0)},
        "impact": _lake_impact_from_changeset(
            db, changeset, pk_cols, rows_before, int(version.rowcount or 0)),
        "readback": readback, "mutated": new_rows,
        "rowcount": int(version.rowcount or 0),
        "dataset_id": ds.id,
    }


def _rows_canonical(rows: list[dict], pk_cols: list[str]):
    """行集规范化：有主键按 (键, 行JSON) 排序，无主键按行 JSON 多重集排序。"""
    scrubbed = [_scrub(r) for r in rows]
    if pk_cols:
        return sorted(
            (tuple(r.get(c, "") for c in pk_cols),
             json.dumps(r, sort_keys=True, ensure_ascii=False))
            for r in scrubbed)
    return sorted(json.dumps(r, sort_keys=True, ensure_ascii=False)
                  for r in scrubbed)


def _impact_canonical(impact: dict) -> dict:
    def _canon_row(row: dict) -> dict:
        # legacy 样本来自内存合并行（原生类型、键集合稀疏），湖表样本是全契约
        # 列的规范化文本：单元格按 snapshot_cell_text 文本化（已是文本则原样），
        # 并摘除空串单元格（「键缺席」与「空串」在快照存储口径下同义）
        return {k: snapshot_cell_text(v) for k, v in row.items()
                if snapshot_cell_text(v) != ""}

    def _textify(item):
        if isinstance(item, dict) and set(item.keys()) == {"before", "after"}:
            return {"before": _canon_row(item["before"] or {}),
                    "after": _canon_row(item["after"] or {})}
        if isinstance(item, dict):
            return _canon_row(item)
        return item

    out = {k: v for k, v in impact.items() if not k.endswith("_sample")}
    for key in ("added_sample", "updated_sample", "deleted_sample"):
        out[key] = sorted(
            json.dumps(_scrub(_textify(item)), sort_keys=True,
                       ensure_ascii=False, default=str)
            for item in impact[key])
    return out


def assert_equivalent(db, base_rows, new_rows, mode, pk_cols, soft=None):
    """同一场景双跑：报错比类型，否则比全部产物。"""
    legacy_exc = lake_exc = None
    legacy = lake = None
    try:
        legacy = _legacy_chain(copy.deepcopy(base_rows) or [], new_rows,
                               mode, pk_cols, soft)
    except Exception as exc:  # noqa: BLE001 — 等价性比较需要捕获任意类型
        legacy_exc = exc
    try:
        lake = _lake_chain(db, copy.deepcopy(base_rows), new_rows,
                           mode, pk_cols, soft)
    except Exception as exc:  # noqa: BLE001
        lake_exc = exc

    if legacy_exc is not None or lake_exc is not None:
        assert type(legacy_exc) is type(lake_exc), (
            f"错误类型不一致：legacy={type(legacy_exc).__name__} "
            f"lake={type(lake_exc).__name__}: {lake_exc}")
        return

    assert lake["meta"] == legacy["meta"], "merge_meta 不一致"
    assert lake["rowcount"] == legacy["rowcount"], "rowcount 不一致"
    assert _rows_canonical(lake["readback"], pk_cols) == _rows_canonical(
        legacy["readback"], pk_cols), "湖内容读回行集不一致"
    lake_impact = _impact_canonical(lake["impact"])
    legacy_impact = _impact_canonical(legacy["impact"])
    if not pk_cols and mode in ("append", "upsert"):
        # 无主键追加的有意口径差：legacy 的影响审计按整行签名归并（与基座
        # 全同的来数行计 unchanged），物理表按真实追加逐行记账（计 added，
        # 回放精确到份数）。added/unchanged 计数与 added 样本豁免比对。
        for key in ("added_count", "unchanged_count", "added_sample"):
            lake_impact.pop(key, None)
            legacy_impact.pop(key, None)
    if lake_impact.get("sample_truncated") and legacy_impact.get(
            "sample_truncated"):
        # 截断样本的成员由排序口径决定（legacy=合并序，湖表=row_pk 序），
        # 均为合法前 50：计数与截断标志已比对，样本成员豁免
        for key in ("added_sample", "updated_sample", "deleted_sample"):
            lake_impact.pop(key, None)
            legacy_impact.pop(key, None)
    assert lake_impact == legacy_impact, "lake_impact 不一致"
    assert _scrub(lake["mutated"]) == _scrub(legacy["mutated"]), \
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
    pytest.param("overwrite", [], None, id="overwrite-nopk"),
    pytest.param("overwrite", ["id"], None, id="overwrite-singlepk"),
    pytest.param("overwrite", ["id", "c"], None, id="overwrite-compositepk"),
    pytest.param("append", [], None, id="append-nopk"),
    pytest.param("append", ["id"], None, id="append-singlepk"),
    pytest.param("append", ["id", "c"], None, id="append-compositepk"),
    pytest.param("append_dedup", [], None, id="dedup-nopk"),
    pytest.param("append_dedup", ["id"], None, id="dedup-singlepk"),
    pytest.param("append_dedup", ["id", "c"], None, id="dedup-compositepk"),
    pytest.param("upsert", [], None, id="upsert-nopk"),
    pytest.param("upsert", ["id"], None, id="upsert-singlepk"),
    pytest.param("upsert", ["id", "c"], None, id="upsert-compositepk"),
    # ── upsert × 软删除 ─────────────────────────
    pytest.param("upsert", ["id"], "b", id="upsert-soft-boolcol"),
    pytest.param("upsert", [], "b", id="upsert-nopk-soft"),
]


@pytest.mark.parametrize("mode,pk_cols,soft", _VALID_CASES)
def test_equivalence_mixed_dataset(db, mode, pk_cols, soft):
    """混合类型数据集（含 unchanged/updated/added 各形态）双跑等价。"""
    base = _MIXED_BASE
    new = _MIXED_NEW
    if soft == "b":
        # 软删除列取值覆盖 truthy 全集与 falsy/空值
        new = [dict(r, b=v) for r, v in zip(
            _MIXED_NEW, ["False", "是", None])]
        base = [dict(r, b=v) for r, v in zip(
            base, ["0", "yes", ""])][:len(base)]
    assert_equivalent(db, base, new, mode, pk_cols, soft)


# ── 空增量 / 空基座 ─────────────────────────────────────────
@pytest.mark.parametrize("mode", ["overwrite", "append", "append_dedup", "upsert"])
def test_empty_increment(db, mode):
    assert_equivalent(db, _MIXED_BASE, [], mode, ["id"])


@pytest.mark.parametrize("mode", ["overwrite", "append", "append_dedup", "upsert"])
def test_empty_base(db, mode):
    assert_equivalent(db, None, _MIXED_NEW, mode, ["id"])
    assert_equivalent(db, [], _MIXED_NEW, mode, ["id"])


def test_both_empty(db):
    assert_equivalent(db, None, [], "append", ["id"])
    assert_equivalent(db, [], [], "upsert", ["id"], soft="b")


# ── 结构/语义边角 ───────────────────────────────────────────
def test_upsert_superseded_base_column_persists_as_empty(db):
    """基座行全被取代时，仅基座携带的列在物理表中保留为空串列（并集持久），
    与 legacy「列随合并行消失」的差异在此单列钉住。"""
    base = [{"id": "1", "gone": "g"}, {"id": "2", "gone": "g2"}]
    new = [{"id": "1", "v": 1}, {"id": "2", "v": 2}]
    lake = _lake_chain(db, base, new, "upsert", ["id"], None)
    by_pk = {r["id"]: r for r in lake["readback"]}
    assert by_pk["1"] == {"id": "1", "gone": "", "v": "1"}
    assert by_pk["2"] == {"id": "2", "gone": "", "v": "2"}
    assert lake["impact"]["updated_count"] == 2


def test_upsert_soft_delete_resurrect_and_previous_markers(db):
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
    assert_equivalent(db, base, new, "upsert", ["id"], soft="flag")


def test_append_dedup_heterogeneous_keys_and_cross_side_duplicates(db):
    """整行去重：跨基座/增量重复保留基座首次出现。"""
    base = [{"a": "1"}, {"a": "1", "b": ""}, {"a": "2", "b": "x"}]
    new = [{"a": "1"}, {"a": "3", "b": None}, {"a": "1", "b": ""}, {"b": "only"}]
    assert_equivalent(db, base, new, "append_dedup", [])


def test_upsert_native_int_pk_matches_stored_string(db):
    """增量原生 int 主键与湖中字符串主键按 str() 文本匹配。"""
    base = [{"id": "86000", "v": "old"}]
    new = [{"id": 86000, "v": "new"}]
    assert_equivalent(db, base, new, "upsert", ["id"])


def test_increment_new_column_and_content_column(db):
    """content 列不进湖/签名/列清单；新增列按首现序追加（并集演化）。"""
    base = [{"id": "1", "v": "a"}]
    new = [
        {"id": "2", "extra": "新列", "content": b"binary"},
        {"id": "1", "v": "a"},
    ]
    assert_equivalent(db, base, new, "append_dedup", [])
    assert_equivalent(db, base, new, "append", [])
    assert_equivalent(db, base, new, "upsert", ["id"])


def test_soft_delete_truthy_spectrum(db):
    """truthy 词表逐项 + 大小写/空白/None/布尔/数字变体。"""
    base = [{"id": str(i), "flag": v} for i, v in enumerate([
        "1", "true", "TRUE", "yes", "y", "t", "是", "删除", "已删除",
        "0", "false", "no", "", "  ", " True ", "2", "真", "none"])]
    new = [{"id": "100", "flag": True}, {"id": "101", "flag": 1},
           {"id": "102", "flag": None}, {"id": "103", "flag": False}]
    assert_equivalent(db, base, new, "upsert", ["id"], soft="flag")


def test_impact_sample_truncation_over_50(db):
    """added 超 50：计数完整、样本截断、sample_truncated 置位。"""
    base = [{"id": f"b{i}", "v": "x"} for i in range(3)]
    new = [{"id": f"n{i}", "v": i} for i in range(60)]
    assert_equivalent(db, base, new, "upsert", ["id"])
    assert_equivalent(db, base, new, "append", [])
    assert_equivalent(db, base, new, "overwrite", ["id"])


def test_updated_sample_slim_truncation(db):
    """updated 样本的 before/after 超长值截断一致。"""
    base = [{"id": "1", "v": "旧" * 250}]
    new = [{"id": "1", "v": "新" * 250}]
    assert_equivalent(db, base, new, "upsert", ["id"])


def test_nopk_upsert_soft_delete_aliasing_semantics(db):
    """无主键 upsert：追加 + 软删除打标（湖中存量同步重评估）。"""
    base = [{"v": "1", "flag": "0"}, {"v": "2", "flag": "是"}]
    new = [{"v": "3", "flag": "true"}, {"v": "4", "flag": "no"}]
    assert_equivalent(db, base, new, "upsert", [], soft="flag")


def test_increment_rows_carrying_marker_keys(db):
    """增量自带 __deleted__ 键：软删除生效时按判定重写或摘除。"""
    base = [{"id": "1", "v": "a", "flag": "0"}]
    new = [
        {"id": "2", "v": "b", "flag": "1", "__deleted__": "False",
         "__deleted_at__": "2020-01-01"},
        {"id": "3", "v": "c", "flag": "0", "__deleted__": "True"},
    ]
    assert_equivalent(db, base, new, "upsert", ["id"], soft="flag")
    # 软删除未生效时（非 upsert），标记键是普通业务列
    assert_equivalent(db, base, new, "append", [])


def test_long_snapshot_scale_smoke(db):
    """较大基座的读回行集一致（3 万行基座 + 稀疏更新）。"""
    base = [{"id": f"k{i:06d}", "v": f"值{i % 7}", "n": str(i)} for i in range(30_000)]
    new = [{"id": f"k{i:06d}", "v": "新", "n": "0"} for i in range(0, 30_000, 997)]
    assert_equivalent(db, base, new, "upsert", ["id"])
    assert_equivalent(db, base, new, "append_dedup", [])


# ── 主键违例：两边同抛 LakeGateError ─────────────────────────
def test_append_merged_duplicate_pk_blocked(db):
    base = [{"id": "1", "v": "old"}]
    new = [{"id": "1", "v": "new"}]
    assert_equivalent(db, base, new, "append", ["id"])
    assert_equivalent(db, base, new, "append_dedup", ["id"])  # 整行不同 → 仍重复


def test_append_duplicate_pk_within_batch_blocked(db):
    base = [{"id": "1", "v": "old"}]
    new = [{"id": "2", "v": "a"}, {"id": "2", "v": "b"}]
    assert_equivalent(db, base, new, "append", ["id"])


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


def test_save_curated_dataset_upsert_roundtrip_via_lake_store(db, monkeypatch):
    """接线冒烟：两次 upsert 落物理湖表，行集/统计/契约/变更集与版本元数据。"""
    from types import SimpleNamespace

    from app.data_channel.datasets import service as dataset_service_module
    from app.data_channel.datasets import lake_store
    from app.data_channel.datasets.models import DatasetChangeset

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

    ds = db.query(Dataset).filter(Dataset.id == first["curated_dataset_id"]).one()
    rows = lake_store.page_rows(db, ds, 0, 100)
    # A-2 被增量取代：内容更新且摘除软删除标记（复活）；标记列一旦进入物理表
    # 即作为普通数据列保留（空串 = 未删除），与快照「键缺席」口径的差异在此钉住
    assert rows == [
        {"order_id": "A-1", "amount": "10", "flag": "0",
         "__deleted__": "", "__deleted_at__": ""},
        {"order_id": "A-2", "amount": "21", "flag": "0",
         "__deleted__": "", "__deleted_at__": ""},
        {"order_id": "A-3", "amount": "30", "flag": "0",
         "__deleted__": "", "__deleted_at__": ""},
    ]
    assert ds.schema_json["primary_key"] == "order_id"
    assert ds.schema_json["columns"] == [
        "order_id", "amount", "flag", "__deleted__", "__deleted_at__"]

    # 版本元数据：无 blob 载荷，rowcount/checksum 与变更集锚定
    versions = db.query(DatasetVersion).filter(
        DatasetVersion.dataset_id == ds.id).order_by(DatasetVersion.version_no).all()
    assert len(versions) == 2
    assert all(v.data_blob is None and v.data_size is None for v in versions)
    assert [v.rowcount for v in versions] == [2, 3]
    changesets = db.query(DatasetChangeset).filter(
        DatasetChangeset.dataset_id == ds.id).order_by(DatasetChangeset.created_at).all()
    assert [c.change_type for c in changesets] == ["run", "run"]
    assert (changesets[1].added_count, changesets[1].updated_count,
            changesets[1].deleted_count) == (1, 1, 0)
    assert changesets[1].checksum == versions[1].checksum
