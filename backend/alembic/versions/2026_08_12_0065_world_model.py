"""world model domain tables + role menu backfill

世界模型（演化层）首期：推演模型项目、脚本版本、调用记录三张表。
调用记录本期只读，写入方随二期「发布为推演服务」落地。

同时回填菜单权限：「本体管理」从单项变为分组后，归一化规则要求持有
父 key（ontologies）的角色必须至少持有一个子 key，否则父 key 会被
剥除。这里给所有已持有 ontologies 的存量角色补 ontologies.library
（本体总览）与 ontologies.world_model（世界模型）两个子 key，保证
升级后老用户对本体管理与世界模型的可见性不变。

Revision ID: 0065_world_model
Revises: 0064_merge_0063_heads
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0065_world_model"
down_revision = "0064_merge_0063_heads"
branch_labels = None
depends_on = None

_WORLD_MODEL_MENU_KEY = "ontologies.world_model"
_LIBRARY_MENU_KEY = "ontologies.library"
_ONTOLOGIES_MENU_KEY = "ontologies"
# 「本体管理」从单项变为分组后新增的两个子 key
_CHILD_MENU_KEYS = (_LIBRARY_MENU_KEY, _WORLD_MODEL_MENU_KEY)


def _backfill_world_model_menu_key(bind) -> None:
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
        if _ONTOLOGIES_MENU_KEY not in keys:
            continue
        updated = False
        for child_key in _CHILD_MENU_KEYS:
            if child_key not in keys:
                keys.append(child_key)
                updated = True
        if updated:
            bind.execute(
                role_menu.update()
                .where(role_menu.c.role == role)
                .values(menu_keys=keys)
            )


def _remove_world_model_menu_key(bind) -> None:
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
        updated = False
        for child_key in _CHILD_MENU_KEYS:
            if child_key in keys:
                keys.remove(child_key)
                updated = True
        if updated:
            bind.execute(
                role_menu.update()
                .where(role_menu.c.role == role)
                .values(menu_keys=keys)
            )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "world_model_projects" not in tables:
        op.create_table(
            "world_model_projects",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("engine_type", sa.String(length=32), nullable=False),
            sa.Column("script", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_world_model_projects_status",
            "world_model_projects",
            ["status"],
        )
        op.create_index(
            "ix_world_model_projects_engine_type",
            "world_model_projects",
            ["engine_type"],
        )
        op.create_index(
            "ix_world_model_projects_updated_at",
            "world_model_projects",
            ["updated_at"],
        )

    if "world_model_script_versions" not in tables:
        op.create_table(
            "world_model_script_versions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("script", sa.Text(), nullable=False),
            sa.Column("test_input", sa.JSON(), nullable=True),
            sa.Column(
                "duration_ms", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["project_id"], ["world_model_projects.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "project_id", "version_no",
                name="uq_world_model_script_versions_project_version",
            ),
        )
        op.create_index(
            "ix_world_model_script_versions_project_id",
            "world_model_script_versions",
            ["project_id"],
        )

    if "world_model_call_records" not in tables:
        op.create_table(
            "world_model_call_records",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column(
                "service_name", sa.String(length=200), nullable=False,
                server_default="",
            ),
            sa.Column(
                "caller", sa.String(length=200), nullable=False,
                server_default="",
            ),
            sa.Column(
                "ok", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            sa.Column(
                "duration_ms", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("request_payload", sa.JSON(), nullable=True),
            sa.Column("response_payload", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["project_id"], ["world_model_projects.id"],
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_world_model_call_records_project",
            "world_model_call_records",
            ["project_id"],
        )
        op.create_index(
            "ix_world_model_call_records_created_at",
            "world_model_call_records",
            ["created_at"],
        )
        op.create_index(
            "ix_world_model_call_records_ok",
            "world_model_call_records",
            ["ok"],
        )

    if "role_menu_permissions" in tables:
        _backfill_world_model_menu_key(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "role_menu_permissions" in tables:
        _remove_world_model_menu_key(bind)

    if "world_model_call_records" in tables:
        op.drop_index(
            "ix_world_model_call_records_ok",
            table_name="world_model_call_records",
        )
        op.drop_index(
            "ix_world_model_call_records_created_at",
            table_name="world_model_call_records",
        )
        op.drop_index(
            "ix_world_model_call_records_project",
            table_name="world_model_call_records",
        )
        op.drop_table("world_model_call_records")

    if "world_model_script_versions" in tables:
        op.drop_index(
            "ix_world_model_script_versions_project_id",
            table_name="world_model_script_versions",
        )
        op.drop_table("world_model_script_versions")

    if "world_model_projects" in tables:
        op.drop_index(
            "ix_world_model_projects_updated_at",
            table_name="world_model_projects",
        )
        op.drop_index(
            "ix_world_model_projects_engine_type",
            table_name="world_model_projects",
        )
        op.drop_index(
            "ix_world_model_projects_status",
            table_name="world_model_projects",
        )
        op.drop_table("world_model_projects")
