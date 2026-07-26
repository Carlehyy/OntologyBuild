"""add durable Sentinel CDC outbox

Revision ID: 0049_sentinel_cdc_outbox
Revises: 0048_trial_link_lineage
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0049_sentinel_cdc_outbox"
down_revision = "0048_trial_link_lineage"
branch_labels = None
depends_on = None


TABLE = "sentinel_cdc_outbox"


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("chain_id", sa.String(length=64), nullable=False),
            sa.Column("ontology_id", sa.String(), nullable=False),
            sa.Column("ontology_release_id", sa.String(), nullable=True),
            sa.Column("object_type_id", sa.String(), nullable=True),
            sa.Column("changed_keys", sa.JSON(), nullable=False),
            sa.Column(
                "link_change", sa.Boolean(), nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "cascade_depth", sa.Integer(), nullable=False,
                server_default="0",
            ),
            sa.Column(
                "mapping_ids", sa.JSON(), nullable=False,
                server_default="[]",
            ),
            sa.Column(
                "status", sa.String(length=20), nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "attempts", sa.Integer(), nullable=False,
                server_default="0",
            ),
            sa.Column(
                "available_at", sa.DateTime(timezone=True), nullable=False,
            ),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("claim_token", sa.String(length=64), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("result_json", sa.JSON(), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "status IN "
                "('held','pending','processing','retry','completed','dead')",
                name="ck_sentinel_cdc_outbox_status",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    inspector = inspect(op.get_bind())
    if inspector.has_table(TABLE):
        outbox_columns = {
            column["name"] for column in inspector.get_columns(TABLE)
        }
        if "mapping_ids" not in outbox_columns:
            with op.batch_alter_table(TABLE) as batch:
                batch.add_column(sa.Column(
                    "mapping_ids", sa.JSON(), nullable=False,
                    server_default="[]",
                ))
        if "ontology_release_id" not in outbox_columns:
            with op.batch_alter_table(TABLE) as batch:
                batch.add_column(sa.Column(
                    "ontology_release_id", sa.String(), nullable=True,
                ))
        inspector = inspect(op.get_bind())
        existing_indexes = {
            index["name"] for index in inspector.get_indexes(TABLE)
        }
        required_indexes = {
            "ix_sentinel_cdc_outbox_chain_id": ["chain_id"],
            "ix_sentinel_cdc_outbox_ontology_id": ["ontology_id"],
            "ix_sentinel_cdc_outbox_object_type_id": ["object_type_id"],
            "ix_sentinel_cdc_outbox_ready": [
                "status", "available_at", "created_at"],
            "ix_sentinel_cdc_outbox_chain": [
                "chain_id", "status", "created_at"],
            "ix_sentinel_cdc_outbox_release_status": [
                "ontology_id", "ontology_release_id", "status", "created_at"],
        }
        for index_name, columns in required_indexes.items():
            if index_name not in existing_indexes:
                op.create_index(
                    index_name, TABLE, columns, unique=False)

    inspector = inspect(op.get_bind())
    if inspector.has_table("fo_action_logs"):
        action_columns = {
            column["name"]
            for column in inspector.get_columns("fo_action_logs")
        }
        if "target_snapshot" not in action_columns:
            with op.batch_alter_table("fo_action_logs") as batch:
                batch.add_column(sa.Column(
                    "target_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if inspector.has_table("fo_action_logs"):
        action_columns = {
            column["name"]
            for column in inspector.get_columns("fo_action_logs")
        }
        if "target_snapshot" in action_columns:
            with op.batch_alter_table("fo_action_logs") as batch:
                batch.drop_column("target_snapshot")
    if inspector.has_table(TABLE):
        op.drop_table(TABLE)
