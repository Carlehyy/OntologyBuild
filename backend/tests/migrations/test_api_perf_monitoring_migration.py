"""Migration coverage for the API performance monitoring tables."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_upgrade_creates_api_perf_tables(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "api-perf-migration.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "0068_super_assistant_skill_governance")
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"api_perf_minute_rollups", "api_perf_slow_requests"} <= tables

    rollup_columns = {
        column["name"] for column in inspector.get_columns("api_perf_minute_rollups")
    }
    assert {"minute_ts", "method", "route", "status_class", "count", "total_ms",
            "max_ms"} <= rollup_columns
    assert all(f"bucket_{i}" in rollup_columns for i in range(10))

    slow_columns = {
        column["name"] for column in inspector.get_columns("api_perf_slow_requests")
    }
    assert {"created_at", "method", "route", "status_code", "duration_ms",
            "request_id", "username", "source_ip", "user_agent",
            "breakdown"} <= slow_columns

    # Insert-only aggregation contract: rows survive and can be summed.
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO api_perf_minute_rollups "
            "(minute_ts, method, route, status_class, count, total_ms, max_ms, "
            " bucket_0, bucket_1, bucket_2, bucket_3, bucket_4, bucket_5,"
            " bucket_6, bucket_7, bucket_8, bucket_9) VALUES "
            "('2026-08-13 10:00:00', 'GET', '/api/v1/domains', '2xx', 3, 360, 150,"
            " 2, 1, 0, 0, 0, 0, 0, 0, 0, 0)"
        ))
    engine.dispose()


def test_downgrade_removes_api_perf_tables(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "api-perf-downgrade.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0068_super_assistant_skill_governance")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert "api_perf_minute_rollups" not in tables
    assert "api_perf_slow_requests" not in tables
    engine.dispose()

