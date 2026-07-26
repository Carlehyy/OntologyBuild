"""add durable Sentinel release and schedule control events

Revision ID: 0051_sentinel_control_events
Revises: 0050_trial_computed_notification
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0051_sentinel_control_events"
down_revision = "0050_trial_computed_notification"
branch_labels = None
depends_on = None


TABLE = "sentinel_cdc_outbox"


def _columns() -> set[str]:
    return {
        column["name"]
        for column in inspect(op.get_bind()).get_columns(TABLE)
    }


def _indexes() -> set[str]:
    return {
        index["name"]
        for index in inspect(op.get_bind()).get_indexes(TABLE)
        if index.get("name")
    }


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if inspector.has_table("sentinels"):
        sentinel_columns = {
            column["name"]
            for column in inspector.get_columns("sentinels")
        }
        if "enable_generation" not in sentinel_columns:
            with op.batch_alter_table("sentinels") as batch:
                batch.add_column(sa.Column(
                    "enable_generation", sa.Integer(), nullable=False,
                    server_default="0",
                ))

    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return
    missing = {
        name for name in ("event_kind", "sentinel_id", "dedupe_key")
        if name not in _columns()
    }
    if missing:
        with op.batch_alter_table(TABLE) as batch:
            if "event_kind" in missing:
                batch.add_column(sa.Column(
                    "event_kind", sa.String(32), nullable=False,
                    server_default="object_change",
                ))
            if "sentinel_id" in missing:
                batch.add_column(sa.Column(
                    "sentinel_id", sa.String(), nullable=True))
            if "dedupe_key" in missing:
                batch.add_column(sa.Column(
                    "dedupe_key", sa.String(255), nullable=True))
    op.execute(
        sa.text(
            "UPDATE sentinel_cdc_outbox "
            "SET event_kind = 'link_change' "
            "WHERE link_change = :link_change"
        ).bindparams(link_change=True)
    )
    # A legacy worker could persist ``processing`` without a claim timestamp
    # during a partial/manual schema transition. Such a row can never satisfy
    # the stale-claim predicate (NULL comparisons are false), so normalize it
    # into an immediately recoverable retry and leave an operator-visible
    # diagnostic. We deliberately do *not* synthesize historical release or
    # Sentinel activation events: replaying them could repeat external actions.
    op.execute(sa.text(
        "UPDATE sentinel_cdc_outbox "
        "SET status = 'retry', "
        "claimed_at = NULL, "
        "claim_token = NULL, "
        "available_at = CURRENT_TIMESTAMP, "
        "updated_at = CURRENT_TIMESTAMP, "
        "last_error = CASE "
        "WHEN COALESCE(last_error, '') = '' "
        "THEN 'migration_0051_recovered_missing_claimed_at' "
        "ELSE last_error || '; "
        "migration_0051_recovered_missing_claimed_at' END "
        "WHERE status = 'processing' AND claimed_at IS NULL"
    ))
    indexes = _indexes()
    if "ix_sentinel_cdc_outbox_control_ready" not in indexes:
        op.create_index(
            "ix_sentinel_cdc_outbox_control_ready",
            TABLE,
            [
                "event_kind", "sentinel_id", "ontology_release_id",
                "status", "available_at",
            ],
            unique=False,
        )
    if "uq_sentinel_cdc_outbox_dedupe_key" not in indexes:
        op.create_index(
            "uq_sentinel_cdc_outbox_dedupe_key",
            TABLE, ["dedupe_key"], unique=True)


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if inspector.has_table(TABLE):
        indexes = _indexes()
        for name in (
            "uq_sentinel_cdc_outbox_dedupe_key",
            "ix_sentinel_cdc_outbox_control_ready",
        ):
            if name in indexes:
                op.drop_index(name, table_name=TABLE)
        columns = _columns()
        removable = [
            name for name in ("dedupe_key", "sentinel_id", "event_kind")
            if name in columns
        ]
        if removable:
            with op.batch_alter_table(TABLE) as batch:
                for name in removable:
                    batch.drop_column(name)
    inspector = inspect(op.get_bind())
    if inspector.has_table("sentinels"):
        sentinel_columns = {
            column["name"]
            for column in inspector.get_columns("sentinels")
        }
        if "enable_generation" in sentinel_columns:
            with op.batch_alter_table("sentinels") as batch:
                batch.drop_column("enable_generation")
