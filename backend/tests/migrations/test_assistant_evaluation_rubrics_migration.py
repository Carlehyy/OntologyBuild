"""Migration coverage for assistant evaluation rubrics table (0079)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_upgrade_creates_rubrics_table(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "assistant-eval-rubrics-migration.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert "assistant_eval_rubrics" in tables

    columns = {c["name"] for c in inspect(engine).get_columns("assistant_eval_rubrics")}
    assert {"id", "name", "task_description", "rubrics",
            "min_score", "max_score", "judge_model_config_id",
            "judge_model_name", "created_by", "created_at"} <= columns
    engine.dispose()


def test_downgrade_drops_rubrics_table(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "assistant-eval-rubrics-downgrade.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")
    # 显式目标版本（仓库惯例，见 0073/0077/0068 各 downgrade 测试）：
    # 追加新迁移后 "-1" 只会回退最新一个，而非本表所属的 0079。
    command.downgrade(cfg, "0078_assistant_evaluation")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert "assistant_eval_rubrics" not in tables
    engine.dispose()
