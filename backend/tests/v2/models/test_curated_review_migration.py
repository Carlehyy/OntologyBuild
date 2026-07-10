"""CuratedReview 版本绑定迁移在 SQLite/PostgreSQL 兼容路径上的契约测试。"""
from __future__ import annotations

from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration_module():
    path = (Path(__file__).parents[3] / "alembic" / "versions" /
            "2026_07_10_0011_curated_review_version.py")
    spec = spec_from_file_location("curated_review_version_migration", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_review_version_migration_backfills_and_downgrades_on_sqlite():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "v2_dataset_versions", metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "v2_curated_reviews", metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("curated_dataset_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)

    with engine.connect() as conn:
        conn.execute(sa.text(
            "INSERT INTO v2_dataset_versions "
            "(id, dataset_id, version_no, created_at) VALUES "
            "('v1', 'ds-1', 1, :v1), ('v2', 'ds-1', 2, :v2)"), {
                "v1": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "v2": datetime(2026, 1, 3, tzinfo=timezone.utc),
            })
        conn.execute(sa.text(
            "INSERT INTO v2_curated_reviews "
            "(id, curated_dataset_id, status, created_at) "
            "VALUES ('r1', 'ds-1', 'approved', :reviewed)"), {
                "reviewed": datetime(2026, 1, 2, tzinfo=timezone.utc),
            })
        conn.commit()

        migration = _load_migration_module()
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()

        inspector = sa.inspect(conn)
        assert "dataset_version_id" in {
            col["name"] for col in inspector.get_columns("v2_curated_reviews")}
        assert "ix_v2_curated_reviews_dataset_version_id" in {
            idx["name"] for idx in inspector.get_indexes("v2_curated_reviews")}
        bound = conn.execute(sa.text(
            "SELECT dataset_version_id FROM v2_curated_reviews WHERE id='r1'"
        )).scalar_one()
        assert bound == "v1"

        migration.downgrade()
        assert "dataset_version_id" not in {
            col["name"] for col in sa.inspect(conn).get_columns("v2_curated_reviews")}
