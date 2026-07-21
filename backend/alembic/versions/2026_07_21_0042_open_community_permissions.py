"""add open community menu permissions

Revision ID: 0042_open_community_permissions
Revises: 0041_merge_minio_files
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0042_open_community_permissions"
down_revision = "0041_merge_minio_files"
branch_labels = None
depends_on = None


COMMUNITY_MENU_KEYS = [
    "community",
    "community.skills",
    "community.plugins",
]


def upgrade() -> None:
    bind = op.get_bind()
    if "role_menu_permissions" not in sa_inspect(bind).get_table_names():
        return

    table = sa.table(
        "role_menu_permissions",
        sa.column("role", sa.String()),
        sa.column("menu_keys", sa.JSON()),
    )
    rows = bind.execute(
        sa.select(table.c.role, table.c.menu_keys).where(
            table.c.role.in_(("editor", "viewer", "custom")),
        ),
    ).mappings()
    for row in rows:
        keys = list(row["menu_keys"] or [])
        # MCP management was previously exposed from Super Assistant. Preserve
        # that exact authority on upgrade without broadening custom roles that
        # deliberately had Super Assistant removed.
        if "super_assistant" not in keys:
            continue
        for key in COMMUNITY_MENU_KEYS:
            if key not in keys:
                keys.append(key)
        bind.execute(
            table.update().where(table.c.role == row["role"]).values(menu_keys=keys),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "role_menu_permissions" not in sa_inspect(bind).get_table_names():
        return

    table = sa.table(
        "role_menu_permissions",
        sa.column("role", sa.String()),
        sa.column("menu_keys", sa.JSON()),
    )
    rows = bind.execute(
        sa.select(table.c.role, table.c.menu_keys).where(
            table.c.role.in_(("editor", "viewer", "custom")),
        ),
    ).mappings()
    for row in rows:
        keys = [key for key in (row["menu_keys"] or []) if key not in COMMUNITY_MENU_KEYS]
        bind.execute(
            table.update().where(table.c.role == row["role"]).values(menu_keys=keys),
        )
