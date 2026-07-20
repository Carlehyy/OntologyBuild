"""add managed n8n pipeline file assets

Revision ID: 0040_pipeline_file_assets
Revises: 0039_inbox
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0040_pipeline_file_assets"
down_revision = "0039_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0003 historically calls current Base.metadata.create_all(), so
    # a fresh database can already contain this current ORM table before the
    # revision chain reaches 0040.  Upgraded production databases do not.
    if sa.inspect(op.get_bind()).has_table("v2_pipeline_file_assets"):
        return
    op.create_table(
        "v2_pipeline_file_assets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("pipeline_id", sa.String(), nullable=True),
        sa.Column("workflow_id", sa.String(length=100), nullable=False),
        sa.Column("invocation_id", sa.String(length=100), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=True),
        sa.Column("dataset_version_id", sa.String(), nullable=True),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("original_name", sa.String(length=500), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=200), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('preview','run')",
            name="ck_pipeline_file_assets_purpose",
        ),
        sa.CheckConstraint(
            "status IN ('ready','committed','deleted','failed')",
            name="ck_pipeline_file_assets_status",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_id"], ["v2_pipelines.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"], ["v2_dataset_versions.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_pipeline_file_assets_idempotency",
        "v2_pipeline_file_assets",
        ["pipeline_id", "invocation_id", "idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_pipeline_file_assets_expiry",
        "v2_pipeline_file_assets",
        ["status", "expires_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_pipeline_file_assets_dataset_version",
        "v2_pipeline_file_assets",
        ["dataset_version_id"],
        unique=False,
    )


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("v2_pipeline_file_assets"):
        return
    op.drop_index(
        "ix_pipeline_file_assets_dataset_version",
        table_name="v2_pipeline_file_assets",
    )
    op.drop_index(
        "ix_pipeline_file_assets_expiry",
        table_name="v2_pipeline_file_assets",
    )
    op.drop_index(
        "uq_pipeline_file_assets_idempotency",
        table_name="v2_pipeline_file_assets",
    )
    op.drop_table("v2_pipeline_file_assets")
