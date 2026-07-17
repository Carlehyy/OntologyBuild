"""store version canvas layout outside immutable ontology snapshots

Revision ID: 0029_version_canvas_layout
Revises: 0028_pipeline_validation_attestation
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0029_version_canvas_layout"
down_revision = "0028_pipeline_validation_attestation"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    inspector = sa_inspect(op.get_bind())
    if "ontology_versions" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("ontology_versions")}


def upgrade() -> None:
    if "canvas_layout" not in _columns():
        op.add_column(
            "ontology_versions",
            sa.Column("canvas_layout", sa.JSON(), nullable=True),
        )
    op.execute(sa.text(
        "UPDATE ontology_versions SET canvas_layout = '{}' "
        "WHERE canvas_layout IS NULL"
    ))


def downgrade() -> None:
    if "canvas_layout" in _columns():
        with op.batch_alter_table("ontology_versions") as batch:
            batch.drop_column("canvas_layout")
