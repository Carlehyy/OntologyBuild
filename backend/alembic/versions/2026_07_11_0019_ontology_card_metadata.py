"""add ontology card icon metadata

Revision ID: 0019_ontology_card_metadata
Revises: 0018_manual_dataset_sharing
Create Date: 2026-07-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0019_ontology_card_metadata"
down_revision = "0018_manual_dataset_sharing"
branch_labels = None
depends_on = None


def _has_icon() -> bool:
    inspector = inspect(op.get_bind())
    return (
        inspector.has_table("ontology_projects")
        and "icon" in {column["name"] for column in inspector.get_columns("ontology_projects")}
    )


def upgrade() -> None:
    # Fresh databases may already have this column because migration 0003
    # bootstraps the current model registry. Existing databases need the ALTER.
    if not _has_icon():
        op.add_column("ontology_projects", sa.Column("icon", sa.String(50), nullable=True))


def downgrade() -> None:
    if _has_icon():
        op.drop_column("ontology_projects", "icon")
