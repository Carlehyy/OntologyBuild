"""assistant evaluation tables (助手评估 / OpenJudge 接入)

系统设置 → 助手评估：对平台各助手的落库会话做旁路质量评估。
新增两张表——评估任务与单会话评估明细。任务状态机
queued → running → success | error；judge 模型复用 model_configs
的选择与解密通道，本域不存储任何密钥。纯旁路只读，不改任何助手表。

Revision ID: 0078_assistant_evaluation
Revises: 0077_merge_semantic_scenes_heads
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0078_assistant_evaluation"
down_revision = "0077_merge_semantic_scenes_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())

    if "assistant_eval_tasks" not in tables:
        op.create_table(
            "assistant_eval_tasks",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("assistant_key", sa.String(length=50), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("judge_model_config_id", sa.String(), nullable=True),
            sa.Column("judge_model_name", sa.String(length=200), nullable=False),
            sa.Column("conversation_count", sa.Integer(), nullable=False),
            sa.Column("completed_conversations", sa.Integer(), nullable=False),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_ae_tasks_assistant_created",
            "assistant_eval_tasks",
            ["assistant_key", "created_at"],
        )

    if "assistant_eval_items" not in tables:
        op.create_table(
            "assistant_eval_items",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("conversation_title", sa.String(length=300), nullable=False),
            sa.Column("overall_score", sa.Float(), nullable=True),
            sa.Column("scores", sa.JSON(), nullable=False),
            sa.Column("reasons", sa.JSON(), nullable=False),
            sa.Column("flags", sa.JSON(), nullable=False),
            sa.Column("root_cause", sa.String(length=200), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["task_id"], ["assistant_eval_tasks.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ae_items_task", "assistant_eval_items", ["task_id"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())
    if "assistant_eval_items" in tables:
        op.drop_table("assistant_eval_items")
    if "assistant_eval_tasks" in tables:
        op.drop_index("ix_ae_tasks_assistant_created", table_name="assistant_eval_tasks")
        op.drop_table("assistant_eval_tasks")
