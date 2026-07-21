"""support stdio, legacy SSE, and Streamable HTTP MCP clients

Revision ID: 0034_sa_mcp_transports
Revises: 0033_model_call_log_query_index
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0034_sa_mcp_transports"
down_revision = "0033_model_call_log_query_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0003 calls Base.metadata.create_all(), so fresh databases may
    # already contain model columns that deployed 0033 databases do not.
    if not sa_inspect(op.get_bind()).has_table("super_assistant_mcp_servers"):
        # A later repair migration recreates the complete current table.  Do
        # not prevent a stamped, partially damaged database from reaching it.
        return
    existing = {
        column["name"] for column in sa_inspect(op.get_bind()).get_columns("super_assistant_mcp_servers")
    }
    columns = (
        sa.Column("command", sa.String(length=1000), nullable=True),
        sa.Column("args", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("env_encrypted", sa.Text(), nullable=True),
        sa.Column("env_names", sa.JSON(), nullable=False, server_default="[]"),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("super_assistant_mcp_servers", column)


def downgrade() -> None:
    if not sa_inspect(op.get_bind()).has_table("super_assistant_mcp_servers"):
        return
    existing = {
        column["name"] for column in sa_inspect(op.get_bind()).get_columns("super_assistant_mcp_servers")
    }
    for name in ("env_names", "env_encrypted", "args", "command"):
        if name in existing:
            op.drop_column("super_assistant_mcp_servers", name)
