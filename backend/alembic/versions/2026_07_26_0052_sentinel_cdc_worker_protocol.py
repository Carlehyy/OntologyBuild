"""fence Sentinel CDC work from legacy shared-database consumers

Revision ID: 0052_sentinel_cdc_protocol
Revises: 0051_sentinel_control_events
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0052_sentinel_cdc_protocol"
down_revision = "0051_sentinel_control_events"
branch_labels = None
depends_on = None


TABLE = "sentinel_cdc_outbox"
CHECK_NAME = "ck_sentinel_cdc_outbox_status"
LEGACY_CHECK = (
    "status IN "
    "('held','pending','processing','retry','completed','dead')"
)
FENCED_CHECK = (
    "status IN ("
    "'held','pending','processing','retry','completed','dead',"
    "'cdc_held','cdc_pending','cdc_processing','cdc_retry','cdc_dead'"
    ")"
)


def _check_names() -> set[str]:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return set()
    return {
        item["name"]
        for item in inspector.get_check_constraints(TABLE)
        if item.get("name")
    }


def _replace_status_check(expression: str) -> None:
    names = _check_names()
    with op.batch_alter_table(TABLE) as batch:
        if CHECK_NAME in names:
            batch.drop_constraint(CHECK_NAME, type_="check")
        batch.create_check_constraint(CHECK_NAME, expression)


def upgrade() -> None:
    if not inspect(op.get_bind()).has_table(TABLE):
        return
    # Do not rewrite existing rows.  Legacy work remains consumable by the new
    # worker, while newly-produced cdc_* rows are invisible to old workers.
    _replace_status_check(FENCED_CHECK)


def downgrade() -> None:
    if not inspect(op.get_bind()).has_table(TABLE):
        return
    # The legacy constraint cannot accept protocol-v2 values.  Preserve every
    # row's lifecycle meaning before restoring the narrower schema contract.
    op.execute(sa.text(
        "UPDATE sentinel_cdc_outbox SET status = CASE status "
        "WHEN 'cdc_held' THEN 'held' "
        "WHEN 'cdc_pending' THEN 'pending' "
        "WHEN 'cdc_processing' THEN 'processing' "
        "WHEN 'cdc_retry' THEN 'retry' "
        "WHEN 'cdc_dead' THEN 'dead' "
        "ELSE status END "
        "WHERE status IN ("
        "'cdc_held','cdc_pending','cdc_processing','cdc_retry','cdc_dead'"
        ")"
    ))
    _replace_status_check(LEGACY_CHECK)
