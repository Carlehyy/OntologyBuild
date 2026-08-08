"""合并引擎性能基准：legacy 内存合并 vs merge_engine（DuckDB 下推）。

场景：基座 100 万行 × 20 列 + 增量 1 万行（upsert / append_dedup）。
两侧各记录墙钟与 tracemalloc 峰值，报告写
.artifacts/pr5-merge-engine-benchmark.json（gitignored）。

注意：tracemalloc 只追踪 Python 侧分配，DuckDB 原生内存不在内——这正是本 PR
的论点：新引擎的 Python 侧峰值不再随湖中总量增长（只随增量批次增长）。
"""
from __future__ import annotations

import gc
import json
import platform
import time
import tracemalloc
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

BASE_ROWS = 1_000_000
BASE_COLS = 20
INC_ROWS = 10_000
# 本机（48GB）legacy 可跑满 100 万行；更小内存的机器可降到能跑的规模并在
# 报告的 note 中注明——新引擎在任何机器上都必须跑满 100 万行。
LEGACY_BASE_ROWS = BASE_ROWS

REPORT_PATH = (
    Path(__file__).resolve().parents[4]
    / ".artifacts" / "pr5-merge-engine-benchmark.json"
)

_records: list[dict] = []


def _build_base_bytes(n: int, cols: int) -> bytes:
    """直接以 pyarrow 生成基座 parquet（与 rows_to_parquet_bytes 同物理格式：
    全列 VARCHAR + zstd），避免为造数据先支付一次 Python 侧全量物化。"""
    import io

    data = {"id": pa.array([f"k{i:07d}" for i in range(n)], type=pa.string())}
    for j in range(cols - 1):
        data[f"c{j}"] = pa.array(
            [f"v{i % 5000}-{j}" for i in range(n)], type=pa.string())
    buf = io.BytesIO()
    pq.write_table(pa.table(data), buf, compression="zstd")
    return buf.getvalue()


def _inc_rows(n: int, *, start: int = 0) -> list[dict]:
    return [
        {"id": f"k{(start + i) % BASE_ROWS:07d}",
         **{f"c{j}": f"n{start + i}-{j}" for j in range(BASE_COLS - 1)}}
        for i in range(n)
    ]


def _legacy_chain(base_bytes: bytes, new_rows: list[dict], mode: str,
                  pk_cols: list[str]):
    """复刻 _save_curated_dataset_in_lock 的 legacy 合并段（全量读改写）。"""
    from app.data_channel.datasets.lake_gate import (
        infer_columns_typed, validate_merged_lake, validate_upsert_base)
    from app.data_channel.datasets.service import (
        _parse_stored_rows, rows_to_parquet_bytes)
    from app.data_channel.pipeline_tasks.merge import (
        compute_lake_impact, merge_rows)

    old = _parse_stored_rows(base_bytes, limit=None)  # = load_all_rows 全量物化
    if mode == "upsert":
        validate_upsert_base(old, pk_cols, dataset_name="基准数据集")
    merged, meta = merge_rows(
        old, new_rows, {"mode": mode, "primary_key": ",".join(pk_cols)})
    validate_merged_lake(merged, pk_cols, dataset_name="基准数据集",
                         write_mode=meta["mode"])
    impact = compute_lake_impact(old, merged, pk_cols)
    typed = infer_columns_typed(merged)
    blob = rows_to_parquet_bytes(merged)
    return len(merged), len(blob), impact["unchanged_count"]


def _engine_chain(base_bytes: bytes, new_rows: list[dict], mode: str,
                  pk_cols: list[str]):
    from app.data_channel.pipeline_tasks.merge_engine import merge_lake_increment

    outcome = merge_lake_increment(
        base_bytes=base_bytes, new_rows=new_rows,
        write_opts={"mode": mode, "primary_key": ",".join(pk_cols)},
        pk_cols=pk_cols, dataset_name="基准数据集", dataset_id="ds-bench",
        base_version_no=1)
    return outcome.rowcount, len(outcome.parquet_bytes), \
        outcome.lake_impact["unchanged_count"]


