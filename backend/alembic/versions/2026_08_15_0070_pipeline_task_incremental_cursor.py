"""pipeline_tasks 增量游标：cursor_column + last_cursor_value

动机：数据任务池声明源端增量游标（Foundry 式水位）后，流水线每次运行只拉取
游标之后的新数据，平台在运行成功时把当次产出的游标词法最大值回写为下次起点。
两列均空串默认（= 每次全量），存量任务行为不变，无需回填。

幂等性：inspector 守卫（表/列存在性），可安全重跑；无索引、无回填。
0003 迁移会对当前 Base.metadata 做 create_all，守卫必须幂等。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0070_pipeline_task_incremental_cursor"
down_revision = "0069_api_perf_monitoring"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("v2_pipeline_tasks"):
        return
    if not _has_column(inspector, "v2_pipeline_tasks", "cursor_column"):
        op.add_column(
            "v2_pipeline_tasks",
            sa.Column("cursor_column", sa.String(200), server_default="", nullable=True),
        )
    if not _has_column(inspector, "v2_pipeline_tasks", "last_cursor_value"):
        op.add_column(
            "v2_pipeline_tasks",
            sa.Column("last_cursor_value", sa.Text(), server_default="", nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("v2_pipeline_tasks"):
        return
    if _has_column(inspector, "v2_pipeline_tasks", "last_cursor_value"):
        op.drop_column("v2_pipeline_tasks", "last_cursor_value")
    if _has_column(inspector, "v2_pipeline_tasks", "cursor_column"):
        op.drop_column("v2_pipeline_tasks", "cursor_column")
