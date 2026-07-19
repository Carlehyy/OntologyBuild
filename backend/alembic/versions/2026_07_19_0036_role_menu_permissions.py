"""add role based menu permissions

Revision ID: 0036_role_menu_permissions
Revises: 0035_formal_instance_release_identity
Create Date: 2026-07-19
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0036_role_menu_permissions"
down_revision = "0035_formal_instance_release_identity"
branch_labels = None
depends_on = None


DEFAULT_MENU_KEYS = [
    "overview",
    "super_assistant",
    "explore",
    "ontologies",
    "agent",
    "events",
    "data",
    "data.pipelines",
    "data.sync_tasks",
    "data.structured",
    "models",
]


def upgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    if "role_menu_permissions" not in inspector.get_table_names():
        op.create_table(
            "role_menu_permissions",
            sa.Column("role", sa.String(length=20), primary_key=True),
            sa.Column("menu_keys", sa.JSON(), nullable=False),
            sa.Column("updated_by", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    table = sa.table(
        "role_menu_permissions",
        sa.column("role", sa.String()),
        sa.column("menu_keys", sa.JSON()),
        sa.column("updated_by", sa.String()),
        sa.column("updated_at", sa.DateTime()),
    )
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    for role in ("editor", "viewer"):
        exists = bind.execute(
            sa.select(table.c.role).where(table.c.role == role),
        ).first()
        if not exists:
            op.bulk_insert(table, [{
                "role": role,
                "menu_keys": DEFAULT_MENU_KEYS,
                "updated_by": None,
                "updated_at": now,
            }])

    # An early UI version wrote the unsupported value "user". Normalize it to
    # the established read-only role so every account participates in RBAC.
    if "users" in inspector.get_table_names():
        op.execute("UPDATE users SET role = 'viewer' WHERE role = 'user'")


def downgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    if "role_menu_permissions" in inspector.get_table_names():
        op.drop_table("role_menu_permissions")
