"""Migration coverage for the memory palace first-class folders table.

全新库经迁移 0003 的 create_all 已按最新模型建出 super_assistant_palace_folders
（0093 建表守卫跳过）；本迁移对存量库的核心价值是「存量文件目录回填」，
这里用带存量 folder_path 数据的库验证回填与降级。
"""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _insert_palace_file(connection, file_id: str, owner_id: str, folder_path: str) -> None:
    connection.execute(text(
        "INSERT INTO super_assistant_palace_files "
        "(id, owner_id, filename, folder_path, artifact_id, mime_type, size, sha256, "
        " extracted_chars, status, entity_count, relation_count, created_at, updated_at) "
        "VALUES (:id, :owner_id, 'doc.md', :folder_path, 'art-1', 'text/markdown', 3, '', "
        " 0, 'pending', 0, 0, '2026-09-01 00:00:00.000000', '2026-09-01 00:00:00.000000')",
    ), {"id": file_id, "owner_id": owner_id, "folder_path": folder_path})


def test_upgrade_backfills_folder_rows_from_existing_files(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "palace-folders-migration.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "0092_user_token_version")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        _insert_palace_file(connection, "pf-1", "user-1", "研发/规格")
        _insert_palace_file(connection, "pf-2", "user-1", "研发/规格")
        _insert_palace_file(connection, "pf-3", "user-1", "研发")
        _insert_palace_file(connection, "pf-4", "user-2", "其他")
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    assert "super_assistant_palace_folders" in inspector.get_table_names()
    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT owner_id, path FROM super_assistant_palace_folders ORDER BY owner_id, path"),
        ).fetchall()
    # 中间目录一并回填；(owner, path) 去重
    assert set(rows) == {
        ("user-1", "研发"),
        ("user-1", "研发/规格"),
        ("user-2", "其他"),
    }
    engine.dispose()


def test_downgrade_drops_folders_table(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "palace-folders-downgrade.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0092_user_token_version")

    engine = create_engine(f"sqlite:///{db_path}")
    assert "super_assistant_palace_folders" not in inspect(engine).get_table_names()
    engine.dispose()
