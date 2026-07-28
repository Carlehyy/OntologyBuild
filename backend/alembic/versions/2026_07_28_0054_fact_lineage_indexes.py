"""add composite indexes for release-scoped Fact lineage reads

Revision ID: 0054_fact_lineage_indexes
Revises: 0053_ontology_trial_claims
"""

from alembic import op
from sqlalchemy import inspect


revision = "0054_fact_lineage_indexes"
down_revision = "0053_ontology_trial_claims"
branch_labels = None
depends_on = None


TABLE = "fo_property_facts"
INDEXES = {
    "ix_fo_facts_release_coord_order": [
        "ontology_id",
        "ontology_release_id",
        "kind",
        "instance_id",
        "property_name",
        "recorded_at",
        "seq",
        "id",
    ],
    "ix_fo_facts_instance_coord_order": [
        "ontology_id",
        "instance_id",
        "kind",
        "property_name",
        "recorded_at",
        "seq",
        "id",
    ],
}


def _existing_indexes() -> set[str]:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return set()
    return {
        index["name"]
        for index in inspector.get_indexes(TABLE)
        if index.get("name")
    }


def _create_index(name: str, columns: list[str]) -> None:
    context = op.get_context()
    if op.get_bind().dialect.name == "postgresql":
        # This table is append-only and can be large in mature deployments.
        # Avoid blocking Fact writers while the upgrade builds each index.
        with context.autocommit_block():
            op.create_index(
                name,
                TABLE,
                columns,
                unique=False,
                postgresql_concurrently=True,
            )
        return
    op.create_index(name, TABLE, columns, unique=False)


def _drop_index(name: str) -> None:
    context = op.get_context()
    if op.get_bind().dialect.name == "postgresql":
        with context.autocommit_block():
            op.drop_index(
                name,
                table_name=TABLE,
                postgresql_concurrently=True,
            )
        return
    op.drop_index(name, table_name=TABLE)


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return
    existing = _existing_indexes()
    for name, columns in INDEXES.items():
        if name not in existing:
            _create_index(name, columns)


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return
    existing = _existing_indexes()
    for name in reversed(INDEXES):
        if name in existing:
            _drop_index(name)
