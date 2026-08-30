"""Migration coverage for assistant evaluation flywheel M1 tables (0085)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_upgrade_creates_flywheel_tables(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "assistant-eval-flywheel-migration.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert "assistant_eval_benchmark_sets" in tables
    assert "assistant_eval_benchmark_items" in tables
    assert "assistant_eval_calibrations" in tables
    assert "assistant_eval_timeline_events" in tables

    item_columns = {c["name"] for c in inspect(engine).get_columns("assistant_eval_items")}
    assert "attribution" in item_columns

    bench_indexes = {ix["name"] for ix in inspect(engine)
                     .get_indexes("assistant_eval_benchmark_items")}
    assert "ix_ae_bench_items_set" in bench_indexes
    timeline_indexes = {ix["name"] for ix in inspect(engine)
                        .get_indexes("assistant_eval_timeline_events")}
    assert {"ix_ae_timeline_assistant_created", "ix_ae_timeline_ref"} <= timeline_indexes
    engine.dispose()


def test_downgrade_drops_flywheel_tables(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "assistant-eval-flywheel-downgrade.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0084_mcp_display_fields")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert "assistant_eval_benchmark_sets" not in tables
    assert "assistant_eval_benchmark_items" not in tables
    assert "assistant_eval_calibrations" not in tables
    assert "assistant_eval_timeline_events" not in tables
    item_columns = {c["name"] for c in inspect(engine).get_columns("assistant_eval_items")}
    assert "attribution" not in item_columns
    engine.dispose()
