"""Migration coverage for repairing ontology domains missing from Settings."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_upgrade_backfills_missing_ontology_domains(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "domain-registry-backfill.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "0056_ontology_projection_fence")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users "
            "(id,username,email,password_hash,role,is_active,created_at,updated_at) "
            "VALUES ('owner-1','owner','owner@example.com','hash','admin',1,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO domains "
            "(id,name,description,created_by,created_at,updated_at) "
            "VALUES ('domain-1','供应链','保留原描述','owner-1',"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO ontology_projects "
            "(id,name,domain,version,status,created_by,created_at,updated_at) VALUES "
            "('ontology-1','供应链本体','供应链','v0','draft','owner-1',"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),"
            "('ontology-2','业务探索本体','业务探索','v0','draft','owner-1',"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT name,description,created_by FROM domains ORDER BY name"
        )).mappings().all()
    engine.dispose()

    by_name = {row["name"]: row for row in rows}
    assert set(by_name) == {"供应链", "业务探索"}
    assert by_name["供应链"]["description"] == "保留原描述"
    assert by_name["业务探索"]["description"] == "由存量本体领域自动补录"
    assert by_name["业务探索"]["created_by"] == "owner-1"
