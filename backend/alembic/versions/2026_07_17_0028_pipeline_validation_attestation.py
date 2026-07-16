"""add canvas pipeline publish validation attestation

Revision ID: 0028_pipeline_validation_attestation
Revises: 0027_merge_agent_graph_reports
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0028_pipeline_validation_attestation"
down_revision = "0027_merge_agent_graph_reports"
branch_labels = None
depends_on = None


def _widen_alembic_version_column() -> None:
    """Allow this and future descriptive revision IDs on PostgreSQL.

    Alembic creates ``alembic_version.version_num`` as ``VARCHAR(32)`` by
    default.  This revision ID is longer than that, so PostgreSQL otherwise
    rolls back the migration when Alembic records the new current revision.
    SQLite does not enforce the declared VARCHAR length and needs no DDL.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )


def _columns() -> set[str]:
    inspector = sa_inspect(op.get_bind())
    if "v2_pipelines" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("v2_pipelines")}


def upgrade() -> None:
    # This must run before Alembic updates version_num after upgrade() returns.
    _widen_alembic_version_column()
    if "validation_attestation" not in _columns():
        op.add_column(
            "v2_pipelines",
            sa.Column("validation_attestation", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if "validation_attestation" in _columns():
        with op.batch_alter_table("v2_pipelines") as batch:
            batch.drop_column("validation_attestation")
