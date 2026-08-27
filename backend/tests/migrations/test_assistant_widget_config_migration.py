"""Migration coverage for assistant widget config table (0080)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_upgrade_creates_widget_config_table(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "assistant-widget-config-migration.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert "super_assistant_widget_config" in tables

    columns = {c["name"] for c in inspect(engine).get_columns("super_assistant_widget_config")}
    assert {"id", "hidden_menu_keys", "updated_by", "updated_at"} <= columns
    engine.dispose()


def test_downgrade_drops_widget_config_table(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "assistant-widget-config-downgrade.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0080_user_env_vars")

    engine = create_engine(f"sqlite:///{db_path}")
    assert "super_assistant_widget_config" not in set(inspect(engine).get_table_names())
    engine.dispose()
