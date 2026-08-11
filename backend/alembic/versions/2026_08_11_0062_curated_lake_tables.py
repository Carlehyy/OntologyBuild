"""curated lake tables: physical row tables + changesets

Revision ID: 0062_curated_lake_tables
Revises: 0061_retire_canvas_pipelines
Create Date: 2026-08-11

成品数据集（v2_datasets.kind='curated'）的行数据由整份 Parquet blob
（v2_dataset_versions.data_blob）迁移为「每数据集一张物理表 lake_ds_* +
版本级变更集」：

- 新建 v2_dataset_changesets / v2_dataset_changeset_rows；
- 对每个 curated 数据集取最新含载荷版本（data_blob 优先，storage_uri
  次之），解析成行后建物理表、全量灌入，并写 baseline 变更集（仅计数、
  不逐行，change_type='baseline'）；
- 列名净化/去重映射固化到 Dataset.schema_json['lake_columns']，契约主键
  列建复合 PRIMARY KEY（与 lake_store 同一套命名/净化规则）；
- 历史版本 blob 一律保留不动（读取回滚兜底）；storage_uri 历史对象在迁移
  时不可达或载荷解析失败时记录 warning 并跳过该数据集，不阻断迁移——该
  数据集继续走遗留 blob 读取路径，首次入湖运行时再经
  DatasetService.bootstrap_lake_base 懒引导建表。

downgrade 删除两张变更集表与全部 lake_ds_* 物理表：物理表数据不可恢复，
但历史版本 blob 未动，回滚代码即可恢复既有 blob 读取路径。
"""

from datetime import datetime, timezone
import logging
import uuid

from alembic import op
import sqlalchemy as sa

from app.data_channel.datasets.lake_gate import split_pk
from app.data_channel.datasets.lake_store import (
    LAKE_TABLE_PREFIX,
    build_lake_column_mapping,
    compute_changeset_checksum,
    lake_columns_from_rows,
    lake_table_definition,
    lake_table_name,
    normalize_lake_rows,
)
from app.data_channel.datasets.service import _parse_stored_rows

logger = logging.getLogger("alembic.runtime.migration")


revision = "0062_curated_lake_tables"
down_revision = "0061_retire_canvas_pipelines"
branch_labels = None
depends_on = None

_INSERT_BATCH = 1000


