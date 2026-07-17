"""bind governance facts and sentinel firings to ontology releases

Revision ID: 0030_governance_release_binding
Revises: 0029_version_canvas_layout
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0030_governance_release_binding"
down_revision = "0029_version_canvas_layout"
branch_labels = None
depends_on = None


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
    facts = _columns("fo_property_facts")
    if facts and "ontology_version" not in facts:
        op.add_column(
            "fo_property_facts",
            sa.Column("ontology_version", sa.String(length=20), nullable=True),
        )
    if _columns("fo_property_facts") and "ix_fo_property_facts_ontology_version" not in _indexes("fo_property_facts"):
        op.create_index(
            "ix_fo_property_facts_ontology_version",
            "fo_property_facts", ["ontology_version"], unique=False,
        )

    firings = _columns("sentinel_firings")
    if firings and "ontology_version" not in firings:
        op.add_column(
            "sentinel_firings",
            sa.Column("ontology_version", sa.String(length=20), nullable=True),
        )
    if _columns("sentinel_firings") and "ix_sentinel_firings_ontology_version" not in _indexes("sentinel_firings"):
        op.create_index(
            "ix_sentinel_firings_ontology_version",
            "sentinel_firings", ["ontology_version"], unique=False,
        )

    # Decision/action facts have a durable action-log pointer and can be
    # backfilled exactly.  Other legacy facts/firings stay NULL rather than
    # being guessed into the current release and leaking cross-version history.
    if _columns("fo_action_logs") and _columns("fo_property_facts"):
        op.execute(sa.text("""
            UPDATE fo_property_facts
            SET ontology_version = (
                SELECT fo_action_logs.ontology_version
                FROM fo_action_logs
                WHERE fo_action_logs.ontology_id = fo_property_facts.ontology_id
                  AND (
                    fo_action_logs.id = fo_property_facts.instance_id
                    OR fo_action_logs.id = fo_property_facts.caused_by
                  )
                LIMIT 1
            )
            WHERE ontology_version IS NULL
              AND EXISTS (
                SELECT 1 FROM fo_action_logs
                WHERE fo_action_logs.ontology_id = fo_property_facts.ontology_id
                  AND fo_action_logs.ontology_version IS NOT NULL
                  AND (
                    fo_action_logs.id = fo_property_facts.instance_id
                    OR fo_action_logs.id = fo_property_facts.caused_by
                  )
              )
        """))


def downgrade() -> None:
    if "ontology_version" in _columns("sentinel_firings"):
        with op.batch_alter_table("sentinel_firings") as batch:
            if "ix_sentinel_firings_ontology_version" in _indexes("sentinel_firings"):
                batch.drop_index("ix_sentinel_firings_ontology_version")
            batch.drop_column("ontology_version")
    if "ontology_version" in _columns("fo_property_facts"):
        with op.batch_alter_table("fo_property_facts") as batch:
            if "ix_fo_property_facts_ontology_version" in _indexes("fo_property_facts"):
                batch.drop_index("ix_fo_property_facts_ontology_version")
            batch.drop_column("ontology_version")
