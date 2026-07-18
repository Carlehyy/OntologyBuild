"""add isolated super assistant runtime

Revision ID: 0032_super_assistant
Revises: 0031_governance_release_identity
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0032_super_assistant"
down_revision = "0031_governance_release_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0003 historically calls Base.metadata.create_all() and therefore
    # bootstraps models that were registered later. Fresh databases can already
    # contain all five tables by the time this revision runs; deployed databases
    # at 0031 do not. Keep both upgrade paths deterministic.
    required_tables = {
        "super_assistant_conversations",
        "super_assistant_messages",
        "super_assistant_tool_runs",
        "super_assistant_skills",
        "super_assistant_mcp_servers",
    }
    existing = set(sa_inspect(op.get_bind()).get_table_names())
    if required_tables <= existing:
        return
    partial = required_tables & existing
    if partial:
        raise RuntimeError(
            "检测到不完整的超级助手表，请先修复后再迁移: "
            + ", ".join(sorted(partial))
        )
    op.create_table(
        "super_assistant_conversations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("model_config_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["model_config_id"], ["model_configs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_super_assistant_conversations_owner_id", "super_assistant_conversations", ["owner_id"])
    op.create_index("ix_sa_conversations_owner_updated", "super_assistant_conversations", ["owner_id", "updated_at"])

    op.create_table(
        "super_assistant_skills",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("triggers", sa.JSON(), nullable=False),
        sa.Column("folder_path", sa.String(length=1000), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "name", name="uq_sa_skill_owner_name"),
    )
    op.create_index("ix_super_assistant_skills_owner_id", "super_assistant_skills", ["owner_id"])
    op.create_index("ix_sa_skills_owner_updated", "super_assistant_skills", ["owner_id", "updated_at"])

    op.create_table(
        "super_assistant_mcp_servers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("transport", sa.String(length=30), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("headers_encrypted", sa.Text(), nullable=True),
        sa.Column("header_names", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("require_confirmation", sa.Boolean(), nullable=False),
        sa.Column("tool_manifest", sa.JSON(), nullable=False),
        sa.Column("last_test_status", sa.String(length=20), nullable=True),
        sa.Column("last_test_message", sa.String(length=500), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "name", name="uq_sa_mcp_owner_name"),
    )
    op.create_index("ix_super_assistant_mcp_servers_owner_id", "super_assistant_mcp_servers", ["owner_id"])
    op.create_index("ix_sa_mcp_owner_updated", "super_assistant_mcp_servers", ["owner_id", "updated_at"])

    op.create_table(
        "super_assistant_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("token_usage", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["super_assistant_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_super_assistant_messages_conversation_id", "super_assistant_messages", ["conversation_id"])
    op.create_index("ix_sa_messages_conversation_created", "super_assistant_messages", ["conversation_id", "created_at"])

    op.create_table(
        "super_assistant_tool_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("assistant_message_id", sa.String(), nullable=True),
        sa.Column("call_id", sa.String(length=200), nullable=False),
        sa.Column("tool_name", sa.String(length=300), nullable=False),
        sa.Column("server_id", sa.String(), nullable=True),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["super_assistant_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["super_assistant_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["server_id"], ["super_assistant_mcp_servers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_super_assistant_tool_runs_conversation_id", "super_assistant_tool_runs", ["conversation_id"])
    op.create_index("ix_sa_tool_runs_conversation_created", "super_assistant_tool_runs", ["conversation_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_sa_tool_runs_conversation_created", table_name="super_assistant_tool_runs")
    op.drop_index("ix_super_assistant_tool_runs_conversation_id", table_name="super_assistant_tool_runs")
    op.drop_table("super_assistant_tool_runs")
    op.drop_index("ix_sa_messages_conversation_created", table_name="super_assistant_messages")
    op.drop_index("ix_super_assistant_messages_conversation_id", table_name="super_assistant_messages")
    op.drop_table("super_assistant_messages")
    op.drop_index("ix_sa_mcp_owner_updated", table_name="super_assistant_mcp_servers")
    op.drop_index("ix_super_assistant_mcp_servers_owner_id", table_name="super_assistant_mcp_servers")
    op.drop_table("super_assistant_mcp_servers")
    op.drop_index("ix_sa_skills_owner_updated", table_name="super_assistant_skills")
    op.drop_index("ix_super_assistant_skills_owner_id", table_name="super_assistant_skills")
    op.drop_table("super_assistant_skills")
    op.drop_index("ix_sa_conversations_owner_updated", table_name="super_assistant_conversations")
    op.drop_index("ix_super_assistant_conversations_owner_id", table_name="super_assistant_conversations")
    op.drop_table("super_assistant_conversations")
