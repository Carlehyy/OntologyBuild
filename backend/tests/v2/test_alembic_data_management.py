"""Alembic 必须能从空库独立建立数据管理主链路，不能依赖应用启动补表。"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_fresh_upgrade_builds_data_management_contract(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "fresh-data-management.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    required = {
        "v2_n8n_pipelines",
        "v2_steward_conversations",
        "v2_steward_messages",
        "v2_pipeline_tasks",
        "v2_pipeline_runs",
        "v2_datasets",
        "v2_dataset_versions",
        "v2_curated_reviews",
        "v2_ontology_mappings",
    }
    assert required <= set(inspector.get_table_names())
    assert "task_id" in {c["name"] for c in inspector.get_columns("v2_pipeline_runs")}
    assert "dataset_version_id" in {
        c["name"] for c in inspector.get_columns("v2_curated_reviews")}

    def fk_target(table: str, column: str) -> str:
        match = next(
            fk for fk in inspector.get_foreign_keys(table)
            if fk["constrained_columns"] == [column]
        )
        return match["referred_table"]

    assert fk_target("v2_pipeline_tasks", "pipeline_id") == "v2_pipelines"
    assert fk_target("v2_pipeline_runs", "task_id") == "v2_pipeline_tasks"
    assert fk_target("v2_curated_reviews", "curated_dataset_id") == "v2_datasets"
    assert fk_target("v2_curated_reviews", "dataset_version_id") == "v2_dataset_versions"
    assert fk_target("v2_ontology_mappings", "curated_dataset_id") == "v2_datasets"
    engine.dispose()
