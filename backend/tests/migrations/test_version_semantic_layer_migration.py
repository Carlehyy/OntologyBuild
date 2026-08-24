import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

NEW_COLUMNS = {
    "ontology_versions": {"snapshot_semantic"},
    "bx_sessions": {"ontology_id", "ontology_version_id"},
    "bx_drafts": {"applied_version_id"},
}
SESSION_INDEXES = {
    "ix_bx_sessions_ontology_id",
    "ix_bx_sessions_ontology_version_id",
}


def _load_migration():
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "2026_08_23_0074_version_semantic_layer.py"
    )
    spec = importlib.util.spec_from_file_location(
        "version_semantic_layer_migration",
        path,
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _create_pre_0074_tables(engine) -> None:
    """手工建 0073 形态的三张表（不含语义层新列）。"""
    metadata = sa.MetaData()
    sa.Table(
        "ontology_versions",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("canvas_layout", sa.JSON(), nullable=True),
    )
    sa.Table(
        "bx_sessions",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=True),
    )
    sa.Table(
        "bx_drafts",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("applied_ontology_id", sa.String(), nullable=True),
    )
    metadata.create_all(engine)


def _columns(engine, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns(table)}


def _indexes(engine, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(engine).get_indexes(table)}


def test_upgrade_adds_semantic_layer_columns_and_is_idempotent(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'semantic-layer.db'}")
    _create_pre_0074_tables(engine)
    migration = _load_migration()

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.upgrade()

    for table, columns in NEW_COLUMNS.items():
        assert columns <= _columns(engine, table)
    assert SESSION_INDEXES <= _indexes(engine, "bx_sessions")
    engine.dispose()


def test_downgrade_removes_semantic_layer_and_is_idempotent(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'semantic-layer-down.db'}")
    _create_pre_0074_tables(engine)
    migration = _load_migration()

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.downgrade()
        migration.downgrade()

    for table, columns in NEW_COLUMNS.items():
        assert columns.isdisjoint(_columns(engine, table))
    assert SESSION_INDEXES.isdisjoint(_indexes(engine, "bx_sessions"))
    # 既有列不受影响
    assert "canvas_layout" in _columns(engine, "ontology_versions")
    assert "applied_ontology_id" in _columns(engine, "bx_drafts")
    engine.dispose()
