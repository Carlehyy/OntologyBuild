"""add administrator MinIO configuration and built-in assistant MCP

Revision ID: 0040_minio_platform
Revises: 0039_inbox
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0040_minio_platform"
down_revision = "0039_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "minio_config" not in tables:
        op.create_table(
            "minio_config",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("endpoint", sa.String(length=500), nullable=False),
            sa.Column("secure", sa.Boolean(), nullable=False),
            sa.Column("region", sa.String(length=100), nullable=False),
            sa.Column("default_bucket", sa.String(length=255), nullable=False),
            sa.Column("access_key_encrypted", sa.Text(), nullable=False),
            sa.Column("secret_key_encrypted", sa.Text(), nullable=False),
            sa.Column("read_enabled", sa.Boolean(), nullable=False),
            sa.Column("write_enabled", sa.Boolean(), nullable=False),
            sa.Column("delete_enabled", sa.Boolean(), nullable=False),
            sa.Column("mcp_enabled", sa.Boolean(), nullable=False),
            sa.Column("mcp_token_hash", sa.String(length=64), nullable=False),
            sa.Column("mcp_token_hint", sa.String(length=12), nullable=False),
            sa.Column("connected", sa.Boolean(), nullable=False),
            sa.Column("last_test_status", sa.String(length=20), nullable=True),
            sa.Column("last_test_message", sa.String(length=500), nullable=True),
            sa.Column("last_tested_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if "minio_operation_audits" not in tables:
        op.create_table(
            "minio_operation_audits",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("actor_type", sa.String(length=30), nullable=False),
            sa.Column("actor_id", sa.String(), nullable=True),
            sa.Column("operation", sa.String(length=100), nullable=False),
            sa.Column("bucket", sa.String(length=255), nullable=True),
            sa.Column("object_key", sa.String(length=1024), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=False),
            sa.Column("details", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_minio_audit_created", "minio_operation_audits", ["created_at"], unique=False,
        )
        op.create_index(
            "ix_minio_audit_operation_created", "minio_operation_audits",
            ["operation", "created_at"], unique=False,
        )

    if "super_assistant_mcp_servers" in tables:
        mcp_columns = {
            column["name"] for column in sa_inspect(bind).get_columns("super_assistant_mcp_servers")
        }
        if "builtin_key" not in mcp_columns:
            op.add_column(
                "super_assistant_mcp_servers",
                sa.Column("builtin_key", sa.String(length=50), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())
    if "super_assistant_mcp_servers" in tables:
        columns = {
            column["name"] for column in inspector.get_columns("super_assistant_mcp_servers")
        }
        if "builtin_key" in columns:
            op.drop_column("super_assistant_mcp_servers", "builtin_key")
    if "minio_operation_audits" in tables:
        op.drop_index("ix_minio_audit_operation_created", table_name="minio_operation_audits")
        op.drop_index("ix_minio_audit_created", table_name="minio_operation_audits")
        op.drop_table("minio_operation_audits")
    if "minio_config" in tables:
        op.drop_table("minio_config")
