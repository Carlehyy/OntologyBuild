"""Migration coverage for assistant evaluation tables (0078)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_upgrade_creates_assistant_eval_tables(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "assistant-eval-migration.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert "assistant_eval_tasks" in tables
    assert "assistant_eval_items" in tables

    task_columns = {c["name"] for c in inspect(engine).get_columns("assistant_eval_tasks")}
    assert {"id", "assistant_key", "status", "params", "summary",
            "conversation_count", "created_by"} <= task_columns
    item_columns = {c["name"] for c in inspect(engine).get_columns("assistant_eval_items")}
    assert {"id", "task_id", "conversation_id", "scores", "reasons",
            "flags", "overall_score"} <= item_columns

    indexes = {ix["name"] for ix in inspect(engine).get_indexes("assistant_eval_tasks")}
    assert "ix_ae_tasks_assistant_created" in indexes
    item_indexes = {ix["name"] for ix in inspect(engine).get_indexes("assistant_eval_items")}
    assert "ix_ae_items_task" in item_indexes
    engine.dispose()


def test_downgrade_drops_assistant_eval_tables(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "assistant-eval-downgrade.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert "assistant_eval_tasks" not in tables
    assert "assistant_eval_items" not in tables
    engine.dispose()
