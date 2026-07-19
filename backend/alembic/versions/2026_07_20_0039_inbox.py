"""add personal inbox and pipeline task ownership

Revision ID: 0039_inbox
Revises: 0038_dynamic_sentinels
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0039_inbox"
down_revision = "0038_dynamic_sentinels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    task_columns = {
        column["name"] for column in inspector.get_columns("v2_pipeline_tasks")
    }
    if "created_by" not in task_columns and bind.dialect.name == "sqlite":
        # SQLite can add a nullable REFERENCES column directly. Avoid Alembic's
        # batch table recreation here: this legacy table has accumulated column
        # ordering hints across migrations that form a cycle in Alembic 1.13.
        op.execute(sa.text(
            "ALTER TABLE v2_pipeline_tasks ADD COLUMN created_by VARCHAR "
            "REFERENCES users(id) ON DELETE SET NULL"
        ))
    elif "created_by" not in task_columns:
        op.add_column(
            "v2_pipeline_tasks",
            sa.Column("created_by", sa.String(), nullable=True),
        )
        op.create_foreign_key(
            "fk_pipeline_tasks_created_by_users",
            "v2_pipeline_tasks",
            "users",
            ["created_by"],
            ["id"],
            ondelete="SET NULL",
        )

    # Fresh installations may already have the current ORM column because the
    # legacy 0017 reconciliation migration creates missing tables from current
    # metadata. Keep this revision safe for both fresh and upgraded databases.
    inspector = sa.inspect(bind)
    task_indexes = {
        index["name"] for index in inspector.get_indexes("v2_pipeline_tasks")
    }
    if "ix_v2_pipeline_tasks_created_by" not in task_indexes:
        op.create_index(
            "ix_v2_pipeline_tasks_created_by",
            "v2_pipeline_tasks",
            ["created_by"],
            unique=False,
        )

    op.execute(sa.text(
        "UPDATE v2_pipeline_tasks SET created_by = ("
        " SELECT v2_pipelines.created_by FROM v2_pipelines"
        " WHERE v2_pipelines.id = v2_pipeline_tasks.pipeline_id"
        ") WHERE created_by IS NULL"
    ))

    op.create_table(
        "inbox_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=12), nullable=False),
        sa.Column("source_system", sa.String(length=80), nullable=False),
        sa.Column("source_type", sa.String(length=120), nullable=False),
        sa.Column("source_id", sa.String(length=200), nullable=False),
        sa.Column("correlation_key", sa.String(length=300), nullable=False),
        sa.Column("open_key", sa.String(length=255), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("business_state", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("safe_context", sa.JSON(), nullable=False),
        sa.Column("resource", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("latest_occurrence_id", sa.String(length=200), nullable=True),
        sa.Column("first_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolution_reason", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('task','alert','notice')", name="ck_inbox_items_kind"),
        sa.CheckConstraint(
            "priority IN ('urgent','high','normal','low')",
            name="ck_inbox_items_priority",
        ),
        sa.CheckConstraint(
            "business_state IN ('open','resolved','cancelled','expired')",
            name="ck_inbox_items_business_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("open_key"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_inbox_items_source",
        "inbox_items",
        ["source_system", "source_type", "source_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_inbox_items_state_last",
        "inbox_items",
        ["business_state", "last_occurred_at"],
        unique=False,
        if_not_exists=True,
    )

    op.create_table(
        "inbox_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_user_id", sa.String(), nullable=False),
        sa.Column("delivery_state", sa.String(length=20), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "delivery_state IN ('unread','read','archived')",
            name="ck_inbox_deliveries_state",
        ),
        sa.ForeignKeyConstraint(["item_id"], ["inbox_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "recipient_user_id", name="uq_inbox_delivery_recipient"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_inbox_deliveries_item_id",
        "inbox_deliveries",
        ["item_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_inbox_deliveries_recipient_user_id",
        "inbox_deliveries",
        ["recipient_user_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_inbox_deliveries_user_state",
        "inbox_deliveries",
        ["recipient_user_id", "delivery_state"],
        unique=False,
        if_not_exists=True,
    )

    op.create_table(
        "inbox_event_receipts",
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("source_system", sa.String(length=80), nullable=False),
        sa.Column("source_type", sa.String(length=120), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["inbox_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("event_id"),
        if_not_exists=True,
    )

    op.create_table(
        "inbox_outbox_events",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','processing','completed')",
            name="ck_inbox_outbox_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_inbox_outbox_pending",
        "inbox_outbox_events",
        ["status", "created_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_inbox_outbox_pending", table_name="inbox_outbox_events")
    op.drop_table("inbox_outbox_events")
    op.drop_table("inbox_event_receipts")
    op.drop_index("ix_inbox_deliveries_user_state", table_name="inbox_deliveries")
    op.drop_index("ix_inbox_deliveries_recipient_user_id", table_name="inbox_deliveries")
    op.drop_index("ix_inbox_deliveries_item_id", table_name="inbox_deliveries")
    op.drop_table("inbox_deliveries")
    op.drop_index("ix_inbox_items_state_last", table_name="inbox_items")
    op.drop_index("ix_inbox_items_source", table_name="inbox_items")
    op.drop_table("inbox_items")
    op.drop_index("ix_v2_pipeline_tasks_created_by", table_name="v2_pipeline_tasks")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite cannot DROP a column that participates in a foreign key in
        # place. Recreate only on downgrade; unlike the upgrade/add path this
        # does not introduce a competing append-order hint.
        with op.batch_alter_table("v2_pipeline_tasks") as batch:
            batch.drop_column("created_by")
    else:
        op.drop_constraint(
            "fk_pipeline_tasks_created_by_users",
            "v2_pipeline_tasks",
            type_="foreignkey",
        )
        op.drop_column("v2_pipeline_tasks", "created_by")
