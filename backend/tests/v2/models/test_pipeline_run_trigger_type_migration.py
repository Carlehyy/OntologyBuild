"""0063_pipeline_run_trigger_type 迁移在 SQLite 方言上的契约测试：
加列、建索引、从 stats JSON 分批回填（缺键行保持 NULL ≡ manual）、
stats JSON 键不动、downgrade 回退列与索引。"""
from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration_module():
    path = (Path(__file__).parents[3] / "alembic" / "versions" /
            "2026_08_12_0063_pipeline_run_trigger_type.py")
    spec = spec_from_file_location("pipeline_run_trigger_type_migration", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_trigger_type_migration_backfills_stats_and_downgrades_on_sqlite(
    monkeypatch,
):
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "v2_pipeline_runs", metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pipeline_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=True),
    )
    metadata.create_all(engine)

    with engine.connect() as conn:
        conn.execute(sa.text(
            "INSERT INTO v2_pipeline_runs (id, pipeline_id, status, stats)"
            " VALUES "
            "('run-scheduled', 'p1', 'success', :scheduled), "
            "('run-manual', 'p1', 'failed', :manual), "
            "('run-legacy', 'p1', 'success', NULL), "
            "('run-empty-stats', 'p1', 'success', :empty)"), {
                "scheduled": json.dumps(
                    {"trigger_type": "scheduled", "rows_out": 3}),
                "manual": json.dumps({"trigger_type": "manual"}),
                "empty": json.dumps({"rows_out": 1}),
            })
        conn.commit()

        migration = _load_migration_module()
        # 批量压到 1，逐行走完分批提交循环，验证键集翻页可续跑
        monkeypatch.setattr(migration, "_BACKFILL_BATCH", 1)
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()

        inspector = sa.inspect(conn)
        assert "trigger_type" in {
            col["name"] for col in inspector.get_columns("v2_pipeline_runs")}
        assert "ix_v2_pipeline_runs_trigger_type" in {
            idx["name"] for idx in inspector.get_indexes("v2_pipeline_runs")}
        rows = dict(conn.execute(sa.text(
            "SELECT id, trigger_type FROM v2_pipeline_runs")).all())
        assert rows == {
            "run-scheduled": "scheduled",
            "run-manual": "manual",
            # stats 缺键/无 stats 与 NULL 同义（按 manual 计），列保持 NULL
            "run-legacy": None,
            "run-empty-stats": None,
        }
        # stats JSON 键保留不动（HTTP 契约不变）
        stats = conn.execute(sa.text(
            "SELECT stats FROM v2_pipeline_runs WHERE id='run-scheduled'"
        )).scalar_one()
        assert json.loads(stats)["trigger_type"] == "scheduled"

        migration.downgrade()
        inspector = sa.inspect(conn)
        assert "trigger_type" not in {
            col["name"] for col in inspector.get_columns("v2_pipeline_runs")}
        assert "ix_v2_pipeline_runs_trigger_type" not in {
            idx["name"] for idx in inspector.get_indexes("v2_pipeline_runs")}
