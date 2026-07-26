"""freeze trial computed values and notification execution lineage

Revision ID: 0050_trial_computed_notification
Revises: 0049_sentinel_cdc_outbox
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0050_trial_computed_notification"
down_revision = "0049_sentinel_cdc_outbox"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {
        column["name"]
        for column in inspect(op.get_bind()).get_columns(table)
    }


def _indexes(table: str) -> set[str]:
    return {
        index["name"]
        for index in inspect(op.get_bind()).get_indexes(table)
        if index.get("name")
    }


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if inspector.has_table("ontology_trial_objects"):
        if "computed" not in _columns("ontology_trial_objects"):
            with op.batch_alter_table("ontology_trial_objects") as batch:
                batch.add_column(sa.Column(
                    "computed", sa.JSON(), nullable=False,
                    server_default="{}",
                ))

    inspector = inspect(op.get_bind())
    if not inspector.has_table("sentinel_notifications"):
        return
    missing = {
        name for name in (
            "ontology_release_id", "sentinel_id", "action_log_id",
        )
        if name not in _columns("sentinel_notifications")
    }
    if missing:
        with op.batch_alter_table("sentinel_notifications") as batch:
            for name in sorted(missing):
                batch.add_column(sa.Column(name, sa.String(), nullable=True))

    existing_indexes = _indexes("sentinel_notifications")
    for name in (
        "ontology_release_id", "sentinel_id", "action_log_id",
    ):
        index_name = f"ix_sentinel_notifications_{name}"
        if index_name not in existing_indexes:
            op.create_index(
                index_name, "sentinel_notifications", [name], unique=False)


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if inspector.has_table("sentinel_notifications"):
        existing_indexes = _indexes("sentinel_notifications")
        for name in (
            "ontology_release_id", "sentinel_id", "action_log_id",
        ):
            index_name = f"ix_sentinel_notifications_{name}"
            if index_name in existing_indexes:
                op.drop_index(
                    index_name, table_name="sentinel_notifications")
        notification_columns = _columns("sentinel_notifications")
        removable = [
            name for name in (
                "ontology_release_id", "sentinel_id", "action_log_id",
            )
            if name in notification_columns
        ]
        if removable:
            with op.batch_alter_table("sentinel_notifications") as batch:
                for name in removable:
                    batch.drop_column(name)

    inspector = inspect(op.get_bind())
    if (inspector.has_table("ontology_trial_objects")
            and "computed" in _columns("ontology_trial_objects")):
        with op.batch_alter_table("ontology_trial_objects") as batch:
            batch.drop_column("computed")
