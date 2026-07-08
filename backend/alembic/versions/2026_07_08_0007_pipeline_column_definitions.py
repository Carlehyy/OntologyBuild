"""add column_definitions to v2_pipelines

Revision ID: 0007_pipeline_column_definitions
Revises: 0006_element_source_lineage
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "0007_pipeline_column_definitions"
down_revision = "0006_element_source_lineage"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    inspector = sa_inspect(conn)
    return column in [c["name"] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _column_exists("v2_pipelines", "column_definitions"):
        op.add_column(
            "v2_pipelines",
            sa.Column("column_definitions", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("v2_pipelines", "column_definitions"):
        op.drop_column("v2_pipelines", "column_definitions")
