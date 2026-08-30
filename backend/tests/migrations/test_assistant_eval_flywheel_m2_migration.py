"""Migration coverage for assistant evaluation flywheel M2 tables (0086)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_upgrade_creates_m2_tables(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "assistant-eval-flywheel-m2-migration.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert "assistant_eval_proposals" in tables
    assert "assistant_eval_experiments" in tables
    assert "assistant_eval_experiment_items" in tables

    conv_columns = {c["name"] for c in sa_inspect_columns(engine, "fo_agent_conversations")}
    assert "is_sandbox" in conv_columns
    bench_columns = {c["name"]
                     for c in sa_inspect_columns(engine, "assistant_eval_benchmark_sets")}
    assert "ontology_id" in bench_columns

    proposal_indexes = {ix["name"] for ix in inspect(engine)
                        .get_indexes("assistant_eval_proposals")}
    assert "ix_ae_proposals_ontology_created" in proposal_indexes
    experiment_indexes = {ix["name"] for ix in inspect(engine)
                          .get_indexes("assistant_eval_experiments")}
    assert "ix_ae_experiments_ontology_created" in experiment_indexes
    assert "ix_ae_experiments_proposal" in experiment_indexes
    engine.dispose()


def sa_inspect_columns(engine, table: str):
    return inspect(engine).get_columns(table)


def test_downgrade_drops_m2_tables(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "assistant-eval-flywheel-m2-downgrade.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0085_assistant_eval_flywheel_m1")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert "assistant_eval_proposals" not in tables
    assert "assistant_eval_experiments" not in tables
    assert "assistant_eval_experiment_items" not in tables
    conv_columns = {c["name"] for c in sa_inspect_columns(engine, "fo_agent_conversations")}
    assert "is_sandbox" not in conv_columns
    bench_columns = {c["name"]
                     for c in sa_inspect_columns(engine, "assistant_eval_benchmark_sets")}
    assert "ontology_id" not in bench_columns
    engine.dispose()
