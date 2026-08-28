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
        "id", "ticket_no", "title", "content", "status", "category", "page_url",
        "submitter_id", "submitter_name", "created_at", "updated_at",
    } <= columns
    engine.dispose()


def test_0083_backfills_category_on_existing_rows(tmp_path, monkeypatch):
    """存量工单在 0083 升级后分类回填为 other，且可干净降级。

    全新库的初始迁移按当前模型建表（新列天然存在），因此这里先降到 0082
    复刻「无 category/page_url」的存量形态，再走真实的 0083 升级路径。
    """
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "tickets-0083-backfill.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0082_tickets")

    from sqlalchemy import text
    engine = create_engine(f"sqlite:///{db_path}")
    columns = {c["name"] for c in inspect(engine).get_columns("tickets")}
    assert "category" not in columns
    assert "page_url" not in columns
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO tickets (id, ticket_no, title, content, status, created_at, updated_at)"
            " VALUES ('t1', 'TK-OLD-001', '旧工单', '旧内容', 'pending', '2026-08-28 00:00:00', '2026-08-28 00:00:00')"
        ))
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        category = connection.execute(
            text("SELECT category FROM tickets WHERE id = 't1'")).scalar()
    assert category == "other"
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
