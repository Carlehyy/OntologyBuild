"""pipeline 生命周期收敛 + 版本快照契约列

1. v2_pipeline_versions 增加 column_definitions——发布那一刻的字段契约快照，
   契约是封版核心工件，版本必须能回溯。
2. 历史 status 归一：status 此前混装了生命周期（draft/published）与运行态
   （running/failed/editing），运行失败会把已发布流水线冲成 failed，导致
   任务池「必须已发布」校验拦掉全部调度、封版保护失效。现在运行态只落
   PipelineRun，存量脏值按「有无已发布版本快照」归一回 published/draft。

Revision ID: 0008_pipeline_lifecycle_contract
Revises: 0007_pipeline_column_definitions
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_pipeline_lifecycle_contract"
down_revision = "0007_pipeline_column_definitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "v2_pipeline_versions",
        sa.Column("column_definitions", sa.JSON(), nullable=True),
    )
    # 有已发布版本快照的 → published；其余脏值 → draft
    op.execute(
        "UPDATE v2_pipelines SET status='published' "
        "WHERE status IN ('running','failed','editing') "
        "AND id IN (SELECT pipeline_id FROM v2_pipeline_versions WHERE status='published')"
    )
    op.execute(
        "UPDATE v2_pipelines SET status='draft' "
        "WHERE status IN ('running','failed','editing')"
    )
    # 维持「只有已发布才能启用」不变量：未发布却已启用的存量一律停用
    # （表达式构造保证 SQLite/Postgres 的布尔字面量都正确）
    pipelines = sa.table(
        "v2_pipelines",
        sa.column("status", sa.String),
        sa.column("enabled", sa.Boolean),
    )
    op.execute(
        pipelines.update()
        .where(sa.and_(pipelines.c.status != "published",
                       pipelines.c.enabled.is_(True)))
        .values(enabled=False)
    )


def downgrade() -> None:
    op.drop_column("v2_pipeline_versions", "column_definitions")
