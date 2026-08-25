"""assistant evaluation rubrics (助手评估 · 自定义评分标准)

系统设置 → 助手评估：新增评分标准表。judge 模型按任务描述生成
OpenJudge 风格 rubric（编号列表文本），任务创建时把 rubric 快照进
task.params（JSON），删除标准记录不影响历史报告。judge 模型复用
model_configs 的选择与解密通道，本域不存储任何密钥。

Revision ID: 0079_assistant_evaluation_rubrics
Revises: 0078_assistant_evaluation
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0079_assistant_evaluation_rubrics"
down_revision = "0078_assistant_evaluation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())

    if "assistant_eval_rubrics" not in tables:
        op.create_table(
            "assistant_eval_rubrics",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("task_description", sa.Text(), nullable=False),
            sa.Column("rubrics", sa.Text(), nullable=False),
            sa.Column("min_score", sa.Float(), nullable=False),
            sa.Column("max_score", sa.Float(), nullable=False),
            sa.Column("judge_model_config_id", sa.String(), nullable=True),
            sa.Column("judge_model_name", sa.String(length=200), nullable=False),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())
    if "assistant_eval_rubrics" in tables:
        op.drop_table("assistant_eval_rubrics")
