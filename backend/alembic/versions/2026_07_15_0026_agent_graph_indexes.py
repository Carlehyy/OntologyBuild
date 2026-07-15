"""add bounded assistant graph query indexes

Revision ID: 0026_agent_graph_indexes
Revises: 0025_ontology_evolution
Create Date: 2026-07-15
"""
from alembic import op
from sqlalchemy import inspect as sa_inspect


revision = "0026_agent_graph_indexes"
down_revision = "0025_ontology_evolution"
branch_labels = None
depends_on = None


_INDEXES = (
    (
        "ix_fo_object_instances_graph_page",
        "fo_object_instances",
        ["ontology_id", "object_type_id", "updated_at"],
    ),
    (
        "ix_fo_link_instances_graph_source",
        "fo_link_instances",
        ["ontology_id", "link_type_id", "source_object_id"],
    ),
    (
        "ix_fo_link_instances_graph_target",
        "fo_link_instances",
        ["ontology_id", "link_type_id", "target_object_id"],
    ),
)


def _table_names() -> set[str]:
    return set(sa_inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    return {item["name"] for item in sa_inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    tables = _table_names()
    for name, table, columns in _INDEXES:
        if table in tables and name not in _index_names(table):
            op.create_index(name, table, columns)


def downgrade() -> None:
    tables = _table_names()
    for name, table, _ in reversed(_INDEXES):
        if table in tables and name in _index_names(table):
            op.drop_index(name, table_name=table)
