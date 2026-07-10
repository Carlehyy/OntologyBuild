"""reconcile production schema and unify dataset foreign keys

Revision ID: 0014_schema_reconciliation
Revises: 0013_sentinel_action_idempotency
Create Date: 2026-07-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0014_schema_reconciliation"
down_revision = "0013_sentinel_action_idempotency"
branch_labels = None
depends_on = None


_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa_inspect(op.get_bind()).get_columns(table)}


def _add_column(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def _fk_name(table: str, column: str, referred_table: str) -> str:
    return f"fk_{table}_{column}_{referred_table}"


def _replace_dataset_fks(table: str, columns: list[tuple[str, str | None]]) -> None:
    """Point selected columns at the canonical v2_datasets table.

    ``columns`` contains (column_name, ondelete).  SQLite requires batch table
    recreation; PostgreSQL can alter named constraints directly.
    """
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing = inspector.get_foreign_keys(table)
    by_column = {
        tuple(fk.get("constrained_columns") or []): fk
        for fk in existing
    }
    needs_change = []
    for column, ondelete in columns:
        current = by_column.get((column,))
        if (current is None
                or current.get("referred_table") != "v2_datasets"
                or (ondelete and (current.get("options") or {}).get("ondelete") != ondelete)):
            needs_change.append((column, ondelete, current))
    if not needs_change:
        return

    if bind.dialect.name != "sqlite":
        for column, ondelete, current in needs_change:
            if current and current.get("name"):
                op.drop_constraint(current["name"], table, type_="foreignkey")
            op.create_foreign_key(
                _fk_name(table, column, "v2_datasets"),
                table, "v2_datasets", [column], ["id"], ondelete=ondelete)
        return

    with op.batch_alter_table(
        table, recreate="always", naming_convention=_NAMING
    ) as batch:
        for column, ondelete, current in needs_change:
            if current:
                old_table = current.get("referred_table") or "unknown"
                batch.drop_constraint(
                    current.get("name") or _fk_name(table, column, old_table),
                    type_="foreignkey")
            batch.create_foreign_key(
                _fk_name(table, column, "v2_datasets"),
                "v2_datasets", [column], ["id"], ondelete=ondelete)


def upgrade() -> None:
    # Columns present in current models but absent from the historical compact
    # baseline.  Fresh and upgraded databases must converge to the same shape.
    _add_column("entities", sa.Column("name_abbr", sa.String(50), nullable=True))
    _add_column("entities", sa.Column("snomed_id", sa.String(50), nullable=True))
    _add_column("entities", sa.Column("canonical_id", sa.String(200), nullable=True))
    _add_column("v2_pipeline_runs", sa.Column("task_id", sa.String(), nullable=True))
    _add_column("v2_ontology_mappings", sa.Column(
        "target_object_type_id", sa.String(), nullable=True))
    _add_column("v2_ontology_link_mappings", sa.Column(
        "link_type_id", sa.String(), nullable=True))
    _add_column("v2_ontology_link_mappings", sa.Column(
        "edge_dataset_id", sa.String(), nullable=True))
    _add_column("v2_ontology_link_mappings", sa.Column(
        "field_mapping", sa.JSON(), nullable=True,
        server_default=sa.text("'{}'")))

    # One asset identity table: reviews and all mapping inputs reference
    # v2_datasets.  The legacy v2_curated_datasets mirror remains readable only
    # during the compatibility window and is no longer a write-time dependency.
    _replace_dataset_fks(
        "v2_curated_reviews", [("curated_dataset_id", "CASCADE")])
    _replace_dataset_fks(
        "v2_ontology_mappings", [("curated_dataset_id", None)])
    _replace_dataset_fks(
        "v2_ontology_link_mappings", [
            ("src_dataset_id", None),
            ("tgt_dataset_id", None),
            ("edge_dataset_id", None),
        ])


def downgrade() -> None:
    _replace_dataset_fks(
        "v2_curated_reviews", [("curated_dataset_id", "CASCADE")])
    # Downgrade intentionally preserves the canonical v2_datasets foreign keys.
    # Reintroducing the split-brain legacy FK would make valid production rows
    # undeployable. Columns are likewise retained to keep rollback non-lossy.