def _create_changeset_tables(bind) -> None:
    # 迁移 0003 会对当前 Base.metadata 做 create_all：全新库在到达本迁移前
    # 可能已按当前模型建好这两张表（含索引），此处按表守卫跳过重复创建；
    # 存量升级库没有这些表，正常创建。数据回填不受跳过影响。
    if not sa.inspect(bind).has_table("v2_dataset_changesets"):
        op.create_table(
            "v2_dataset_changesets",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("dataset_id", sa.String(), nullable=False),
            sa.Column("version_id", sa.String(), nullable=False),
            sa.Column("change_type", sa.String(length=20), nullable=False),
            sa.Column("added_count", sa.BigInteger(), nullable=False),
            sa.Column("updated_count", sa.BigInteger(), nullable=False),
            sa.Column("deleted_count", sa.BigInteger(), nullable=False),
            sa.Column("checksum", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["dataset_id"], ["v2_datasets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["version_id"], ["v2_dataset_versions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_v2_dataset_changesets_dataset_id",
            "v2_dataset_changesets", ["dataset_id"])
        op.create_index(
            "uq_v2_dataset_changesets_version_id",
            "v2_dataset_changesets", ["version_id"], unique=True)
    if not sa.inspect(bind).has_table("v2_dataset_changeset_rows"):
        op.create_table(
            "v2_dataset_changeset_rows",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("changeset_id", sa.String(), nullable=False),
            sa.Column("row_pk", sa.String(length=1000), nullable=False),
            sa.Column("change_type", sa.String(length=10), nullable=False),
            sa.Column("old_row", sa.JSON(), nullable=True),
            sa.Column("new_row", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(
                ["changeset_id"], ["v2_dataset_changesets.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_v2_dataset_changeset_rows_changeset_row_pk",
            "v2_dataset_changeset_rows", ["changeset_id", "row_pk"])


def _migrate_curated_dataset(bind, datasets, versions, changesets,
                             dataset_id, dataset_name, schema_json) -> None:
    """单个 curated 数据集：最新含载荷版本 → 物理表 + baseline 变更集。"""
    version = bind.execute(
        sa.select(
            versions.c.id, versions.c.version_no,
            versions.c.data_blob, versions.c.storage_uri,
        )
        .where(versions.c.dataset_id == dataset_id)
        .where(sa.or_(versions.c.data_blob.is_not(None),
                      versions.c.storage_uri.is_not(None)))
        .order_by(versions.c.version_no.desc())
        .limit(1),
    ).mappings().first()
    if version is None:
        return  # 尚无载荷版本：首次入湖时再建表

    raw = version["data_blob"]
    if raw is None:
        try:
            from app.services.storage_service import get_storage_service
            raw = get_storage_service().get_object(version["storage_uri"])
        except Exception as exc:  # noqa: BLE001 — 对象不可达不阻断整体迁移
            logger.warning(
                "0062: 数据集 %s（%s）历史存储对象不可达（%s），跳过物理表迁移",
                dataset_id, dataset_name, exc)
            return
    try:
        rows = _parse_stored_rows(bytes(raw), limit=None)
    except Exception as exc:  # noqa: BLE001 — 单个坏版本不阻断整体迁移
        logger.warning(
            "0062: 数据集 %s（%s）版本 v%s 载荷解析失败（%s），跳过物理表迁移",
            dataset_id, dataset_name, version["version_no"], exc)
        return

    schema = dict(schema_json or {})
    columns = lake_columns_from_rows(rows) or [
        str(c) for c in schema.get("columns") or []]
    if not columns:
        logger.warning(
            "0062: 数据集 %s（%s）无可用列契约，跳过物理表迁移",
            dataset_id, dataset_name)
        return
    mapping = build_lake_column_mapping(columns)
    declared_pk = split_pk(schema.get("primary_key"))
    pk_cols = [c for c in declared_pk if c in mapping]
    if len(pk_cols) != len(declared_pk):
        logger.warning(
            "0062: 数据集 %s（%s）契约主键列 %s 不在数据列中，物理表不建主键约束",
            dataset_id, dataset_name,
            [c for c in declared_pk if c not in mapping])

    table_name = lake_table_name(dataset_id)
    if sa.inspect(bind).has_table(table_name):
        logger.warning(
            "0062: 数据集 %s（%s）的物理表 %s 已存在，跳过灌入",
            dataset_id, dataset_name, table_name)
        return
    table = lake_table_definition(table_name, mapping, pk_cols)
    table.create(bind=bind)
    normalized = normalize_lake_rows(rows, mapping, pk_cols)
    if pk_cols:
        # 历史快照理论上已过主键校验；防御性末现去重，避免脏历史撞 PK 约束
        seen: dict[tuple, int] = {}
        for index, row in enumerate(normalized):
            seen[tuple(row[mapping[c]] for c in pk_cols)] = index
        normalized = [normalized[i] for i in sorted(seen.values())]
    for start in range(0, len(normalized), _INSERT_BATCH):
        bind.execute(table.insert(), normalized[start:start + _INSERT_BATCH])

    checksum = compute_changeset_checksum(
        dataset_id, "baseline", len(normalized), 0, 0, [])
    bind.execute(changesets.insert().values(
        id=str(uuid.uuid4()),
        dataset_id=dataset_id,
        version_id=version["id"],
        change_type="baseline",
        added_count=len(normalized),
        updated_count=0,
        deleted_count=0,
        checksum=checksum,
        created_at=datetime.now(timezone.utc),
    ))
    schema["lake_columns"] = mapping
    bind.execute(
        datasets.update()
        .where(datasets.c.id == dataset_id)
        .values(schema_json=schema))


def upgrade() -> None:
    bind = op.get_bind()
    _create_changeset_tables(bind)

    datasets = sa.table(
        "v2_datasets",
        sa.column("id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("schema_json", sa.JSON()),
    )
    versions = sa.table(
        "v2_dataset_versions",
        sa.column("id", sa.String()),
        sa.column("dataset_id", sa.String()),
        sa.column("version_no", sa.Integer()),
        sa.column("data_blob", sa.LargeBinary()),
        sa.column("storage_uri", sa.Text()),
    )
    changesets = sa.table(
        "v2_dataset_changesets",
        sa.column("id", sa.String()),
        sa.column("dataset_id", sa.String()),
        sa.column("version_id", sa.String()),
        sa.column("change_type", sa.String()),
        sa.column("added_count", sa.BigInteger()),
        sa.column("updated_count", sa.BigInteger()),
        sa.column("deleted_count", sa.BigInteger()),
        sa.column("checksum", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    curated = bind.execute(
        sa.select(datasets.c.id, datasets.c.name, datasets.c.schema_json)
        .where(datasets.c.kind == "curated"),
    ).mappings()
    for row in curated:
        _migrate_curated_dataset(
            bind, datasets, versions, changesets,
            row["id"], row["name"], row["schema_json"])


def downgrade() -> None:
    # 物理表数据不可恢复；历史版本 blob 未动，回滚代码即可恢复遗留读取路径。
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "v2_dataset_changeset_rows" in existing:
        op.drop_table("v2_dataset_changeset_rows")
    if "v2_dataset_changesets" in existing:
        op.drop_table("v2_dataset_changesets")
    for name in sorted(n for n in existing if n.startswith(LAKE_TABLE_PREFIX)):
        op.drop_table(name)
