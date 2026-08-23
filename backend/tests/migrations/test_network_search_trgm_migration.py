"""本体网络搜索 trgm 索引迁移契约。"""

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
        / "2026_08_24_0076_network_search_trgm.py"
    )
    spec = importlib.util.spec_from_file_location(
        "network_search_trgm_migration",
        path,
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_network_search_trgm_migration_metadata_and_sqlite_noop(tmp_path):
    migration = _load_migration()
    assert migration.revision == "0076_network_search_trgm"
    assert migration.down_revision == "0075_scenes_assistant_conversations"
    # pg_trgm 为 PG 专有：非 PG 方言升级/降级必须是无操作且幂等，
    # 保证 sqlite 上的 alembic upgrade head 不被破坏。
    assert {name for name, _ in migration.INDEXES} == {
        "ix_fo_object_instances_id_trgm",
        "ix_fo_object_instances_external_id_trgm",
        "ix_fo_object_instances_properties_trgm",
        "ix_fo_object_instances_computed_trgm",
    }

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'trgm-noop.db'}")
    metadata = sa.MetaData()
    sa.Table(
        "fo_object_instances",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("external_id", sa.String(200), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("computed", sa.JSON(), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.upgrade()
        migration.downgrade()
        migration.downgrade()

    assert sa.inspect(engine).get_indexes("fo_object_instances") == []
    engine.dispose()
