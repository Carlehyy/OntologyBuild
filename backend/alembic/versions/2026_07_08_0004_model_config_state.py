"""add model config state fields

Revision ID: 0004_model_config_state
Revises: 0003_sentinel_firing_edges
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "0004_model_config_state"
down_revision = "0003_sentinel_firing_edges"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    inspector = sa_inspect(conn)
    return column in [c["name"] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _column_exists("model_configs", "enabled"):
        op.add_column("model_configs", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    if not _column_exists("model_configs", "is_default"):
        op.add_column("model_configs", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    if _column_exists("model_configs", "is_default"):
        op.drop_column("model_configs", "is_default")
    if _column_exists("model_configs", "enabled"):
        op.drop_column("model_configs", "enabled")
