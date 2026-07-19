"""add custom role menu permissions

Revision ID: 0037_custom_role
Revises: 0036_role_menu_permissions
Create Date: 2026-07-20
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0037_custom_role"
down_revision = "0036_role_menu_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = sa.table(
        "role_menu_permissions",
        sa.column("role", sa.String()),
        sa.column("menu_keys", sa.JSON()),
        sa.column("updated_by", sa.String()),
        sa.column("updated_at", sa.DateTime()),
    )
    bind = op.get_bind()
    exists = bind.execute(
        sa.select(table.c.role).where(table.c.role == "custom"),
    ).first()
    if not exists:
        op.bulk_insert(table, [{
            "role": "custom",
            "menu_keys": ["overview"],
            "updated_by": None,
            "updated_at": datetime.now(timezone.utc),
        }])


def downgrade() -> None:
    op.execute("UPDATE users SET role = 'viewer' WHERE role = 'custom'")
    op.execute("DELETE FROM role_menu_permissions WHERE role = 'custom'")
