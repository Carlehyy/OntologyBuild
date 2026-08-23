"""Migration coverage for the scenes tables and role-menu backfill."""

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_upgrade_creates_tables_and_backfills_role_menu_keys(
    tmp_path, monkeypatch,
):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "scenes-migration.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "0073_drop_agent_config")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        # 0036 迁移已播种 editor/viewer 行：按测试意图重置其授权
        connection.execute(text(
            "UPDATE role_menu_permissions SET menu_keys = :menu_keys "
            "WHERE role = 'editor'"
        ), {"menu_keys": json.dumps(["overview", "agent"])})
        connection.execute(text(
            "UPDATE role_menu_permissions SET menu_keys = :menu_keys "
            "WHERE role = 'viewer'"
        ), {"menu_keys": json.dumps(["overview"])})
        connection.execute(text(
            "UPDATE role_menu_permissions SET menu_keys = :menu_keys "
            "WHERE role = 'custom'"
        ), {"menu_keys": json.dumps(["overview"])})
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"))
        }
        assert "scenes" in tables
        assert "scene_versions" in tables
        assert "scene_runtime_logs" in tables
        assert "scene_conversations" in tables
        assert "scene_messages" in tables

        version_columns = {
            row[1]
            for row in connection.execute(text(
                "PRAGMA table_info(scene_versions)"))
        }
        assert {"scene_id", "version_no", "definition", "source"} <= version_columns

        rows = connection.execute(text(
            "SELECT role,menu_keys FROM role_menu_permissions ORDER BY role"
        )).mappings().all()
    engine.dispose()

    by_role = {row["role"]: json.loads(row["menu_keys"]) for row in rows}
    # editor/viewer 是「非 admin 默认全量」角色：新一级菜单自动可见
    assert "scenes" in by_role["editor"]
    assert "scenes" in by_role["viewer"]
    # custom 坚持显式授予，不受回填影响
    assert "scenes" not in by_role.get("custom", [])


def test_downgrade_removes_tables_and_menu_key(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "scenes-downgrade.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0073_drop_agent_config")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"))
        }
        assert "scenes" not in tables
        assert "scene_versions" not in tables
        assert "scene_runtime_logs" not in tables
        assert "scene_conversations" not in tables
        assert "scene_messages" not in tables
        rows = connection.execute(text(
            "SELECT role,menu_keys FROM role_menu_permissions ORDER BY role"
        )).mappings().all()
    engine.dispose()

    by_role = {row["role"]: json.loads(row["menu_keys"]) for row in rows}
    for keys in by_role.values():
        assert "scenes" not in keys