def _measure(fn):
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    result = fn()
    wall = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, wall, peak


def _record(mode: str, impl: str, base_rows: int, result, wall: float,
            peak: int, note: str = "") -> dict:
    rec = {
        "scenario": f"base_{base_rows}x{BASE_COLS}__inc_{INC_ROWS}__{mode}",
        "impl": impl,
        "base_rows": base_rows,
        "inc_rows": INC_ROWS,
        "mode": mode,
        "wall_s": round(wall, 3),
        "peak_tracemalloc_mb": round(peak / 1024 / 1024, 1),
        "merged_rows": result[0],
        "snapshot_bytes": result[1],
        "note": note,
    }
    _records.append(rec)
    return rec


def _write_report():
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps({
        "title": "PR5 合并引擎基准：legacy 内存合并 vs merge_engine（DuckDB 下推）",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "duckdb": __import__("duckdb").__version__,
        },
        "method": "墙钟 = time.perf_counter；峰值 = tracemalloc（仅 Python 侧）",
        "records": _records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture(scope="module")
def base_bytes():
    return _build_base_bytes(BASE_ROWS, BASE_COLS)


def test_benchmark_upsert_1m_base(base_bytes):
    """upsert：增量 1 万行全部命中既有主键（最重的合并形态）。"""
    inc = _inc_rows(INC_ROWS)

    legacy_result, legacy_wall, legacy_peak = _measure(
        lambda: _legacy_chain(base_bytes, inc, "upsert", ["id"]))
    _record("upsert", "legacy", BASE_ROWS, legacy_result, legacy_wall,
            legacy_peak, note="全量读入 Python 内存合并")

    engine_result, engine_wall, engine_peak = _measure(
        lambda: _engine_chain(base_bytes, inc, "upsert", ["id"]))
    rec = _record("upsert", "merge_engine", BASE_ROWS, engine_result,
                  engine_wall, engine_peak, note="DuckDB 下推，零基座物化")
    _write_report()

    assert engine_result[0] == legacy_result[0], "合并行数不一致"
    assert engine_result[2] == legacy_result[2], "审计 unchanged 计数不一致"
    assert engine_wall < 120, f"新引擎 upsert 100 万基座耗时 {engine_wall:.1f}s"
    assert engine_peak < legacy_peak, (
        f"新引擎 Python 峰值 {rec['peak_tracemalloc_mb']}MB 应显著低于 legacy")


def test_benchmark_append_dedup_1m_base(base_bytes):
    """append_dedup：增量中一半与基座整行重复、一半为新行。"""
    dupes = [
        {"id": f"k{i:07d}", **{f"c{j}": f"v{i % 5000}-{j}"
                               for j in range(BASE_COLS - 1)}}
        for i in range(INC_ROWS // 2)
    ]
    fresh = [
        {"id": f"new-{i:05d}", **{f"c{j}": f"f{i}-{j}"
                                  for j in range(BASE_COLS - 1)}}
        for i in range(INC_ROWS - INC_ROWS // 2)
    ]
    inc = dupes + fresh

    legacy_result, legacy_wall, legacy_peak = _measure(
        lambda: _legacy_chain(base_bytes, inc, "append_dedup", []))
    _record("append_dedup", "legacy", BASE_ROWS, legacy_result, legacy_wall,
            legacy_peak, note="全量读入 Python 内存合并")

    engine_result, engine_wall, engine_peak = _measure(
        lambda: _engine_chain(base_bytes, inc, "append_dedup", []))
    rec = _record("append_dedup", "merge_engine", BASE_ROWS, engine_result,
                  engine_wall, engine_peak, note="DuckDB 下推，零基座物化")
    _write_report()

    assert engine_result[0] == legacy_result[0], "合并行数不一致"
    assert engine_wall < 120, f"新引擎 append_dedup 100 万基座耗时 {engine_wall:.1f}s"
    assert engine_peak < legacy_peak, (
        f"新引擎 Python 峰值 {rec['peak_tracemalloc_mb']}MB 应显著低于 legacy")
