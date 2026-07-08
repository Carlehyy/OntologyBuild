"""add column_definitions to v2_pipelines

Revision ID: 0006_pipeline_column_definitions
Revises: 0005_model_call_log
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_pipeline_column_definitions"
down_revision = "0005_model_call_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "v2_pipelines",
        sa.Column("column_definitions", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v2_pipelines", "column_definitions")
