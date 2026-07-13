"""add durable dataset version automation events

Revision ID: 0024_dataset_version_events
Revises: 0023_model_test_state
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0024_dataset_version_events"
down_revision = "0023_model_test_state"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa_inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("v2_dataset_version_events"):
        return
    op.create_table(
        "v2_dataset_version_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("dataset_version_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["v2_datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"], ["v2_dataset_versions.id"],
            ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_v2_dataset_version_events_version_type",
        "v2_dataset_version_events", ["dataset_version_id", "event_type"],
        unique=True,
    )
    op.create_index(
        "ix_v2_dataset_version_events_ready",
        "v2_dataset_version_events", ["status", "available_at", "created_at"],
    )
    op.create_index(
        "ix_v2_dataset_version_events_dataset_id",
        "v2_dataset_version_events", ["dataset_id"],
    )


def downgrade() -> None:
    if _has_table("v2_dataset_version_events"):
        op.drop_table("v2_dataset_version_events")
