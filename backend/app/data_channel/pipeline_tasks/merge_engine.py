"""
资产湖合并引擎（DuckDB 下推实现）。

【已退役】运行时入湖路径已切换 lake_store 物理湖表（行级 upsert + 变更集），
本模块不再被任何运行时代码引用；文件按兼容纪律保留，仅供等价测试对照
与历史回溯，不得新增调用方。

参考实现（语义权威）保留在 merge.py：merge_rows / compute_lake_impact /
_row_signature 等函数定义了合并、去重、软删除、审计 diff 与序列化的外部行为。
本模块把「读湖中全量 → 合并 → 主键校验 → 审计 diff → parquet 序列化」整段
下推到 DuckDB：parquet 基座零 Python 行物化，单次运行成本与内存只随增量批次
而非湖中总量增长。

外部行为曾与参考实现逐字节等价（合并读回行集、merge_meta、lake_impact、
LakeGateError 文案、columns_typed）。等价性测试已随存储切换改写为
「lake_store 物理表 upsert vs merge_rows 参考实现」矩阵，仍位于
tests/v2/pipeline/test_merge_engine_equivalence.py。

已知边界（等价测试不覆盖，参考实现在这些路径上行为本就退化）：
- 手工构造的非平台产出 parquet 基座（含非字符串物理列 / NULL 单元格）：
  平台写出的湖中快照全列 VARCHAR 且无 NULL，CAST/coalesce 对其是恒等变换。
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime

from app.data_channel.datasets.lake_gate import LakeGateError, infer_columns_typed
from app.data_channel.datasets.service import (
    DatasetReadError,
    _parse_stored_rows,
    snapshot_cell_text,
)
from app.data_channel.pipeline_tasks.merge import _slim_row, normalize_write_mode

_CONTENT_COL = "content"
_MARKER_DELETED = "__deleted__"
_MARKER_AT = "__deleted_at__"
_MARKERS = (_MARKER_DELETED, _MARKER_AT)
# 与 merge._apply_soft_delete 完全一致的 truthy 集合
_TRUTHY = frozenset({"1", "true", "yes", "y", "t", "是", "删除", "已删除"})
_SAMPLE_LIMIT = 50

# Python str.strip() 的空白字符全集（含 Unicode 空白），用于在 SQL 中复刻
# validate_pk / _apply_soft_delete 的 strip 语义
_PY_WS = (
    " \\t\\n\\r\\v\\f\\x{1c}\\x{1d}\\x{1e}\\x{1f}\\x{85}\\x{a0}\\x{1680}"
    "\\x{2000}-\\x{200a}\\x{2028}\\x{2029}\\x{202f}\\x{205f}\\x{3000}"
)


def _py_strip_sql(expr: str) -> str:
    """SQL 版 Python str.strip()：两端去除 Python 空白（含 Unicode 空白）。"""
    return f"regexp_replace({expr}, '^[{_PY_WS}]+|[{_PY_WS}]+$', '', 'g')"


def _q(ident: str) -> str:
    """SQL 标识符引用（列名可为中文/空格/引号等任意字符）。"""
    return '"' + str(ident).replace('"', '""') + '"'


def _lit(value: str) -> str:
    """SQL 字符串字面量。"""
    return "'" + str(value).replace("'", "''") + "'"


def _keyset_code(keys) -> str:
    """行键集合的确定性编码：排序后长度前缀拼接。

    只作引擎内部的等价关系（同键集合 ⇔ 同编码），不对外输出；
    与 merge._row_signature 的「键集合参与签名」语义对齐。
    """
    return "|".join(f"{len(k)}:{k}" for k in sorted(keys))


def _hidden_names(taken: set[str]) -> dict[str, str]:
    """内部列名：保证不与业务列冲突（业务列可能恰好叫 __merge_seq 等）。"""
    pool = set(taken)
    names: dict[str, str] = {}
    for logical in ("seq", "rn", "keyset", "keyset_del", "keyset_plain", "isdel"):
        candidate = f"__merge_{logical}"
        index = 0
        while candidate in pool:
            index += 1
            candidate = f"__merge_{logical}_{index}"
        pool.add(candidate)
        names[logical] = candidate
    return names


def _pk_hidden_names(taken: set[str], count: int) -> list[str]:
    pool = set(taken)
    out: list[str] = []
    for i in range(count):
        candidate = f"__merge_pk_{i}"
        index = 0
        while candidate in pool:
            index += 1
            candidate = f"__merge_pk_{i}_{index}"
        pool.add(candidate)
        out.append(candidate)
    return out


@dataclass
class MergeOutcome:
    """merge_lake_increment 的产物：与 legacy 合并段逐项对应。"""
    parquet_bytes: bytes          # 新版本快照（空表/空列 → b""，同 rows_to_parquet_bytes）
    rowcount: int                 # 合并后行数（= legacy len(lake_rows)）
    merge_meta: dict              # = legacy merge_rows 返回的统计 dict
    lake_impact: dict             # = legacy compute_lake_impact 返回的审计 diff
    columns_typed: list[dict]     # = legacy infer_columns_typed(lake_rows)
    merged_rows: list[dict] | None = None  # 仅 need_merged_rows 时物化（质量分边缘场景）


def merge_lake_increment(
    *,
    base_bytes: bytes | None,
    new_rows: list[dict],
    write_opts: dict,
    pk_cols: list[str],
    dataset_name: str = "",
    dataset_id: str = "",
    base_version_no: int | None = None,
    need_merged_rows: bool = False,
) -> MergeOutcome:
    """把本批增量按 write_mode 合并进湖中存量，产出新版本快照与审计信息。

    base_bytes：湖中最新版本的原始字节（parquet 直读；历史 csv/json/excel 走
    _parse_stored_rows 解析——与 load_latest_rows 同一解析路径）。None/空字节
    表示空基座。任何基座读取/解析失败抛 DatasetReadError（不得当空基座）。
    new_rows：已过 gate_rows 规范化的本批增量；upsert 软删除会按参考实现
    就地打标（__deleted__/__deleted_at__），与 legacy 对 data 的就地修改一致。
    """
    import duckdb

    opts = write_opts or {}
    mode = normalize_write_mode(opts.get("mode"))
    pk_cols = list(pk_cols or [])
    # 软删除只在 upsert 分支生效（与 merge_rows 一致）
    soft_col = str(opts.get("soft_delete_column") or "") if mode == "upsert" else ""
    # 同一次运行内所有软删除标记共用一个时间戳
    run_ts = datetime.utcnow().isoformat()

    eng = _Engine(
        mode=mode,
        pk_cols=pk_cols,
        soft_col=soft_col,
        run_ts=run_ts,
        dataset_name=dataset_name,
        dataset_id=dataset_id,
        base_version_no=base_version_no,
        new_rows=new_rows,
    )

    with tempfile.TemporaryDirectory(prefix="ontologybuild-merge-") as tmpdir:
        eng.tmpdir = tmpdir
        eng.load_base(base_bytes)
        eng.prepare_increment()
        con = duckdb.connect(":memory:")
        try:
            con.execute(f"SET temp_directory={_lit(tmpdir)}")
            eng.con = con
            eng.create_tables()
            eng.build_merged()
            eng.apply_base_validation()
            eng.shape_merged()
            eng.finalize_merged()
            eng.apply_merged_validation()
            eng.mutate_kept_base_rows()
            lake_impact = eng.compute_impact()
            columns = eng.compute_columns()
            columns_typed = eng.compute_columns_typed(columns)
            parquet_bytes = eng.write_snapshot(columns)
            merged_rows = eng.materialize_merged() if need_merged_rows else None
        finally:
            con.close()

    merge_meta = {
        "mode": mode,
        # legacy：overwrite 的 old_rows 固定为 []，rows_before 恒为 0
        "rows_before": 0 if mode == "overwrite" else eng.base_count,
        "rows_new": len(new_rows),
        "rows_after": eng.merged_count,
    }
    return MergeOutcome(
        parquet_bytes=parquet_bytes,
        rowcount=eng.merged_count,
        merge_meta=merge_meta,
        lake_impact=lake_impact,
        columns_typed=columns_typed,
        merged_rows=merged_rows,
    )


class _Engine:
    """单次合并的 DuckDB 会话与全部中间态。"""

    def __init__(self, *, mode, pk_cols, soft_col, run_ts,
                 dataset_name, dataset_id, base_version_no, new_rows):
        self.mode = mode
        self.pk_cols = pk_cols
        self.soft_col = soft_col
        self.soft_active = bool(soft_col)
        self.run_ts = run_ts
        self.dataset_name = dataset_name
        self.dataset_id = dataset_id
        self.base_version_no = base_version_no
        self.new_rows = new_rows

        self.tmpdir = ""
        self.con = None

        # 基座两种形态：parquet 临时文件（零 Python 行物化）或解析后的行（历史格式，有界）
        self.base_path: str | None = None
        self.base_rows: list[dict] | None = None
        self.base_schema: list[str] = []       # parquet 基座物理列序
        self.base_count = 0                    # M：基座行数
        self.base_all_cols: set[str] = set()   # 含 content（校验文案用）
        self.base_norm: list[dict] = []        # 解析型基座规范化行
        self.base_value_cols: list[str] = []   # 基座业务列（parquet = 物理列序）

        # 增量规范化产物
        self.inc_norm: list[dict] = []
        self.inc_value_cols: list[str] = []    # 首现序
        self.inc_all_keys: set[str] = set()    # 含 content（校验文案用）
        self.inc_is_del: list[bool] = []

        self.h: dict[str, str] = {}            # 隐藏列名（避开业务列名）
        self.pk_hid: list[str] = []

        self.union_value_cols: list[str] = []  # 两侧业务列并集
        self.sig_cols: list[str] = []          # 参与签名/分组的列（不含 content）
        self.merged_count = 0

    # ── 输入装载 ────────────────────────────────────────────────
    def load_base(self, base_bytes: bytes | None) -> None:
        """基座装载：parquet 直写临时文件；历史格式走 _parse_stored_rows。

        任何解析失败抛 DatasetReadError，与 load_all_rows 的包装文案一致。
        """
        if not base_bytes:
            return
        if base_bytes[:4] == b"PAR1":
            self.base_path = os.path.join(self.tmpdir, "base.parquet")
            with open(self.base_path, "wb") as fh:
                fh.write(base_bytes)
            return
        try:
            self.base_rows = _parse_stored_rows(base_bytes, limit=None)
        except Exception as exc:
            raise DatasetReadError(
                f"数据集 {self.dataset_id} v{self.base_version_no} 内容解析失败：{exc}"
            ) from exc
        self.base_count = len(self.base_rows)

    def _row_is_del(self, row: dict) -> bool:
        """软删除 truthy 判定：复刻 merge._apply_soft_delete 的逐字语义。"""
        v = row.get(self.soft_col)
        return str(v).strip().lower() in _TRUTHY if v is not None else False

    def _pk_texts(self, row: dict) -> list[str]:
        """主键去重键文本：复刻 merge._dedup_by_key 的 str(r.get(k, ""))。

        None 归一为 ""——主键为空的行在任何模式下都会先被主键校验硬失败
        拦截（validate_pk 的 `v is None` 判定），不会进入去重/比对。
        """
        out = []
        for c in self.pk_cols:
            v = row.get(c, "")
            out.append("" if v is None else str(v))
        return out

    def prepare_increment(self) -> None:
        """增量规范化：逐 cell snapshot_cell_text，与存量字符串语义同构。

        同时预计算软删除判定并按参考实现就地修改 new_rows（legacy 中
        merge_rows 会原地打标，调用方的 output_sample / last_output_columns
        依赖这一副作用）。
        """
        seen: set[str] = set()
        for row in self.new_rows:
            self.inc_all_keys.update(str(k) for k in row.keys())
            norm = {
                str(k): snapshot_cell_text(v)
                for k, v in row.items()
                if str(k) != _CONTENT_COL
            }
            is_del = self.soft_active and self._row_is_del(row)
            self.inc_is_del.append(is_del)
            if self.soft_active:
                # 与 merge._apply_soft_delete 逐字一致的就地打标/摘除
                if is_del:
                    row[_MARKER_DELETED] = True
                    row[_MARKER_AT] = self.run_ts
                else:
                    row.pop(_MARKER_DELETED, None)
                    row.pop(_MARKER_AT, None)
            self.inc_norm.append(norm)
            for key in norm:
                if key not in seen:
                    seen.add(key)
                    self.inc_value_cols.append(key)

    # ── DuckDB 表装载 ───────────────────────────────────────────
    def _python_side_extras(self, rows: list[dict], norms: list[dict],
                            seq_start: int, is_dels: list[bool]) -> dict:
        """Python 侧切片（增量/解析型基座）的隐藏列值。"""
        keysets: list[str] = []
        keysets_del: list[str] = []
        for norm in norms:
            if self.soft_active:
                keys = set(norm.keys())
                keysets.append(_keyset_code(keys - set(_MARKERS)))
                keysets_del.append(_keyset_code(keys | set(_MARKERS)))
            else:
                keysets.append(_keyset_code(norm.keys()))
        pk_rows = [self._pk_texts(r) for r in rows]
        extras = {
            "seq": list(range(seq_start, seq_start + len(rows))),
            "pk": [[t[i] for t in pk_rows] for i in range(len(self.pk_cols))],
            "keyset": keysets,
            # before 关系用的原始键集合（未打标；非软删除时与 keyset 相同）
            "keyset_plain": [_keyset_code(norm.keys()) for norm in norms],
        }
        if self.soft_active:
            extras["keyset_del"] = keysets_del
            extras["isdel"] = is_dels
        return extras

    def _write_rows_parquet(self, path: str, rows: list[dict],
                            value_cols: list[str], extras: dict) -> None:
        """Python 侧行 → 临时 parquet：全列 VARCHAR（缺键 → NULL）+ 隐藏列。"""
        import pyarrow as pa
        import pyarrow.parquet as pq

        data = {
            c: pa.array([r.get(c) for r in rows], type=pa.string())
            for c in value_cols
        }
        data[self.h["seq"]] = pa.array(extras["seq"], type=pa.int64())
        for i, hid in enumerate(self.pk_hid):
            data[hid] = pa.array(extras["pk"][i], type=pa.string())
        data[self.h["keyset"]] = pa.array(extras["keyset"], type=pa.string())
        if "keyset_plain" in extras:
            data[self.h["keyset_plain"]] = pa.array(
                extras["keyset_plain"], type=pa.string())
        if self.soft_active:
            data[self.h["keyset_del"]] = pa.array(
                extras["keyset_del"], type=pa.string())
            data[self.h["isdel"]] = pa.array(extras["isdel"], type=pa.bool_())
        pq.write_table(pa.table(data), path, compression="zstd")

    def _normalize_parsed_base(self) -> None:
        """解析型基座：与增量同构的规范化（行已在 Python，量有界）。"""
        seen: set[str] = set()
        for row in self.base_rows or []:
            self.base_all_cols.update(str(k) for k in row.keys())
            norm = {
                str(k): snapshot_cell_text(v)
                for k, v in row.items()
                if str(k) != _CONTENT_COL
            }
            self.base_norm.append(norm)
            for key in norm:
                if key not in seen:
                    seen.add(key)
                    self.base_value_cols.append(key)

    def _is_del_sql(self, value_expr: str) -> str:
        """SQL 版软删除 truthy 判定（parquet 基座侧用）。"""
        vals = ", ".join(_lit(v) for v in sorted(_TRUTHY))
        return f"(lower({_py_strip_sql(value_expr)}) IN ({vals}))"

    def create_tables(self) -> None:
        """在 DuckDB 中物化 base / inc 两张切片表（base 恒存在，可能零行）。"""
        con = self.con
        self._normalize_parsed_base()

        # parquet 基座：先探物理列（DESCRIBE 保持文件列序），失败按 DatasetReadError 上抛
        if self.base_path is not None:
            try:
                self.base_schema = [
                    r[0] for r in con.execute(
                        f"DESCRIBE SELECT * FROM read_parquet({_lit(self.base_path)})"
                    ).fetchall()
                ]
                self.base_count = con.execute(
                    f"SELECT count(*) FROM read_parquet({_lit(self.base_path)})"
                ).fetchone()[0]
            except Exception as exc:
                raise DatasetReadError(
                    f"数据集 {self.dataset_id} v{self.base_version_no} 内容解析失败：{exc}"
                ) from exc
            self.base_all_cols = set(self.base_schema)
            self.base_value_cols = list(self.base_schema)

        # 隐藏列名须在收集完所有业务列之后确定，避免撞名
        taken = (
            set(self.base_all_cols) | set(self.inc_all_keys)
            | set(self.base_value_cols) | set(self.inc_value_cols) | set(_MARKERS)
        )
        self.h = _hidden_names(taken)
        self.pk_hid = _pk_hidden_names(taken | set(self.h.values()), len(self.pk_cols))
        seq_q = _q(self.h["seq"])

        if self.base_path is not None:
            schema_nocontent = [c for c in self.base_schema if c != _CONTENT_COL]
            selects = [f"row_number() OVER () - 1 AS {seq_q}"]
            for c in self.base_value_cols:
                selects.append(f"coalesce(CAST({_q(c)} AS VARCHAR), '') AS {_q(c)}")
            for i, c in enumerate(self.pk_cols):
                if c in self.base_all_cols:
                    expr = f"coalesce(CAST({_q(c)} AS VARCHAR), '')"
                else:
                    expr = "''"
                selects.append(f"{expr} AS {_q(self.pk_hid[i])}")
            # before 关系用未打标的原始键集合；after 用打标后变体
            selects.append(
                f"{_lit(_keyset_code(schema_nocontent))} AS {_q(self.h['keyset_plain'])}")
            if self.soft_active:
                selects.append(
                    f"{_lit(_keyset_code(set(schema_nocontent) - set(_MARKERS)))} "
                    f"AS {_q(self.h['keyset'])}")
                selects.append(
                    f"{_lit(_keyset_code(set(schema_nocontent) | set(_MARKERS)))} "
                    f"AS {_q(self.h['keyset_del'])}")
                if self.soft_col in self.base_all_cols:
                    soft_expr = f"coalesce(CAST({_q(self.soft_col)} AS VARCHAR), '')"
                else:
                    soft_expr = "''"
                selects.append(f"{self._is_del_sql(soft_expr)} AS {_q(self.h['isdel'])}")
            else:
                selects.append(
                    f"{_lit(_keyset_code(schema_nocontent))} AS {_q(self.h['keyset'])}")
            try:
                con.execute(
                    f"CREATE TABLE base AS SELECT {', '.join(selects)} "
                    f"FROM read_parquet({_lit(self.base_path)})")
            except Exception as exc:
                raise DatasetReadError(
                    f"数据集 {self.dataset_id} v{self.base_version_no} 内容解析失败：{exc}"
                ) from exc
        elif self.base_count:
            path = os.path.join(self.tmpdir, "base_parsed.parquet")
            is_dels = ([self._row_is_del(r) for r in self.base_rows]
                       if self.soft_active else [])
            extras = self._python_side_extras(
                self.base_rows, self.base_norm, 0, is_dels)
            self._write_rows_parquet(path, self.base_norm, self.base_value_cols, extras)
            con.execute(f"CREATE TABLE base AS SELECT * FROM read_parquet({_lit(path)})")
        else:
            # 空基座：零行空表，保证后续 SQL 形态统一
            selects = [f"CAST(NULL AS BIGINT) AS {seq_q}"]
            selects += [f"'' AS {_q(hid)}" for hid in self.pk_hid]
            selects.append(f"'' AS {_q(self.h['keyset_plain'])}")
            selects.append(f"'' AS {_q(self.h['keyset'])}")
            if self.soft_active:
                selects.append(f"'' AS {_q(self.h['keyset_del'])}")
                selects.append(f"FALSE AS {_q(self.h['isdel'])}")
            con.execute(
                f"CREATE TABLE base AS SELECT {', '.join(selects)} WHERE FALSE")

        n = len(self.new_rows)
        if n:
            path = os.path.join(self.tmpdir, "inc.parquet")
            extras = self._python_side_extras(
                self.new_rows, self.inc_norm, self.base_count, self.inc_is_del)
            self._write_rows_parquet(path, self.inc_norm, self.inc_value_cols, extras)
            con.execute(f"CREATE TABLE inc AS SELECT * FROM read_parquet({_lit(path)})")

        # 列并集：基座列序 + 增量新增列按首现追加
        base_set = set(self.base_value_cols)
        self.union_value_cols = self.base_value_cols + [
            c for c in self.inc_value_cols if c not in base_set]
        self.sig_cols = [c for c in self.union_value_cols if c != _CONTENT_COL]

    # ── 合并 ────────────────────────────────────────────────────
    def _source_select(self, table: str, side_cols: set[str]) -> str:
        """一侧切片投影为统一列序的业务列 + 隐藏列（缺列补空串）。"""
        cols = [f"{_q(self.h['seq'])} AS {_q(self.h['seq'])}"]
        for c in self.union_value_cols:
            if c in side_cols:
                cols.append(f"coalesce({_q(c)}, '') AS {_q(c)}")
            else:
                cols.append(f"'' AS {_q(c)}")
        for hid in self.pk_hid:
            cols.append(f"{_q(hid)} AS {_q(hid)}")
        cols.append(f"{_q(self.h['keyset'])} AS {_q(self.h['keyset'])}")
        if self.soft_active:
            cols.append(f"{_q(self.h['keyset_del'])} AS {_q(self.h['keyset_del'])}")
            cols.append(f"{_q(self.h['isdel'])} AS {_q(self.h['isdel'])}")
        return f"SELECT {', '.join(cols)} FROM {table}"

    def build_merged(self) -> None:
        """union 两侧为 merged_raw（业务列缺键补空串，键集合列消除歧义）。"""
        sources = []
        if self.base_count and self.mode != "overwrite":
            sources.append(self._source_select("base", set(self.base_value_cols)))
        if self.new_rows:
            sources.append(self._source_select("inc", set(self.inc_value_cols)))
        if sources:
            self.con.execute(
                f"CREATE TABLE merged_raw AS {' UNION ALL '.join(sources)}")
            return
        # 两侧皆空：造一张全列零行空表，后续流程形态统一
        cols = [f"CAST(NULL AS BIGINT) AS {_q(self.h['seq'])}"]
        cols += [f"'' AS {_q(c)}" for c in self.union_value_cols]
        cols += [f"'' AS {_q(hid)}" for hid in self.pk_hid]
        cols.append(f"'' AS {_q(self.h['keyset'])}")
        if self.soft_active:
            cols.append(f"'' AS {_q(self.h['keyset_del'])}")
            cols.append(f"FALSE AS {_q(self.h['isdel'])}")
        self.con.execute(
            f"CREATE TABLE merged_raw AS SELECT {', '.join(cols)} WHERE FALSE")

    def apply_base_validation(self) -> None:
        """upsert 合并前校验湖中存量主键契约（validate_upsert_base 等价）。"""
        if self.mode != "upsert" or not self.pk_cols or not self.base_count:
            return
        try:
            self._validate_pk_sql(
                "base", f"{_q(self.h['seq'])} + 1", self.base_all_cols, "湖中现有数据")
        except LakeGateError as exc:
            raise LakeGateError(
                f"{exc} —— 湖中存量数据不满足主键契约，无法安全执行主键合并（upsert）。"
                f"请先用 overwrite 方式重建该资产，让全量数据经过主键校验后再切回 upsert。"
            ) from None

    def shape_merged(self) -> None:
        """按入库方式去重：append_dedup 整行首现；upsert 主键末现。"""
        seq = _q(self.h["seq"])
        if self.mode == "append_dedup":
            group_cols = ", ".join(
                [_q(self.h["keyset"])] + [_q(c) for c in self.sig_cols])
            self.con.execute(
                f"CREATE TABLE merged_shaped AS SELECT * FROM merged_raw "
                f"QUALIFY row_number() OVER (PARTITION BY {group_cols} "
                f"ORDER BY {seq}) = 1")
        elif self.mode == "upsert" and self.pk_cols:
            pks = ", ".join(_q(h) for h in self.pk_hid)
            self.con.execute(
                f"CREATE TABLE merged_shaped AS SELECT * FROM merged_raw "
                f"QUALIFY row_number() OVER (PARTITION BY {pks} "
                f"ORDER BY {seq} DESC) = 1")
        else:
            self.con.execute("CREATE TABLE merged_shaped AS SELECT * FROM merged_raw")

    def finalize_merged(self) -> None:
        """终态投影：软删除标记列按判定重写；赋合并后行号 rn（1 起）。"""
        seq = _q(self.h["seq"])
        isdel = _q(self.h["isdel"])
        cols = [f"{seq} AS {seq}",
                f"row_number() OVER (ORDER BY {seq}) AS {_q(self.h['rn'])}"]
        value_cols = list(self.union_value_cols)
        if self.soft_active:
            for m in _MARKERS:
                if m not in value_cols:
                    value_cols.append(m)
        for c in value_cols:
            if self.soft_active and c == _MARKER_DELETED:
                cols.append(f"CASE WHEN {isdel} THEN 'True' ELSE '' END AS {_q(c)}")
            elif self.soft_active and c == _MARKER_AT:
                cols.append(
                    f"CASE WHEN {isdel} THEN {_lit(self.run_ts)} ELSE '' END AS {_q(c)}")
            else:
                cols.append(f"{_q(c)} AS {_q(c)}")
        for hid in self.pk_hid:
            cols.append(f"{_q(hid)} AS {_q(hid)}")
        if self.soft_active:
            cols.append(
                f"CASE WHEN {isdel} THEN {_q(self.h['keyset_del'])} "
                f"ELSE {_q(self.h['keyset'])} END AS {_q(self.h['keyset'])}")
            cols.append(f"{isdel} AS {isdel}")
        else:
            cols.append(f"{_q(self.h['keyset'])} AS {_q(self.h['keyset'])}")
            cols.append(f"FALSE AS {isdel}")
        self.con.execute(
            f"CREATE TABLE merged AS SELECT {', '.join(cols)} FROM merged_shaped")
        self.merged_count = self.con.execute(
            "SELECT count(*) FROM merged").fetchone()[0]

    def apply_merged_validation(self) -> None:
        """合并后全量主键校验（validate_merged_lake 等价，所有模式统一执行）。"""
        if not self.pk_cols or not self.merged_count:
            return
        # legacy 的 all_cols 是合并后所有行键的并集（含 content）；软删除生效时
        # 非删除行的标记键会被 pop，仅当仍有删除行时标记键才留在并集里
        counts = self.con.execute(
            f"SELECT count(*) FILTER ({_q(self.h['seq'])} < {self.base_count}), "
            f"count(*) FILTER ({_q(self.h['seq'])} >= {self.base_count}) FROM merged"
        ).fetchone()
        all_cols: set[str] = set()
        if counts[0]:
            all_cols |= self.base_all_cols
        if counts[1]:
            all_cols |= self.inc_all_keys
        if self.soft_active:
            all_cols -= set(_MARKERS)
            any_del = self.con.execute(
                f"SELECT count(*) FROM merged WHERE {_q(self.h['isdel'])}"
            ).fetchone()[0]
            if any_del:
                all_cols |= set(_MARKERS)
        try:
            self._validate_pk_sql(
                "merged", _q(self.h["rn"]), all_cols, "合并后的全量数据")
        except LakeGateError as exc:
            mode_hint = (
                "当前使用追加模式；有主键的资产应改用 upsert，或确保各批次主键互不重复。"
                if self.mode in ("append", "append_dedup") else
                "请修正合并逻辑或源数据后重试。")
            raise LakeGateError(
                f"{exc} {mode_hint}为保护既有版本，本次数据不会入湖。") from None

    def _validate_pk_sql(self, table: str, rowno_expr: str, all_cols: set[str],
                         scope: str) -> None:
        """主键三校验：列存在 / 值非空 / 组合唯一。文案与 validate_pk 逐字一致。"""
        name = self.dataset_name
        missing = [c for c in self.pk_cols if c not in all_cols]
        if missing:
            raise LakeGateError(
                f"数据集「{name}」{scope}中不存在主键列 {missing}"
                f"（现有列：{sorted(all_cols)[:20]}）。"
                f"请核对任务的主键配置，或调整流水线让输出携带该列。")

        view = f"vk_{table}"
        kcols = [_q(f"k{i}") for i in range(len(self.pk_hid))]
        cols_sql = ", ".join(
            f"{_py_strip_sql(_q(hid))} AS {kcols[i]}"
            for i, hid in enumerate(self.pk_hid))
        self.con.execute(
            f"CREATE TEMP VIEW {view} AS SELECT {rowno_expr} AS rn, {cols_sql} FROM {table}")
        klist = ", ".join(kcols)

        empty_rn = self.con.execute(
            f"SELECT MIN(rn) FROM {view} WHERE "
            + " OR ".join(f"{k} = ''" for k in kcols)).fetchone()[0]
        dup = self.con.execute(f"""
            WITH g AS (
              SELECT {klist}, MIN(rn) AS first_rn, COUNT(*) AS c
              FROM {view} GROUP BY {klist} HAVING COUNT(*) > 1
            ), s AS (
              SELECT g.first_rn AS first_rn, MIN({view}.rn) AS second_rn
              FROM g JOIN {view} ON {" AND ".join(f"g.{k} = {view}.{k}" for k in kcols)}
              WHERE {view}.rn > g.first_rn
              GROUP BY g.first_rn, {", ".join(f"g.{k}" for k in kcols)}
            )
            SELECT first_rn, second_rn FROM s ORDER BY second_rn LIMIT 1
        """).fetchone()
        # 报错需要的取值都在视图内，先取回再 DROP，最后按 legacy 优先级抛错
        empty_vals = None
        if empty_rn is not None:
            empty_vals = self.con.execute(
                f"SELECT {klist} FROM {view} WHERE rn = {int(empty_rn)}").fetchone()
        dup_vals = None
        if dup is not None:
            dup_vals = self.con.execute(
                f"SELECT {klist} FROM {view} WHERE rn = {int(dup[1])}").fetchone()
        self.con.execute(f"DROP VIEW {view}")

        # 先后判定与 legacy 逐行扫描一致：行号小者优先；同行内空值先于重复
        if empty_rn is not None and (dup is None or empty_rn <= dup[1]):
            col = next(c for c, v in zip(self.pk_cols, empty_vals) if v == "")
            raise LakeGateError(
                f"数据集「{name}」{scope}第 {empty_rn} 行的主键列「{col}」为空。"
                f"主键值必须非空，否则该行无法获得稳定身份。请在流水线中过滤或补全该列。")
        if dup is not None:
            first_rn, second_rn = dup
            key_dict = dict(zip(self.pk_cols, list(dup_vals)))
            raise LakeGateError(
                f"数据集「{name}」{scope}第 {first_rn} 行与第 {second_rn} 行主键重复"
                f"（{key_dict}）。同一主键值代表同一业务对象，{scope}内不允许重复；"
                f"若源端确有重复，请在流水线中先去重（保留最新）。")

    # ── 行解析（审计样本/类型推断/质量分用，量有界）──────────────
    def _fetch_base_rows(self, seqs) -> dict[int, dict]:
        """parquet 基座按 seq 定点取回（值为快照文本，与 legacy 解析行一致）。"""
        seqs = [int(s) for s in seqs]
        if not seqs or self.base_rows is not None or not self.base_count:
            return {}
        sel = ", ".join(
            [f"{_q(self.h['seq'])} AS __s"] + [_q(c) for c in self.base_value_cols])
        rows = self.con.execute(
            f"SELECT {sel} FROM base WHERE {_q(self.h['seq'])} IN "
            f"({', '.join(map(str, seqs))})").fetchall()
        names = list(self.base_value_cols)
        return {r[0]: dict(zip(names, r[1:])) for r in rows}

    def _resolve_after(self, seq: int, is_del: bool, cache: dict) -> dict:
        """合并后行 → legacy 中的 merged dict（增量行/解析基座直接取原对象）。"""
        if seq >= self.base_count:
            return self.new_rows[seq - self.base_count]      # 已就地打标
        if self.base_rows is not None:
            return self.base_rows[seq]                       # 保留行已就地打标
        row = dict(cache[seq])
        if self.soft_active:
            if is_del:
                row[_MARKER_DELETED] = True                  # 原生 bool，同 legacy
                row[_MARKER_AT] = self.run_ts
            else:
                row.pop(_MARKER_DELETED, None)
                row.pop(_MARKER_AT, None)
        return row

    def _resolve_before(self, seq: int, cache: dict,
                        marker_state: bool | None = None) -> dict:
        """入库前行 → legacy 中的 prev_rows dict。

        marker_state：None=原始形态（pk 模式）；True/False=no-pk 软删除下
        before 与 after 共享同一批被就地打标的 dict（legacy 别名语义）。
        """
        if self.base_rows is not None:
            return self.base_rows[seq]   # no-pk 软删除时保留行已全部就地打标
        row = dict(cache[seq])
        if marker_state is not None and self.soft_active:
            if marker_state:
                row[_MARKER_DELETED] = True
                row[_MARKER_AT] = self.run_ts
            else:
                row.pop(_MARKER_DELETED, None)
                row.pop(_MARKER_AT, None)
        return row

    # ── 审计 diff（compute_lake_impact 等价）────────────────────
    def compute_impact(self) -> dict:
        if self.pk_cols:
            added, updated, deleted, after_keys, samples = self._impact_keyed()
        else:
            added, updated, deleted, after_keys, samples = self._impact_by_row()
        return {
            "keyed_by": list(self.pk_cols) if self.pk_cols else None,
            "total_before": self.base_count,
            "total_after": self.merged_count,
            "added_count": added,
            "updated_count": updated,
            "deleted_count": deleted,
            "unchanged_count": max(0, after_keys - added - updated),
            "added_sample": samples[0],
            "updated_sample": samples[1],
            "deleted_sample": samples[2],
            "sample_truncated": (added > _SAMPLE_LIMIT or updated > _SAMPLE_LIMIT
                                 or deleted > _SAMPLE_LIMIT),
        }

    def _impact_keyed(self):
        """有主键：按主键组合识别同一行（能区分更新）。"""
        con = self.con
        seq = _q(self.h["seq"])
        isdel = _q(self.h["isdel"])
        m = self.base_count
        pks = ", ".join(_q(h) for h in self.pk_hid)
        join_cond = " AND ".join(f"bm.{_q(h)} = am.{_q(h)}" for h in self.pk_hid)
        con.execute(f"""
            CREATE TEMP VIEW bm AS
            SELECT * FROM (
              SELECT b.*, row_number() OVER (PARTITION BY {pks} ORDER BY {seq} DESC) AS rd,
                     MIN({seq}) OVER (PARTITION BY {pks}) AS first_seq
              FROM base b) WHERE rd = 1""")
        con.execute(f"""
            CREATE TEMP VIEW am AS
            SELECT * FROM (
              SELECT t.*, row_number() OVER (PARTITION BY {pks} ORDER BY {seq} DESC) AS rd,
                     MIN({seq}) OVER (PARTITION BY {pks}) AS first_seq
              FROM merged t) WHERE rd = 1""")
        # updated 判定：仅当 after 行来自增量——legacy 中基座保留行的 before/after
        # 是同一批 dict 对象（软删除就地打标），签名恒等，永远不会被判为 updated
        diffs = [f"am.{_q(self.h['keyset'])} != bm.{_q(self.h['keyset_plain'])}"]
        base_cols = set(self.base_value_cols)
        for c in self.sig_cols:
            b_expr = f"bm.{_q(c)}" if c in base_cols else "''"
            diffs.append(f"am.{_q(c)} != {b_expr}")
        updated_cond = f"(am.{seq} >= {m} AND (" + " OR ".join(diffs) + "))"

        added, deleted, updated, after_keys = con.execute(f"""
            SELECT
              count(*) FILTER (WHERE bm.{seq} IS NULL),
              count(*) FILTER (WHERE am.{seq} IS NULL),
              count(*) FILTER (WHERE bm.{seq} IS NOT NULL AND am.{seq} IS NOT NULL
                               AND {updated_cond}),
              (SELECT count(*) FROM am)
            FROM bm FULL OUTER JOIN am ON {join_cond}""").fetchone()
        added_seqs = con.execute(f"""
            SELECT am.{seq}, am.{isdel} FROM am
            WHERE NOT EXISTS (SELECT 1 FROM bm WHERE {join_cond})
            ORDER BY am.first_seq LIMIT {_SAMPLE_LIMIT}""").fetchall()
        updated_seqs = con.execute(f"""
            SELECT bm.{seq} AS bseq, am.{seq} AS aseq, am.{isdel}
            FROM bm JOIN am ON {join_cond} WHERE {updated_cond}
            ORDER BY am.first_seq LIMIT {_SAMPLE_LIMIT}""").fetchall()
        deleted_seqs = [r[0] for r in con.execute(f"""
            SELECT bm.{seq} FROM bm
            WHERE NOT EXISTS (SELECT 1 FROM am WHERE {join_cond})
            ORDER BY bm.first_seq LIMIT {_SAMPLE_LIMIT}""").fetchall()]
        con.execute("DROP VIEW bm")
        con.execute("DROP VIEW am")

        cache = self._fetch_base_rows(
            [s for s, _ in added_seqs if s < m]
            + [b for b, _, _ in updated_seqs] + [a for _, a, _ in updated_seqs if a < m]
            + list(deleted_seqs))
        added_sample = [_slim_row(self._resolve_after(s, d, cache))
                        for s, d in added_seqs]
        updated_sample = [
            {"before": _slim_row(self._resolve_before(b, cache)),
             "after": _slim_row(self._resolve_after(a, d, cache))}
            for b, a, d in updated_seqs]
        deleted_sample = [_slim_row(self._resolve_before(s, cache))
                          for s in deleted_seqs]
        return added, updated, deleted, after_keys, (
            added_sample, updated_sample, deleted_sample)

    def _impact_by_row(self):
        """无主键：整行内容比对，只有新增/删除（键即整行签名，无更新）。"""
        con = self.con
        seq = _q(self.h["seq"])
        isdel = _q(self.h["isdel"])
        # no-pk 软删除（upsert 无主键）时，legacy 的 before/after 共享同一批
        # 被就地打标的 dict——before 侧按打标后形态分组才能复刻该别名语义
        if self.soft_active:
            ks_b = (f"CASE WHEN {isdel} THEN {_q(self.h['keyset_del'])} "
                    f"ELSE {_q(self.h['keyset'])} END")
        else:
            ks_b = _q(self.h["keyset_plain"])
        base_cols = set(self.base_value_cols)
        sig_b = []
        for c in self.sig_cols:
            if c not in base_cols:
                sig_b.append(f"'' AS {_q(c)}")
            elif self.soft_active and c == _MARKER_DELETED:
                sig_b.append(f"CASE WHEN {isdel} THEN 'True' ELSE '' END AS {_q(c)}")
            elif self.soft_active and c == _MARKER_AT:
                sig_b.append(
                    f"CASE WHEN {isdel} THEN {_lit(self.run_ts)} ELSE '' END AS {_q(c)}")
            else:
                sig_b.append(f"{_q(c)} AS {_q(c)}")
        isd_b = isdel if self.soft_active else "FALSE"
        inner_cols = [f"{ks_b} AS ks"] + sig_b + [f"{seq} AS seq", f"{isd_b} AS isd"]
        group_cols = ", ".join(["ks"] + [_q(c) for c in self.sig_cols])
        con.execute(f"""
            CREATE TEMP VIEW bg AS
            SELECT {", ".join(
                ["ks"] + [_q(c) for c in self.sig_cols]
                + ["MAX(seq) AS last_seq", "MIN(seq) AS first_seq",
                   "arg_max(isd, seq) AS last_isdel"])}
            FROM (SELECT {", ".join(inner_cols)} FROM base)
            GROUP BY {group_cols}""")
        con.execute(f"""
            CREATE TEMP VIEW ag AS
            SELECT {", ".join(
                [f"{_q(self.h['keyset'])} AS ks"] + [_q(c) for c in self.sig_cols]
                + [f"MAX({seq}) AS last_seq", f"MIN({seq}) AS first_seq",
                   f"arg_max({isdel}, {seq}) AS last_isdel"])}
            FROM merged
            GROUP BY {group_cols}""")
        match = " AND ".join(
            [f"ag.ks = bg.ks"] + [f"ag.{_q(c)} = bg.{_q(c)}" for c in self.sig_cols])
        added, deleted, after_keys = con.execute(f"""
            SELECT
              (SELECT count(*) FROM ag WHERE NOT EXISTS
                (SELECT 1 FROM bg WHERE {match})),
              (SELECT count(*) FROM bg WHERE NOT EXISTS
                (SELECT 1 FROM ag WHERE {match})),
              (SELECT count(*) FROM ag)""").fetchone()
        added_seqs = con.execute(f"""
            SELECT last_seq, last_isdel FROM ag
            WHERE NOT EXISTS (SELECT 1 FROM bg WHERE {match})
            ORDER BY first_seq LIMIT {_SAMPLE_LIMIT}""").fetchall()
        deleted_seqs = con.execute(f"""
            SELECT last_seq, last_isdel FROM bg
            WHERE NOT EXISTS (SELECT 1 FROM ag WHERE {match})
            ORDER BY first_seq LIMIT {_SAMPLE_LIMIT}""").fetchall()
        con.execute("DROP VIEW bg")
        con.execute("DROP VIEW ag")

        m = self.base_count
        cache = self._fetch_base_rows(
            [s for s, _ in added_seqs if s < m] + [s for s, _ in deleted_seqs])
        added_sample = [_slim_row(self._resolve_after(s, d, cache))
                        for s, d in added_seqs]
        deleted_sample = [
            _slim_row(self._resolve_before(
                s, cache, marker_state=(d if self.soft_active else None)))
            for s, d in deleted_seqs]
        return added, 0, deleted, after_keys, (added_sample, [], deleted_sample)

    # ── 保留基座行的就地打标（legacy 对 merged 行 dict 的原地修改）──
    def mutate_kept_base_rows(self) -> None:
        """解析型基座：对合并保留行就地打标/摘除软删除标记（upsert 专用）。"""
        if self.base_rows is None or not self.soft_active or not self.base_count:
            return
        kept = [r[0] for r in self.con.execute(
            f"SELECT {_q(self.h['seq'])} FROM merged "
            f"WHERE {_q(self.h['seq'])} < {self.base_count}").fetchall()]
        for s in kept:
            row = self.base_rows[s]
            if self._row_is_del(row):
                row[_MARKER_DELETED] = True
                row[_MARKER_AT] = self.run_ts
            else:
                row.pop(_MARKER_DELETED, None)
                row.pop(_MARKER_AT, None)

    # ── 列清单 / 类型推断 / 快照 / 物化 ─────────────────────────
    def _base_row_keys(self, is_del: bool) -> list[str]:
        """parquet 基座单行的键序（统一 schema；软删除标记按 legacy 增删）。"""
        keys = [c for c in self.base_schema if c != _CONTENT_COL]
        if not self.soft_active:
            return keys
        if is_del:
            # 已存在的标记列保留原位，新增的按赋值顺序追加
            return keys + [m for m in _MARKERS if m not in self.base_schema]
        return [k for k in keys if k not in _MARKERS]

    def compute_columns(self) -> list[str]:
        """合并后列并集（首现序）= legacy rows_to_parquet_bytes/infer 的列序。

        parquet 基座行 schema 统一，列贡献只看首个保留行（软删除标记列看
        首个被删除行）；增量/解析基座按保留行逐行贡献。
        """
        events: list[tuple[int, list[str]]] = []
        seq_q = _q(self.h["seq"])
        if self.mode != "overwrite" and self.base_count:
            if self.base_rows is not None:
                kept = [r[0] for r in self.con.execute(
                    f"SELECT {seq_q} FROM merged WHERE {seq_q} < {self.base_count} "
                    f"ORDER BY {seq_q}").fetchall()]
                for s in kept:
                    events.append((s, [
                        str(k) for k in self.base_rows[s].keys()
                        if str(k) != _CONTENT_COL]))
            else:
                s0 = self.con.execute(
                    f"SELECT MIN({seq_q}) FROM merged WHERE {seq_q} < {self.base_count}"
                ).fetchone()[0]
                if s0 is not None:
                    if self.soft_active:
                        is0 = self.con.execute(
                            f"SELECT {_q(self.h['isdel'])} FROM merged "
                            f"WHERE {seq_q} = {int(s0)}").fetchone()[0]
                        events.append((s0, self._base_row_keys(bool(is0))))
                        s1 = self.con.execute(
                            f"SELECT MIN({seq_q}) FROM merged "
                            f"WHERE {seq_q} < {self.base_count} AND {_q(self.h['isdel'])}"
                        ).fetchone()[0]
                        if s1 is not None:
                            events.append((s1, self._base_row_keys(True)))
                    else:
                        events.append((s0, self._base_row_keys(False)))
        kept_inc = [r[0] for r in self.con.execute(
            f"SELECT {seq_q} FROM merged WHERE {seq_q} >= {self.base_count} "
            f"ORDER BY {seq_q}").fetchall()]
        for s in kept_inc:
            events.append((s, [
                str(k) for k in self.new_rows[s - self.base_count].keys()
                if str(k) != _CONTENT_COL]))

        columns: list[str] = []
        seen: set[str] = set()
        for _, keys in sorted(events, key=lambda e: e[0]):
            for k in keys:
                if k != _CONTENT_COL and k not in seen:
                    seen.add(k)
                    columns.append(k)
        return columns

    def compute_columns_typed(self, columns: list[str]) -> list[dict]:
        """带类型列清单：首 50 行解析回原始行后直接复用 infer_columns_typed。"""
        if not self.merged_count:
            return []
        first = self.con.execute(
            f"SELECT {_q(self.h['seq'])}, {_q(self.h['isdel'])} FROM merged "
            f"ORDER BY {_q(self.h['rn'])} LIMIT 50").fetchall()
        cache = self._fetch_base_rows([s for s, _ in first if s < self.base_count])
        resolved = [self._resolve_after(s, d, cache) for s, d in first]
        typed = {c["name"]: c["type"] for c in infer_columns_typed(resolved)}
        # 首 50 行未出现的列：与 infer_columns_typed 的默认 "string" 一致
        return [{"name": c, "type": typed.get(c, "string")} for c in columns]

    def write_snapshot(self, columns: list[str]) -> bytes:
        """DuckDB 直写 parquet（全列 VARCHAR + zstd）；空表/空列 → b""。"""
        if not self.merged_count or not columns:
            return b""
        sel = ", ".join(_q(c) for c in columns)
        out = os.path.join(self.tmpdir, "snapshot.parquet")
        self.con.execute(
            f"COPY (SELECT {sel} FROM merged ORDER BY {_q(self.h['seq'])}) "
            f"TO {_lit(out)} (FORMAT PARQUET, COMPRESSION 'ZSTD')")
        with open(out, "rb") as fh:
            return fh.read()

    def materialize_merged(self) -> list[dict]:
        """物化合并后全量行（仅空增量场景为质量分调用，与 legacy 同构）。

        键存在性必须逐行精确（质量分按 len(row) 计数）：parquet 基座行只携带
        基座 schema 的键，增量行直接取原始 dict——不能用并集列统一补空串。
        """
        if not self.merged_count:
            return []
        seq_q = _q(self.h["seq"])
        out: list[dict] = []
        # 基座部分（seq 全部小于增量，直接按序拼接即可保持合并序）
        if self.base_count and self.mode != "overwrite":
            if self.base_rows is not None:
                kept = [r[0] for r in self.con.execute(
                        f"SELECT {seq_q} FROM merged WHERE {seq_q} < {self.base_count} "
                        f"ORDER BY {seq_q}").fetchall()]
                out.extend(self.base_rows[s] for s in kept)
            else:
                cols = [_q(c) for c in self.base_value_cols]
                sel = ", ".join(
                    [f"m.{seq_q} AS __s", f"m.{_q(self.h['isdel'])} AS __d"]
                    + [f"b.{c}" for c in cols])
                fetched = self.con.execute(
                    f"SELECT {sel} FROM merged m JOIN base b ON m.{seq_q} = b.{seq_q} "
                    f"WHERE m.{seq_q} < {self.base_count} ORDER BY m.{seq_q}").fetchall()
                for r in fetched:
                    row = dict(zip(self.base_value_cols, r[2:]))
                    if self.soft_active:
                        if r[1]:
                            row[_MARKER_DELETED] = True
                            row[_MARKER_AT] = self.run_ts
                        else:
                            row.pop(_MARKER_DELETED, None)
                            row.pop(_MARKER_AT, None)
                    out.append(row)
        kept_inc = [r[0] for r in self.con.execute(
            f"SELECT {seq_q} FROM merged WHERE {seq_q} >= {self.base_count} "
            f"ORDER BY {seq_q}").fetchall()]
        out.extend(self.new_rows[s - self.base_count] for s in kept_inc)
        return out
