"""add recoverable sentinel claims and action idempotency

Revision ID: 0013_sentinel_action_idempotency
Revises: 0012_sentinel_runtime_hardening
Create Date: 2026-07-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0013_sentinel_action_idempotency"
down_revision = "0012_sentinel_runtime_hardening"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    return column in {
        c["name"] for c in sa_inspect(op.get_bind()).get_columns(table)
    }


def _unique_exists(table: str, name: str) -> bool:
    return name in {
        c.get("name")
        for c in sa_inspect(op.get_bind()).get_unique_constraints(table)
    }


def _index_exists(table: str, name: str) -> bool:
    return name in {
        c.get("name") for c in sa_inspect(op.get_bind()).get_indexes(table)
    }


def upgrade() -> None:
    if not _column_exists("sentinel_match_state", "runtime_status"):
        op.add_column(
            "sentinel_match_state",
            sa.Column(
                "runtime_status", sa.String(length=24), nullable=False,
                server_default="completed",
            ),
        )
    if not _column_exists("sentinel_match_state", "execution_epoch"):
        op.add_column(
            "sentinel_match_state",
            sa.Column(
                "execution_epoch", sa.Integer(), nullable=False,
                server_default="0",
            ),
        )

    if not _column_exists("fo_action_logs", "idempotency_key"):
        op.add_column(
            "fo_action_logs",
            sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        )
    if not _column_exists("fo_action_logs", "sentinel_match_state_id"):
        op.add_column(
            "fo_action_logs",
            sa.Column("sentinel_match_state_id", sa.String(), nullable=True),
        )

    if not _unique_exists(
            "fo_action_logs", "uq_action_log_ontology_idempotency_key"):
        with op.batch_alter_table("fo_action_logs") as batch:
            batch.create_unique_constraint(
                "uq_action_log_ontology_idempotency_key",
                ["ontology_id", "idempotency_key"],
            )
    if not _index_exists(
            "fo_action_logs", "ix_fo_action_logs_sentinel_match_state_id"):
        op.create_index(
            "ix_fo_action_logs_sentinel_match_state_id", "fo_action_logs",
            ["sentinel_match_state_id"], unique=False,
        )


def downgrade() -> None:
    if _index_exists(
            "fo_action_logs", "ix_fo_action_logs_sentinel_match_state_id"):
        op.drop_index(
            "ix_fo_action_logs_sentinel_match_state_id",
            table_name="fo_action_logs",
        )
    if _unique_exists(
            "fo_action_logs", "uq_action_log_ontology_idempotency_key"):
        with op.batch_alter_table("fo_action_logs") as batch:
            batch.drop_constraint(
                "uq_action_log_ontology_idempotency_key", type_="unique")
    if _column_exists("fo_action_logs", "sentinel_match_state_id"):
        op.drop_column("fo_action_logs", "sentinel_match_state_id")
    if _column_exists("fo_action_logs", "idempotency_key"):
        op.drop_column("fo_action_logs", "idempotency_key")
    if _column_exists("sentinel_match_state", "execution_epoch"):
        op.drop_column("sentinel_match_state", "execution_epoch")
    if _column_exists("sentinel_match_state", "runtime_status"):
        op.drop_column("sentinel_match_state", "runtime_status")
