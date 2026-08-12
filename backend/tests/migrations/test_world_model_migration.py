"""Migration coverage for the world-model tables and role-menu backfill."""

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
    db_path = tmp_path / "world-model-migration.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "0064_merge_0063_heads")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        # 0036 迁移已播种 editor/viewer 行：按测试意图重置其授权
        connection.execute(text(
            "UPDATE role_menu_permissions SET menu_keys = :menu_keys "
            "WHERE role = 'editor'"
        ), {"menu_keys": json.dumps(["overview", "ontologies", "agent"])})
        connection.execute(text(
            "UPDATE role_menu_permissions SET menu_keys = :menu_keys "
            "WHERE role = 'viewer'"
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
        assert "world_model_projects" in tables
        assert "world_model_script_versions" in tables
        assert "world_model_call_records" in tables

        rows = connection.execute(text(
            "SELECT role,menu_keys FROM role_menu_permissions ORDER BY role"
        )).mappings().all()
    engine.dispose()

    by_role = {row["role"]: json.loads(row["menu_keys"]) for row in rows}
    # 持有 ontologies 的角色被回填两个子 key（否则归一化会剥除父 key）
    assert "ontologies.library" in by_role["editor"]
    assert "ontologies.world_model" in by_role["editor"]
    assert "ontologies" in by_role["editor"]
    # 未持有 ontologies 的角色不受影响
    assert "ontologies.library" not in by_role["viewer"]
    assert "ontologies.world_model" not in by_role["viewer"]


def test_downgrade_removes_tables_and_menu_key(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "world-model-downgrade.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE role_menu_permissions SET menu_keys = :menu_keys "
            "WHERE role = 'editor'"
        ), {"menu_keys": json.dumps(
            ["ontologies", "ontologies.library", "ontologies.world_model"])})
    engine.dispose()

    command.downgrade(cfg, "0064_merge_0063_heads")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"))
        }
        assert "world_model_projects" not in tables
        row = connection.execute(text(
            "SELECT menu_keys FROM role_menu_permissions WHERE role='editor'"
        )).mappings().one()
    engine.dispose()

    keys = json.loads(row["menu_keys"])
    assert "ontologies.library" not in keys
    assert "ontologies.world_model" not in keys
