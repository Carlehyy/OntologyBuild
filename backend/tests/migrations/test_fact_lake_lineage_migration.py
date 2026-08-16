"""0072 血缘迁移（source_dataset_version_id + producer_run_id）幂等与可逆。"""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration():
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "2026_08_16_0072_fact_lake_lineage.py"
    )
    spec = importlib.util.spec_from_file_location(
        "fact_lake_lineage_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _tables():
    metadata = sa.MetaData()
    sa.Table(
        "fo_property_facts", metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("ontology_id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("property_name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
    )
    sa.Table(
        "v2_dataset_versions", metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
    )
    sa.Table(
        "v2_pipeline_runs", metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pipeline_id", sa.String(), nullable=False),
    )
    return metadata


def test_fact_lake_lineage_migration_idempotent_and_reversible(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'lineage.db'}")
    _tables().create_all(engine)
    migration = _load_migration()

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.upgrade()  # 幂等

    inspector = sa.inspect(engine)
    fact_cols = {c["name"] for c in inspector.get_columns("fo_property_facts")}
    ver_cols = {c["name"] for c in inspector.get_columns("v2_dataset_versions")}
    assert "source_dataset_version_id" in fact_cols
    assert "producer_run_id" in ver_cols
    fact_indexes = {i["name"] for i in inspector.get_indexes("fo_property_facts")}
    ver_indexes = {i["name"] for i in inspector.get_indexes("v2_dataset_versions")}
    assert "ix_fo_property_facts_source_dataset_version_id" in fact_indexes
    assert "ix_v2_dataset_versions_producer_run_id" in ver_indexes

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.downgrade()

    inspector = sa.inspect(engine)
    fact_cols = {c["name"] for c in inspector.get_columns("fo_property_facts")}
    ver_cols = {c["name"] for c in inspector.get_columns("v2_dataset_versions")}
    assert "source_dataset_version_id" not in fact_cols
    assert "producer_run_id" not in ver_cols
