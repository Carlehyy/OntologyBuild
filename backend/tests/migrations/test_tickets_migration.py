"""Migration coverage for tickets tables (0082)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_upgrade_creates_ticket_tables(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "tickets-migration.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert {
        "tickets", "ticket_attachments", "ticket_progress_logs",
    } <= tables

    columns = {c["name"] for c in inspect(engine).get_columns("tickets")}
    assert {
        "id", "ticket_no", "title", "content", "status",
        "submitter_id", "submitter_name", "created_at", "updated_at",
    } <= columns
    engine.dispose()


def test_downgrade_drops_ticket_tables(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "tickets-downgrade.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0081_assistant_widget_config")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert "tickets" not in tables
    assert "ticket_attachments" not in tables
    assert "ticket_progress_logs" not in tables
    engine.dispose()
