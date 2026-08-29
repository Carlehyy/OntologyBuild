"""Migration coverage for MCP display fields (0084)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_upgrade_adds_and_backfills_display_fields(tmp_path, monkeypatch):
    """存量 MCP 行在 0084 升级后 display_name 回填为标识，新列可干净降级。"""
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "mcp-display-migration.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0083_tickets_category_page")

    engine = create_engine(f"sqlite:///{db_path}")
    columns = {
        c["name"]
        for c in inspect(engine).get_columns("super_assistant_mcp_servers")
    }
    assert "display_name" not in columns
    assert "description" not in columns

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO super_assistant_mcp_servers "
                "(id, owner_id, name, builtin_key, transport, url, header_names, "
                "args, env_names, enabled, require_confirmation, tool_manifest, "
                "created_at, updated_at) "
                "VALUES ('mcp-1', 'owner-1', 'dmp-mcp-server', NULL, "
                "'streamable_http', 'https://example.com/mcp', '[]', '[]', '[]', "
                "1, 1, '[]', '2026-08-29', '2026-08-29')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT name, display_name, description "
                "FROM super_assistant_mcp_servers WHERE id = 'mcp-1'"
            )
        ).fetchone()
    assert row.name == "dmp-mcp-server"
    assert row.display_name == "dmp-mcp-server"
    assert row.description == ""
    engine.dispose()
