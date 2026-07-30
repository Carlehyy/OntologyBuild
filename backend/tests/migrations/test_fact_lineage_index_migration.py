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
        / "2026_07_28_0054_fact_lineage_indexes.py"
    )
    spec = importlib.util.spec_from_file_location(
        "fact_lineage_index_migration",
        path,
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_fact_lineage_index_migration_is_idempotent_and_reversible(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fact-indexes.db'}")
    metadata = sa.MetaData()
    sa.Table(
        "fo_property_facts",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("ontology_id", sa.String(), nullable=False),
        sa.Column("ontology_release_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("property_name", sa.String(length=200), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
    )
    metadata.create_all(engine)
    migration = _load_migration()

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.upgrade()

    indexes = {
        index["name"]: index["column_names"]
        for index in sa.inspect(engine).get_indexes("fo_property_facts")
    }
    assert indexes == {
        "ix_fo_facts_instance_coord_order": [
            "ontology_id",
            "instance_id",
            "kind",
            "property_name",
            "recorded_at",
            "seq",
            "id",
        ],
        "ix_fo_facts_release_coord_order": [
            "ontology_id",
            "ontology_release_id",
            "kind",
            "instance_id",
            "property_name",
            "recorded_at",
            "seq",
            "id",
        ],
    }

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.downgrade()
        migration.downgrade()

    assert sa.inspect(engine).get_indexes("fo_property_facts") == []
    engine.dispose()
