"""bind formal runtime instances to immutable release ids

Revision ID: 0035_formal_instance_release_identity
Revises: 0034_sa_mcp_transports
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0035_formal_instance_release_identity"
down_revision = "0034_sa_mcp_transports"
branch_labels = None
depends_on = None


_TABLES = (
    "fo_object_instances",
    "fo_link_instances",
)


def _columns(table: str) -> set[str]:
    inspector = sa_inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(table: str) -> set[str]:
    inspector = sa_inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    # Do not backfill legacy rows.  An ontology/version label cannot prove which
    # immutable release produced them; assigning the current pointer would make
    # previously leaked draft/trial data look official.
    for table in _TABLES:
        if not _columns(table):
            continue
        if "ontology_release_id" not in _columns(table):
            op.add_column(
                table,
                sa.Column("ontology_release_id", sa.String(), nullable=True),
            )
        index_name = f"ix_{table}_ontology_release_id"
        if index_name not in _indexes(table):
            op.create_index(
                index_name, table, ["ontology_release_id"], unique=False,
            )


def downgrade() -> None:
    for table in reversed(_TABLES):
        if "ontology_release_id" not in _columns(table):
            continue
        index_name = f"ix_{table}_ontology_release_id"
        with op.batch_alter_table(table) as batch:
            if index_name in _indexes(table):
                batch.drop_index(index_name)
            batch.drop_column("ontology_release_id")
