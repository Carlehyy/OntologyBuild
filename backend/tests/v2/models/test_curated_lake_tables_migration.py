"""0062_curated_lake_tables 迁移在 SQLite 方言上的契约测试：
建变更集表、curated 数据集 blob → 物理表 + baseline 变更集、对象不可达跳过、
downgrade 声明式放弃物理表。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.data_channel.datasets.lake_store import lake_table_name


def _load_migration_module():
    path = (Path(__file__).parents[3] / "alembic" / "versions" /
            "2026_08_11_0062_curated_lake_tables.py")
    spec = spec_from_file_location("curated_lake_tables_migration", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_legacy_schema(engine) -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "v2_datasets", metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("schema_json", sa.JSON(), nullable=True),
    )
    sa.Table(
        "v2_dataset_versions", metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("rowcount", sa.BigInteger(), nullable=True),
        sa.Column("data_blob", sa.LargeBinary(), nullable=True),
        sa.Column("data_size", sa.BigInteger(), nullable=True),
        sa.Column("storage_uri", sa.Text(), nullable=True),
        sa.Column("checksum", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    return metadata


def _seed(conn):
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    blob = json.dumps([
        {"id": "1", "name": "甲"},
        {"id": "2", "name": "乙"},
    ]).encode("utf-8")
    conn.execute(sa.text(
        "INSERT INTO v2_datasets (id, name, kind, schema_json) VALUES "
        "('ds-curated', '成品', 'curated', :schema), "
        "('ds-legacy-uri', '历史对象', 'curated', NULL), "
        "('ds-structured', '结构化', 'structured', NULL)"), {
        "schema": json.dumps({"primary_key": "id", "columns": ["id", "name"]}),
    })
    conn.execute(sa.text(
        "INSERT INTO v2_dataset_versions "
        "(id, dataset_id, version_no, rowcount, data_blob, data_size,"
        " storage_uri, checksum, created_at) VALUES "
        "('v-curated', 'ds-curated', 1, 2, :blob, :size, NULL, 'ck', :now), "
        "('v-legacy', 'ds-legacy-uri', 1, 5, NULL, NULL,"
        " 'minio://gone/object', 'ck', :now), "
        "('v-structured', 'ds-structured', 1, 2, :blob, :size, NULL, 'ck', :now)"),
        {"blob": blob, "size": len(blob), "now": now})
    conn.commit()


def test_0062_upgrade_backfills_lake_tables_and_downgrade_drops(monkeypatch):
    # storage_uri 历史对象不可达：记录 warning 并跳过，不阻断迁移
    import app.services.storage_service as storage_service
    monkeypatch.setattr(
        storage_service, "get_storage_service",
        lambda: (_ for _ in ()).throw(RuntimeError("minio unreachable")))

    engine = sa.create_engine("sqlite://")
    _build_legacy_schema(engine)
    with engine.connect() as conn:
        _seed(conn)
        migration = _load_migration_module()
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()

        inspector = sa.inspect(conn)
        assert {"v2_dataset_changesets", "v2_dataset_changeset_rows"} <= set(
            inspector.get_table_names())
        assert "ix_v2_dataset_changesets_dataset_id" in {
            idx["name"] for idx in inspector.get_indexes("v2_dataset_changesets")}
        assert "ix_v2_dataset_changeset_rows_changeset_row_pk" in {
            idx["name"] for idx in inspector.get_indexes("v2_dataset_changeset_rows")}

        # curated blob 数据集：物理表 + 全量行 + 主键约束 + baseline 变更集
        lake = lake_table_name("ds-curated")
        assert inspector.has_table(lake)
        assert inspector.get_pk_constraint(lake)["constrained_columns"] == ["id"]
        rows = conn.execute(sa.text(f'SELECT * FROM "{lake}" ORDER BY id')).all()
        assert [tuple(r) for r in rows] == [("1", "甲"), ("2", "乙")]

        baseline = conn.execute(sa.text(
            "SELECT dataset_id, version_id, change_type, added_count,"
            "       updated_count, deleted_count, checksum"
            " FROM v2_dataset_changesets")).mappings().all()
        assert len(baseline) == 1
        assert baseline[0]["dataset_id"] == "ds-curated"
        assert baseline[0]["version_id"] == "v-curated"
        assert baseline[0]["change_type"] == "baseline"
        assert (baseline[0]["added_count"], baseline[0]["updated_count"],
                baseline[0]["deleted_count"]) == (2, 0, 0)
        assert baseline[0]["checksum"]

        # 列映射固化进契约；storage_uri 不可达与结构化数据集不建物理表
        schema = conn.execute(sa.text(
            "SELECT schema_json FROM v2_datasets WHERE id='ds-curated'")).scalar()
        assert json.loads(schema)["lake_columns"] == {"id": "id", "name": "name"}
        assert not inspector.has_table(lake_table_name("ds-legacy-uri"))
        assert not inspector.has_table(lake_table_name("ds-structured"))

        # downgrade：变更集表与全部 lake_ds_* 物理表一并删除
        migration.downgrade()
        inspector = sa.inspect(conn)
        remaining = set(inspector.get_table_names())
        assert "v2_dataset_changesets" not in remaining
        assert "v2_dataset_changeset_rows" not in remaining
        assert not any(name.startswith("lake_ds_") for name in remaining)
        # 历史 blob 未动
        assert conn.execute(sa.text(
            "SELECT data_size FROM v2_dataset_versions"
            " WHERE id='v-curated'")).scalar_one() > 0
