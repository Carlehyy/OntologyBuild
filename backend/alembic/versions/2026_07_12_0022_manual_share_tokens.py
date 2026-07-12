"""Persist encrypted manual-dataset share tokens for link reuse.

Revision ID: 0022_manual_share_tokens
Revises: 0021_exploration_workspace
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0022_manual_share_tokens"
down_revision = "0021_exploration_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("v2_manual_dataset_shares")
    }
    if "token_encrypted" not in columns:
        op.add_column(
            "v2_manual_dataset_shares",
            sa.Column("token_encrypted", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("v2_manual_dataset_shares")
    }
    if "token_encrypted" in columns:
        op.drop_column("v2_manual_dataset_shares", "token_encrypted")
