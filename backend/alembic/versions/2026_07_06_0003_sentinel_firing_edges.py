"""add sentinel firing edge columns

Revision ID: 0003_sentinel_firing_edges
Revises: 0002_mcp_interface_configs
Create Date: 2026-07-06
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_sentinel_firing_edges"
down_revision = "0002_mcp_interface_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sentinel_firings",
        sa.Column("entered", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.add_column(
        "sentinel_firings",
        sa.Column("left", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )


def downgrade() -> None:
    op.drop_column("sentinel_firings", "left")
    op.drop_column("sentinel_firings", "entered")
