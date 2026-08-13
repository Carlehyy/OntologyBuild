"""super assistant self-evolution: memories / reflection / profiles

超级助手自我进化首期（对标 small-rust-hermes）：

- ``super_assistant_memories``：跨会话记忆，zone + pinned + supersedes
  冲突链，行内效果计数（match/reference）配合时间衰减降权；
- ``super_assistant_reflection_runs``：micro/full/focused 三种反思执行
  记录，同时充当 NATS 消费侧的幂等锚点；
- ``super_assistant_reflection_candidates``：反思产出的 memory/skill/
  conflict 待审批候选，全部经人工（或低风险记忆 auto-accept）落库；
- ``super_assistant_memory_profiles``：每用户 palace 索引、LLM 编译画像
  与 auto-accept 开关；
- ``super_assistant_conversations`` 增加上下文压缩所需的
  ``summary`` / ``summary_message_count`` 两列。

Revision ID: 0066_super_assistant_evolution
Revises: 0065_world_model
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0066_super_assistant_evolution"
down_revision = "0065_world_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "super_assistant_memories" not in tables:
        op.create_table(
            "super_assistant_memories",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("zone", sa.String(length=50), nullable=False, server_default="general"),
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("confidence", sa.String(length=10), nullable=False, server_default="medium"),
            sa.Column("source", sa.String(length=20), nullable=False, server_default="reflection"),
            sa.Column("tags", sa.JSON(), nullable=True),
            sa.Column("supersedes", sa.JSON(), nullable=True),
            sa.Column("superseded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("match_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reference_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_accessed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_sa_memories_owner_zone", "super_assistant_memories", ["owner_id", "zone"])
        op.create_index("ix_sa_memories_owner_updated", "super_assistant_memories", ["owner_id", "updated_at"])
        op.create_index("ix_super_assistant_memories_owner_id", "super_assistant_memories", ["owner_id"])

    if "super_assistant_reflection_runs" not in tables:
        op.create_table(
            "super_assistant_reflection_runs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("message_id", sa.String(), nullable=True),
            sa.Column("kind", sa.String(length=10), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["conversation_id"], ["super_assistant_conversations.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["message_id"], ["super_assistant_messages.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_sa_reflect_runs_conversation_created",
            "super_assistant_reflection_runs",
            ["conversation_id", "created_at"],
        )
        op.create_index(
            "ix_sa_reflect_runs_owner_created",
            "super_assistant_reflection_runs",
            ["owner_id", "created_at"],
        )
        op.create_index(
            "ix_super_assistant_reflection_runs_owner_id",
            "super_assistant_reflection_runs",
            ["owner_id"],
        )
        op.create_index(
            "ix_super_assistant_reflection_runs_conversation_id",
            "super_assistant_reflection_runs",
            ["conversation_id"],
        )
        op.create_index(
            "ix_super_assistant_reflection_runs_message_id",
            "super_assistant_reflection_runs",
            ["message_id"],
        )

    if "super_assistant_reflection_candidates" not in tables:
        op.create_table(
            "super_assistant_reflection_candidates",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("kind", sa.String(length=10), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("confidence", sa.String(length=10), nullable=False, server_default="medium"),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("decision", sa.String(length=30), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["run_id"], ["super_assistant_reflection_runs.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["conversation_id"], ["super_assistant_conversations.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_sa_reflect_candidates_owner_status",
            "super_assistant_reflection_candidates",
            ["owner_id", "status"],
        )
        op.create_index(
            "ix_super_assistant_reflection_candidates_run_id",
            "super_assistant_reflection_candidates",
            ["run_id"],
        )
        op.create_index(
            "ix_super_assistant_reflection_candidates_owner_id",
            "super_assistant_reflection_candidates",
            ["owner_id"],
        )
        op.create_index(
            "ix_super_assistant_reflection_candidates_conversation_id",
            "super_assistant_reflection_candidates",
            ["conversation_id"],
        )

    if "super_assistant_memory_profiles" not in tables:
        op.create_table(
            "super_assistant_memory_profiles",
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("palace_index", sa.Text(), nullable=True),
            sa.Column("profile", sa.Text(), nullable=True),
            sa.Column("auto_accept_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("compiled_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("owner_id"),
        )

    conversation_columns = (
        {column["name"] for column in inspector.get_columns("super_assistant_conversations")}
        if "super_assistant_conversations" in tables
        else set()
    )
    if conversation_columns and "summary" not in conversation_columns:
        op.add_column("super_assistant_conversations", sa.Column("summary", sa.Text(), nullable=True))
    if conversation_columns and "summary_message_count" not in conversation_columns:
        op.add_column(
            "super_assistant_conversations",
            sa.Column("summary_message_count", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "super_assistant_conversations" in tables:
        conversation_columns = {
            column["name"] for column in inspector.get_columns("super_assistant_conversations")
        }
        if "summary_message_count" in conversation_columns:
            op.drop_column("super_assistant_conversations", "summary_message_count")
        if "summary" in conversation_columns:
            op.drop_column("super_assistant_conversations", "summary")

    if "super_assistant_memory_profiles" in tables:
        op.drop_table("super_assistant_memory_profiles")

    if "super_assistant_reflection_candidates" in tables:
        op.drop_index(
            "ix_super_assistant_reflection_candidates_conversation_id",
            table_name="super_assistant_reflection_candidates",
        )
        op.drop_index(
            "ix_super_assistant_reflection_candidates_owner_id",
            table_name="super_assistant_reflection_candidates",
        )
        op.drop_index(
            "ix_super_assistant_reflection_candidates_run_id",
            table_name="super_assistant_reflection_candidates",
        )
        op.drop_index(
            "ix_sa_reflect_candidates_owner_status",
            table_name="super_assistant_reflection_candidates",
        )
        op.drop_table("super_assistant_reflection_candidates")

    if "super_assistant_reflection_runs" in tables:
        op.drop_index(
            "ix_super_assistant_reflection_runs_message_id",
            table_name="super_assistant_reflection_runs",
        )
        op.drop_index(
            "ix_super_assistant_reflection_runs_conversation_id",
            table_name="super_assistant_reflection_runs",
        )
        op.drop_index(
            "ix_super_assistant_reflection_runs_owner_id",
            table_name="super_assistant_reflection_runs",
        )
        op.drop_index(
            "ix_sa_reflect_runs_owner_created",
            table_name="super_assistant_reflection_runs",
        )
        op.drop_index(
            "ix_sa_reflect_runs_conversation_created",
            table_name="super_assistant_reflection_runs",
        )
        op.drop_table("super_assistant_reflection_runs")

    if "super_assistant_memories" in tables:
        op.drop_index("ix_super_assistant_memories_owner_id", table_name="super_assistant_memories")
        op.drop_index("ix_sa_memories_owner_updated", table_name="super_assistant_memories")
        op.drop_index("ix_sa_memories_owner_zone", table_name="super_assistant_memories")
        op.drop_table("super_assistant_memories")
