"""drop the QwenPaw agent_config table

The system-settings agent configuration page (QwenPaw integration) is
retired: the planned QwenPaw agent-platform integration is no longer part
of the product roadmap, so the single-row connection settings table, its
management API (``/api/v1/settings/agent-config*``) and the settings page
are removed end to end.

Downgrade recreates an empty ``agent_config`` table only: previously
stored (encrypted) QwenPaw credentials cannot be recovered.

Revision ID: 0073_drop_agent_config
Revises: 0072_fact_lake_lineage
Create Date: 2026-08-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0073_drop_agent_config"
down_revision = "0072_fact_lake_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())
    if "agent_config" in tables:
        op.drop_table("agent_config")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())
    if "agent_config" not in tables:
        op.create_table(
            "agent_config",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("base_url", sa.String(length=500), nullable=False),
            sa.Column("auth_enabled", sa.Boolean(), nullable=True),
            sa.Column("username", sa.String(length=200), nullable=False),
            sa.Column("password_encrypted", sa.String(length=500), nullable=False),
            sa.Column("token", sa.String(length=2000), nullable=False),
            sa.Column("target_agent_id", sa.String(length=200), nullable=False),
            sa.Column("target_agent_name", sa.String(length=200), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
