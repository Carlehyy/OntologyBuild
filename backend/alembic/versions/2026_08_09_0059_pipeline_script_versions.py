"""python script pipeline save-history table

每次「保存」把通过执行与格式校验的脚本冻结为一版（草稿期编辑历史），
供脚本编辑页查看与恢复。与发布封版快照 v2_pipeline_versions 互补，不
改变既有发布契约。

Revision ID: 0059_pipeline_script_versions
Revises: 0058_drop_minio_config
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0059_pipeline_script_versions"
down_revision = "0058_drop_minio_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())
    if "v2_pipeline_script_versions" in tables:
        return
    op.create_table(
        "v2_pipeline_script_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("pipeline_id", sa.String(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("script", sa.Text(), nullable=False),
        sa.Column("output_columns", sa.JSON(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["pipeline_id"], ["v2_pipelines.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_pipeline_script_versions_pipeline_version",
        "v2_pipeline_script_versions",
        ["pipeline_id", "version_no"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())
    if "v2_pipeline_script_versions" not in tables:
        return
    op.drop_index(
        "uq_pipeline_script_versions_pipeline_version",
        table_name="v2_pipeline_script_versions",
    )
    op.drop_table("v2_pipeline_script_versions")
