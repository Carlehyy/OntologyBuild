"""assistant evaluation flywheel M1 (基准集 / 噪声校准 / 审计时间线)

数据飞轮 M1 地基：从评估坏例沉淀可复评的基准集（train/heldout 稳定
哈希切分）、judge 分数方差校准（噪声地板）、全流程审计时间线，以及
评估明细的结构化归因列（attribution）。全部为旁路新增，不改任何
助手表。
Revision ID: 0085_assistant_eval_flywheel_m1
Revises: 0084_mcp_display_fields
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0085_assistant_eval_flywheel_m1"
down_revision = "0084_mcp_display_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())

    if "assistant_eval_benchmark_sets" not in tables:
        op.create_table(
            "assistant_eval_benchmark_sets",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("assistant_key", sa.String(length=50), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("source_task_id", sa.String(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "assistant_eval_benchmark_items" not in tables:
        op.create_table(
            "assistant_eval_benchmark_items",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("set_id", sa.String(), nullable=False),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("conversation_title", sa.String(length=300), nullable=False),
            sa.Column("split", sa.String(length=10), nullable=False),
            sa.Column("origin", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["set_id"], ["assistant_eval_benchmark_sets.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("set_id", "conversation_id",
                                name="uq_ae_bench_set_conversation"),
        )
        op.create_index("ix_ae_bench_items_set", "assistant_eval_benchmark_items",
                        ["set_id"])

    if "assistant_eval_calibrations" not in tables:
        op.create_table(
            "assistant_eval_calibrations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("assistant_key", sa.String(length=50), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("judge_model_config_id", sa.String(), nullable=True),
            sa.Column("judge_model_name", sa.String(length=200), nullable=False),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "assistant_eval_timeline_events" not in tables:
        op.create_table(
            "assistant_eval_timeline_events",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("assistant_key", sa.String(length=50), nullable=True),
            sa.Column("event_type", sa.String(length=50), nullable=False),
            sa.Column("actor", sa.String(length=20), nullable=False),
            sa.Column("actor_user_id", sa.String(), nullable=True),
            sa.Column("ref_type", sa.String(length=30), nullable=True),
            sa.Column("ref_id", sa.String(), nullable=True),
            sa.Column("detail", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ae_timeline_assistant_created",
                        "assistant_eval_timeline_events",
                        ["assistant_key", "created_at"])
        op.create_index("ix_ae_timeline_ref", "assistant_eval_timeline_events",
                        ["ref_type", "ref_id"])

    item_columns = {c["name"] for c in sa_inspect(bind)
                    .get_columns("assistant_eval_items")}
    if "attribution" not in item_columns:
        op.add_column("assistant_eval_items",
                      sa.Column("attribution", sa.JSON(), nullable=False,
                                server_default=sa.text("'{}'")))


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())
    item_columns = {c["name"] for c in sa_inspect(bind)
                    .get_columns("assistant_eval_items")}
    if "attribution" in item_columns:
        op.drop_column("assistant_eval_items", "attribution")
    if "assistant_eval_timeline_events" in tables:
        op.drop_index("ix_ae_timeline_ref", table_name="assistant_eval_timeline_events")
        op.drop_index("ix_ae_timeline_assistant_created",
                      table_name="assistant_eval_timeline_events")
        op.drop_table("assistant_eval_timeline_events")
    if "assistant_eval_calibrations" in tables:
        op.drop_table("assistant_eval_calibrations")
    if "assistant_eval_benchmark_items" in tables:
        op.drop_index("ix_ae_bench_items_set",
                      table_name="assistant_eval_benchmark_items")
        op.drop_table("assistant_eval_benchmark_items")
    if "assistant_eval_benchmark_sets" in tables:
        op.drop_table("assistant_eval_benchmark_sets")
