"""harden sentinel action parameters and edge-state uniqueness

Revision ID: 0012_sentinel_runtime_hardening
Revises: 0011_curated_review_version
Create Date: 2026-07-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0012_sentinel_runtime_hardening"
down_revision = "0011_curated_review_version"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    inspector = sa_inspect(op.get_bind())
    return column in {c["name"] for c in inspector.get_columns(table)}


def _unique_exists(table: str, name: str) -> bool:
    inspector = sa_inspect(op.get_bind())
    return name in {c.get("name") for c in inspector.get_unique_constraints(table)}


def _deduplicate_match_state() -> None:
    """Keep the most recently seen row before installing the unique guard."""
    conn = op.get_bind()
    rows = conn.execute(sa.text("""
        SELECT id, sentinel_id, match_key, last_seen_at
        FROM sentinel_match_state
        ORDER BY sentinel_id, match_key, last_seen_at DESC, id DESC
    """)).mappings()
    seen: set[tuple[str, str]] = set()
    duplicate_ids: list[str] = []
    for row in rows:
        key = (row["sentinel_id"], row["match_key"])
        if key in seen:
            duplicate_ids.append(row["id"])
        else:
            seen.add(key)
    if duplicate_ids:
        state = sa.table("sentinel_match_state", sa.column("id", sa.String()))
        conn.execute(sa.delete(state).where(state.c.id.in_(duplicate_ids)))


def upgrade() -> None:
    if not _column_exists("sentinels", "action_parameters"):
        op.add_column(
            "sentinels",
            sa.Column("action_parameters", sa.JSON(), nullable=False,
                      server_default=sa.text("'{}'")),
        )

    if not _unique_exists("sentinel_match_state", "uq_sentinel_match_state_key"):
        _deduplicate_match_state()
        with op.batch_alter_table("sentinel_match_state") as batch:
            batch.create_unique_constraint(
                "uq_sentinel_match_state_key", ["sentinel_id", "match_key"])


def downgrade() -> None:
    if _unique_exists("sentinel_match_state", "uq_sentinel_match_state_key"):
        with op.batch_alter_table("sentinel_match_state") as batch:
            batch.drop_constraint("uq_sentinel_match_state_key", type_="unique")
    if _column_exists("sentinels", "action_parameters"):
        op.drop_column("sentinels", "action_parameters")
