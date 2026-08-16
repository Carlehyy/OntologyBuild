"""world model services registry menu key backfill

世界模型新增「推演服务」子目录（服务注册表页），新增菜单 key：
world_model.services（父 key 仍为 world_model）。

存量角色平滑迁移：凡已持有 world_model 组任意键（父 key 或任一子
key）的角色，自动补授 world_model.services，保证升级后老用户对
世界模型域的新增子项可见性一致；未持有世界模型域的角色不受影响。

Revision ID: 0071_world_model_services_menu_key
Revises: 0070_pipeline_task_incremental_cursor
Create Date: 2026-08-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0071_world_model_services_menu_key"
down_revision = "0070_pipeline_task_incremental_cursor"
branch_labels = None
depends_on = None

_SERVICES_KEY = "world_model.services"
_GROUP_KEYS = ("world_model", "world_model.models", "world_model.calls")


def _backfill_services_key(bind) -> None:
    role_menu = sa.table(
        "role_menu_permissions",
        sa.column("role", sa.String),
        sa.column("menu_keys", sa.JSON),
    )
    rows = bind.execute(
        sa.select(role_menu.c.role, role_menu.c.menu_keys)
    ).fetchall()
    for role, menu_keys in rows:
        keys = list(menu_keys or [])
        if not any(key in keys for key in _GROUP_KEYS):
            continue
        if _SERVICES_KEY not in keys:
            keys.append(_SERVICES_KEY)
            bind.execute(
                role_menu.update()
                .where(role_menu.c.role == role)
                .values(menu_keys=keys)
            )


def _remove_services_key(bind) -> None:
    role_menu = sa.table(
        "role_menu_permissions",
        sa.column("role", sa.String),
        sa.column("menu_keys", sa.JSON),
    )
    rows = bind.execute(
        sa.select(role_menu.c.role, role_menu.c.menu_keys)
    ).fetchall()
    for role, menu_keys in rows:
        keys = list(menu_keys or [])
        if _SERVICES_KEY in keys:
            keys.remove(_SERVICES_KEY)
            bind.execute(
                role_menu.update()
                .where(role_menu.c.role == role)
                .values(menu_keys=keys)
            )


def upgrade() -> None:
    bind = op.get_bind()
    _backfill_services_key(bind)


def downgrade() -> None:
    bind = op.get_bind()
    _remove_services_key(bind)
