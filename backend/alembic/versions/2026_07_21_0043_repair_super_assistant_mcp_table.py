"""repair a missing super assistant MCP server table

Revision ID: 0043_repair_sa_mcp_table
Revises: 0042_open_community_permissions
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0043_repair_sa_mcp_table"
down_revision = "0042_open_community_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa_inspect(bind).has_table("super_assistant_mcp_servers"):
        return

    # Some development databases were created while the Super Assistant
    # schema was still evolving and can contain the other four tables without
    # this one.  Recreate the missing table with the complete current schema so
    # an upgrade repairs those databases instead of leaving the MCP endpoint at
    # a permanent 500 response.
    op.create_table(
        "super_assistant_mcp_servers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("builtin_key", sa.String(length=50), nullable=True),
        sa.Column("transport", sa.String(length=30), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("headers_encrypted", sa.Text(), nullable=True),
        sa.Column("header_names", sa.JSON(), nullable=False),
        sa.Column("command", sa.String(length=1000), nullable=True),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("env_encrypted", sa.Text(), nullable=True),
        sa.Column("env_names", sa.JSON(), nullable=False),
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
    op.create_index(
        "ix_super_assistant_mcp_servers_owner_id",
        "super_assistant_mcp_servers",
        ["owner_id"],
    )
    op.create_index(
        "ix_sa_mcp_owner_updated",
        "super_assistant_mcp_servers",
        ["owner_id", "updated_at"],
    )


def downgrade() -> None:
    # This is a repair-only migration.  Dropping the table on downgrade would
    # destroy valid MCP configuration data in databases that never needed the
    # repair.
    pass
