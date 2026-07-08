"""dataset storage hardening: 写锁表 + 版本号/成品名唯一约束（先修复存量重复）

Revision ID: 0008_dataset_storage_hardening
Revises: 0007_pipeline_column_definitions
Create Date: 2026-07-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "0008_dataset_storage_hardening"
down_revision = "0007_pipeline_column_definitions"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa_inspect(op.get_bind()).has_table(name)


def _index_names(table: str) -> set[str]:
    inspector = sa_inspect(op.get_bind())
    names = {ix["name"] for ix in inspector.get_indexes(table)}
    # 唯一约束在部分方言下不体现在 get_indexes 里
    try:
        names |= {uc["name"] for uc in inspector.get_unique_constraints(table)}
    except NotImplementedError:
        pass
    return names


def _dedup_version_numbers(conn) -> None:
    """同一数据集出现重复 version_no（历史无约束+并发写）→ 全数据集按序重排 1..N。

    只重排有重复的数据集；id/latest_version_id/MediaItem 外键都按 id 引用，
    重排 version_no 不影响任何引用关系。
    """
    dup_ds = conn.execute(sa.text(
        "SELECT DISTINCT dataset_id FROM v2_dataset_versions "
        "GROUP BY dataset_id, version_no HAVING COUNT(*) > 1"
    )).fetchall()
    for (ds_id,) in dup_ds:
        rows = conn.execute(sa.text(
            "SELECT id, version_no FROM v2_dataset_versions "
            "WHERE dataset_id = :d ORDER BY version_no, created_at, id"
        ), {"d": ds_id}).fetchall()
        # 两阶段重排：先挪到负数临时区，避免中途撞上尚未挪走的旧号
        for i, (ver_id, _) in enumerate(rows, start=1):
            conn.execute(sa.text(
                "UPDATE v2_dataset_versions SET version_no = :v WHERE id = :i"
            ), {"v": -i, "i": ver_id})
        for i, (ver_id, _) in enumerate(rows, start=1):
            conn.execute(sa.text(
                "UPDATE v2_dataset_versions SET version_no = :v WHERE id = :i"
            ), {"v": i, "i": ver_id})


def _dedup_curated_names(conn) -> None:
    """同名 curated 双胞胎（创建竞争产物）→ 保留最近更新的一个，其余改名。

    改名只影响展示与后续运行的按名匹配（此后统一命中保留者）；
    mapping 等下游都按 id 绑定，不受影响。
    """
    dup_names = conn.execute(sa.text(
        "SELECT name FROM v2_datasets WHERE kind = 'curated' "
        "GROUP BY name HAVING COUNT(*) > 1"
    )).fetchall()
    for (name,) in dup_names:
        rows = conn.execute(sa.text(
            "SELECT id FROM v2_datasets WHERE kind = 'curated' AND name = :n "
            "ORDER BY COALESCE(updated_at, created_at) DESC, id"
        ), {"n": name}).fetchall()
        for (ds_id,) in rows[1:]:
            conn.execute(sa.text(
                "UPDATE v2_datasets SET name = :nn WHERE id = :i"
            ), {"nn": f"{name} [dup-{ds_id[:8]}]", "i": ds_id})


def upgrade() -> None:
    conn = op.get_bind()

    # 1) 数据集写锁表（入湖读改写的跨进程互斥）
    if not _has_table("v2_dataset_write_locks"):
        op.create_table(
            "v2_dataset_write_locks",
            sa.Column("lock_key", sa.String(300), primary_key=True),
            sa.Column("owner", sa.String(64), nullable=False),
            sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        )

    # 2) (dataset_id, version_no) 唯一——先修复存量重复
    if "uq_dataset_versions_dataset_version" not in _index_names("v2_dataset_versions"):
        _dedup_version_numbers(conn)
        op.create_index(
            "uq_dataset_versions_dataset_version",
            "v2_dataset_versions",
            ["dataset_id", "version_no"],
            unique=True,
        )

    # 3) curated 名字唯一（部分索引，仅 sqlite/postgresql 支持）
    if conn.dialect.name in ("sqlite", "postgresql") \
            and "uq_datasets_curated_name" not in _index_names("v2_datasets"):
        _dedup_curated_names(conn)
        op.create_index(
            "uq_datasets_curated_name",
            "v2_datasets",
            ["name"],
            unique=True,
            sqlite_where=sa.text("kind = 'curated'"),
            postgresql_where=sa.text("kind = 'curated'"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if "uq_datasets_curated_name" in _index_names("v2_datasets"):
        op.drop_index("uq_datasets_curated_name", table_name="v2_datasets")
    if "uq_dataset_versions_dataset_version" in _index_names("v2_dataset_versions"):
        op.drop_index("uq_dataset_versions_dataset_version", table_name="v2_dataset_versions")
    if _has_table("v2_dataset_write_locks"):
        op.drop_table("v2_dataset_write_locks")
