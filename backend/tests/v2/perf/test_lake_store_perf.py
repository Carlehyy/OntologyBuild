"""物理湖表性能基准：lake_store 行级 upsert / 分页 / 流式读 / 版本回放。

规模与口径（任务书：50 万行基座）：
- 基座 50 万行 × 8 列；基座大头用 Core 批量直插预置（仅基准 fixture 捷径，
  避开来数字典的 Python 生成成本——该行级成本在记录里单独标注），
  随后 5 万行增量 upsert / 50 万行 overwrite 走完整 upsert_run 路径计时；
- 方言 SQLite（本机临时文件）。PG 侧差异：TEMP 暂存与 PK 索引 JOIN 在 PG
  上通常不劣于 SQLite；OFFSET 深分页两方言同构（都需扫过前 N 行）。
- tracemalloc 只追踪 Python 侧分配。

报告写 .artifacts/lake-store-benchmark.json（gitignored，不进 PR 门禁）。
"""
from __future__ import annotations

import gc
import json
import platform
import time
import tracemalloc
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.data_channel.datasets import lake_store
from app.data_channel.datasets.models import Dataset

BASE_ROWS = 500_000
INC_ROWS = 50_000
COLS = 8

REPORT_PATH = (
    Path(__file__).resolve().parents[4]
    / ".artifacts" / "lake-store-benchmark.json"
)

_records: list[dict] = []


def _row(i: int, *, tag: str = "v") -> dict:
    return {"id": f"k{i:07d}",
            **{f"c{j}": f"{tag}{i % 5000}-{j}" for j in range(COLS - 1)}}


def _measure(fn):
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    result = fn()
    wall = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, wall, peak


def _record(scenario: str, wall: float, peak: int, note: str = "") -> dict:
    rec = {
        "scenario": scenario,
        "wall_s": round(wall, 3),
        "peak_tracemalloc_mb": round(peak / 1024 / 1024, 1),
        "note": note,
    }
    _records.append(rec)
    return rec


def _write_report():
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps({
        "title": "lake_store 物理湖表基准（SQLite，50 万行基座）",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "dialect": "sqlite",
        },
        "method": "墙钟 = time.perf_counter；峰值 = tracemalloc（仅 Python 侧）",
        "records": _records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture(scope="module")
def lake_db(tmp_path_factory):
    """独立 SQLite 文件库 + 50 万行基座（模块级，整个基准共用）。"""
    from app.database import Base
    from app.model_registry import import_all_models

    import_all_models()
    db_path = tmp_path_factory.mktemp("lake_perf") / "bench.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    ds = Dataset(
        id=str(uuid.uuid4()), name="基准数据集", kind="curated",
        schema_json={"primary_key": "id",
                     "columns": ["id"] + [f"c{j}" for j in range(COLS - 1)]})
    session.add(ds)
    session.commit()

    # v1：经 upsert_run 建立物理表与首个版本（小批 5000 行）
    lake_store.upsert_run(session, ds, [_row(i) for i in range(5000)],
                          "overwrite", ["id"])
    # 基座大头：Core 批量直插预置（基准 fixture 捷径，不产生变更集；
    # 避免为造数先支付一次 50 万行 dict 的 Python 物化）
    _, mapping, _ = lake_store._contract(ds)
    table = lake_store.lake_table_definition(
        lake_store.lake_table_name(ds.id), mapping, ["id"])
    gen_start = time.perf_counter()
    conn = session.connection()
    batch = []
    for i in range(5000, BASE_ROWS):
        batch.append(lake_store.normalize_lake_rows([_row(i)], mapping, ["id"])[0])
        if len(batch) >= 5000:
            conn.execute(table.insert(), batch)
            batch.clear()
    if batch:
        conn.execute(table.insert(), batch)
    session.commit()
    gen_wall = time.perf_counter() - gen_start
    _record("base_build_500k_direct_insert", gen_wall, 0,
            note="直插预置基座（含逐行规范化），仅供 fixture，不走 upsert_run")
    yield session, ds
    session.close()
    engine.dispose()


def test_benchmark_lake_store(lake_db):
    session, ds = lake_db

    # ── 5 万行增量 upsert（全命中既有主键，最重合并形态）────────────
    inc_rows = [_row(i % BASE_ROWS, tag="n") for i in range(INC_ROWS)]
    (version, changeset), wall, peak = _measure(
        lambda: lake_store.upsert_run(session, ds, inc_rows, "upsert", ["id"]))
    assert changeset.updated_count == INC_ROWS
    _record("upsert_inc_50k_on_base_500k", wall, peak,
            note="TEMP 暂存 + SQL diff + DELETE/INSERT + 5 万条变更集逐行")

    # ── 50 万行 overwrite 全量替换 ────────────────────────────────
    overwrite_rows = [_row(i, tag="w") for i in range(BASE_ROWS)]
    (v_over, cs_over), wall, peak = _measure(
        lambda: lake_store.upsert_run(
            session, ds, overwrite_rows, "overwrite", ["id"]))
    assert v_over.rowcount == BASE_ROWS
    assert cs_over.added_count + cs_over.updated_count > 0
    _record("overwrite_500k_full_replace", wall, peak,
            note="全量替换：50 万 staged + 删除旧 50 万 + 变更集逐行（最重路径）")

    # ── 深分页 vs 首页 ───────────────────────────────────────────
    page, wall_first, _ = _measure(
        lambda: lake_store.page_rows(session, ds, 0, 100))
    assert len(page) == 100
    _record("page_rows_first_page_100", wall_first, 0, note="主键序首页")
    page, wall_deep, _ = _measure(
        lambda: lake_store.page_rows(session, ds, BASE_ROWS - 10_000, 100))
    assert len(page) == 100
    rec = _record("page_rows_deep_offset_490k", wall_deep, 0,
                  note="OFFSET 深分页（两方言同构需扫过前 49 万行）")
    assert wall_first < 5 and wall_deep < 30

    # ── 全量流式读 ───────────────────────────────────────────────
    total, wall, peak = _measure(lambda: sum(
        len(batch) for batch in lake_store.stream_rows(
            session, ds, batch_size=5000)))
    assert total == BASE_ROWS
    _record("stream_rows_full_500k", wall, peak,
            note="5000/批全量流式收集计数（不持有全量列表）")

    # ── 逆向回放到上一版本（当前 50 万表 + 撤销 overwrite 变更集）────
    target = v_over.version_no - 1
    rows, wall, peak = _measure(
        lambda: lake_store.rows_at_version(session, ds, target))
    assert len(rows) == BASE_ROWS
    _record("rows_at_version_replay_one_step", wall, peak,
            note=f"回放到 v{target}：全表流式 + 撤销 1 个 overwrite 变更集")

    _write_report()
