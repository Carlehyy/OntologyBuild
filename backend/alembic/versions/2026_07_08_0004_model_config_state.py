"""add model config state fields

Revision ID: 0004_model_config_state
Revises: 0003_sentinel_firing_edges
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_model_config_state"
down_revision = "0003_sentinel_firing_edges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_configs", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("model_configs", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("model_configs", "is_default")
    op.drop_column("model_configs", "enabled")
