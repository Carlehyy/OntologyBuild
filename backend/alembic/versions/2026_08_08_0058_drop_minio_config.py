"""drop administrator MinIO configuration table

The system-settings MinIO page and its manual connection configuration are
retired. The platform's only MinIO is the deployment environment one; the
built-in assistant MCP reads that environment and is locked to a dedicated
workspace bucket. The credential-free audit trail is preserved.

Downgrade recreates an empty ``minio_config`` table only: previously stored
(encrypted) credentials cannot be recovered.

Revision ID: 0058_drop_minio_config
Revises: 0057_domain_registry_backfill
Create Date: 2026-08-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0058_drop_minio_config"
down_revision = "0057_domain_registry_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())
    if "minio_config" in tables:
        op.drop_table("minio_config")


def downgrade() -> None:
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
