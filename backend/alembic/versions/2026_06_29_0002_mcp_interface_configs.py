"""add mcp interface configs

Revision ID: 0002_mcp_interface_configs
Revises: 0001_full_baseline
Create Date: 2026-06-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_mcp_interface_configs"
down_revision = "0001_full_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_interface_configs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("operation_id", sa.String(length=300), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_index("ix_mcp_interface_configs_operation_id", "mcp_interface_configs", ["operation_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_interface_configs_operation_id", table_name="mcp_interface_configs")
    op.drop_table("mcp_interface_configs")
