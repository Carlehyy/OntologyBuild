"""bind curated reviews to immutable dataset versions

Revision ID: 0011_curated_review_version
Revises: 0010_steward_remove_approval
Create Date: 2026-07-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0011_curated_review_version"
down_revision = "0010_steward_remove_approval"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa_inspect(op.get_bind()).get_columns(table)}


def _index_exists(table: str, name: str) -> bool:
    return name in {i["name"] for i in sa_inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if not _column_exists("v2_curated_reviews", "dataset_version_id"):
        # batch 模式同时兼容 PostgreSQL 与不支持 ALTER ADD CONSTRAINT 的 SQLite。
        with op.batch_alter_table("v2_curated_reviews") as batch:
            batch.add_column(
                sa.Column("dataset_version_id", sa.String(), nullable=True))
            batch.create_foreign_key(
                "fk_curated_review_dataset_version",
                "v2_dataset_versions",
                ["dataset_version_id"], ["id"], ondelete="SET NULL")
    if not _index_exists("v2_curated_reviews", "ix_v2_curated_reviews_dataset_version_id"):
        op.create_index(
            "ix_v2_curated_reviews_dataset_version_id",
            "v2_curated_reviews", ["dataset_version_id"])

    # 历史审批尽可能绑定到“审批发生时最新”的版本；若历史时间信息不足，
    # 再回退到当前最新版本。此后新版本产生时旧审批便不会被继承。
    op.execute(sa.text(
        "UPDATE v2_curated_reviews AS r SET dataset_version_id = ("
        " SELECT v.id FROM v2_dataset_versions AS v"
        " WHERE v.dataset_id = r.curated_dataset_id AND v.created_at <= r.created_at"
        " ORDER BY v.version_no DESC LIMIT 1)"
        " WHERE r.dataset_version_id IS NULL"
    ))
    op.execute(sa.text(
        "UPDATE v2_curated_reviews AS r SET dataset_version_id = ("
        " SELECT v.id FROM v2_dataset_versions AS v"
        " WHERE v.dataset_id = r.curated_dataset_id"
        " ORDER BY v.version_no DESC LIMIT 1)"
        " WHERE r.dataset_version_id IS NULL"
    ))


def downgrade() -> None:
    if _index_exists("v2_curated_reviews", "ix_v2_curated_reviews_dataset_version_id"):
        op.drop_index(
            "ix_v2_curated_reviews_dataset_version_id",
            table_name="v2_curated_reviews")
    if _column_exists("v2_curated_reviews", "dataset_version_id"):
        with op.batch_alter_table("v2_curated_reviews") as batch:
            batch.drop_constraint(
                "fk_curated_review_dataset_version", type_="foreignkey")
            batch.drop_column("dataset_version_id")
