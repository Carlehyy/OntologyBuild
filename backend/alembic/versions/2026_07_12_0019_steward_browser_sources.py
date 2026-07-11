"""User-scoped browser sources and conversation binding.

Revision ID: 0019_steward_browser_sources
Revises: 0018_manual_dataset_sharing
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0019_steward_browser_sources"
down_revision = "0018_manual_dataset_sharing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("v2_steward_browser_sources"):
        op.create_table(
            "v2_steward_browser_sources",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("source_type", sa.String(30), nullable=False),
            sa.Column("endpoint_url_encrypted", sa.Text(), nullable=False, server_default=""),
            sa.Column("headers_encrypted", sa.Text(), nullable=False, server_default=""),
            sa.Column("device_token_hash", sa.String(64), nullable=False, server_default=""),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.CheckConstraint(
                "source_type IN ('remote_cdp','companion')",
                name="ck_steward_browser_sources_type"),
        )
        op.create_index(
            "ix_steward_browser_sources_user_id", "v2_steward_browser_sources", ["user_id"])
        op.create_index(
            "ix_steward_browser_sources_user_type", "v2_steward_browser_sources",
            ["user_id", "source_type"])

    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("v2_steward_conversations")}
    if "browser_source_id" not in columns:
        with op.batch_alter_table("v2_steward_conversations") as batch:
            batch.add_column(sa.Column("browser_source_id", sa.String(), nullable=True))
            batch.create_foreign_key(
                "fk_steward_conversation_browser_source", "v2_steward_browser_sources",
                ["browser_source_id"], ["id"], ondelete="SET NULL")
        op.create_index(
            "ix_v2_steward_conversations_browser_source_id",
            "v2_steward_conversations", ["browser_source_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("v2_steward_conversations"):
        columns = {column["name"] for column in inspector.get_columns("v2_steward_conversations")}
        if "browser_source_id" in columns:
            op.drop_index(
                "ix_v2_steward_conversations_browser_source_id",
                table_name="v2_steward_conversations")
            with op.batch_alter_table("v2_steward_conversations") as batch:
                batch.drop_constraint("fk_steward_conversation_browser_source", type_="foreignkey")
                batch.drop_column("browser_source_id")
    if inspect(bind).has_table("v2_steward_browser_sources"):
        op.drop_table("v2_steward_browser_sources")
