"""scenes domain tables + role menu backfill

三维场景（白模场景管理与建模）首期：场景主体、版本冻结快照、
运行日志三张表。产物与引擎分离——场景定义是声明式 JSON，
渲染引擎在前端；后端负责状态机（draft|published，与 world_model
同构）、版本冻结与运行日志存查。

同时回填菜单权限：三维场景是全新一级菜单 key（scenes）。平台的
非 admin 默认授权语义是「常规角色保留既有全部产品区可见性」
（见 app/auth/permissions.py），因此为已初始化的 editor/viewer
角色行补授 scenes；custom 角色坚持显式授予原则不动。

Revision ID: 0074_scenes_domain
Revises: 0073_drop_agent_config
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0074_scenes_domain"
down_revision = "0073_drop_agent_config"
branch_labels = None
depends_on = None

_SCENES_MENU_KEY = "scenes"
# 非 admin 默认全量的两个常规角色；admin 硬编码全量无需回填，
# custom 坚持管理员显式授予
_DEFAULT_FULL_ROLES = ("editor", "viewer")


def _backfill_scenes_menu_key(bind) -> None:
    role_menu = sa.table(
        "role_menu_permissions",
        sa.column("role", sa.String),
        sa.column("menu_keys", sa.JSON),
    )
    rows = bind.execute(
        sa.select(role_menu.c.role, role_menu.c.menu_keys)
    ).fetchall()
    for role, menu_keys in rows:
        if role not in _DEFAULT_FULL_ROLES:
            continue
        keys = list(menu_keys or [])
        if _SCENES_MENU_KEY not in keys:
            keys.append(_SCENES_MENU_KEY)
            bind.execute(
                role_menu.update()
                .where(role_menu.c.role == role)
                .values(menu_keys=keys)
            )


def _remove_scenes_menu_key(bind) -> None:
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
        if _SCENES_MENU_KEY in keys:
            keys.remove(_SCENES_MENU_KEY)
            bind.execute(
                role_menu.update()
                .where(role_menu.c.role == role)
                .values(menu_keys=keys)
            )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "scenes" not in tables:
        op.create_table(
            "scenes",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("icon", sa.String(length=40), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("current_version_no", sa.Integer(), nullable=False),
            sa.Column("published_version_no", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_scenes_status", "scenes", ["status"])
        op.create_index("ix_scenes_updated_at", "scenes", ["updated_at"])

    if "scene_versions" not in tables:
        op.create_table(
            "scene_versions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("scene_id", sa.String(), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("definition", sa.JSON(), nullable=False),
            sa.Column("source", sa.String(length=20), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["scene_id"], ["scenes.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "scene_id", "version_no",
                name="uq_scene_versions_scene_version",
            ),
        )
        op.create_index(
            "ix_scene_versions_scene", "scene_versions", ["scene_id"]
        )

    if "scene_runtime_logs" not in tables:
        op.create_table(
            "scene_runtime_logs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("scene_id", sa.String(), nullable=False),
            sa.Column("level", sa.String(length=10), nullable=False),
            sa.Column("object_id", sa.String(length=80), nullable=True),
            sa.Column("event_key", sa.String(length=80), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["scene_id"], ["scenes.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_scene_runtime_logs_scene_occurred",
            "scene_runtime_logs",
            ["scene_id", "occurred_at"],
        )
        op.create_index(
            "ix_scene_runtime_logs_level", "scene_runtime_logs", ["level"]
        )

    # 治理迁移测试等场景会从最小库形态升级到 head：
    # 表不存在时跳过回填而不是失败
    if "role_menu_permissions" in tables:
        _backfill_scenes_menu_key(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "role_menu_permissions" in tables:
        _remove_scenes_menu_key(bind)
    if "scene_runtime_logs" in tables:
        op.drop_index(
            "ix_scene_runtime_logs_level", table_name="scene_runtime_logs")
        op.drop_index(
            "ix_scene_runtime_logs_scene_occurred",
            table_name="scene_runtime_logs",
        )
        op.drop_table("scene_runtime_logs")
    if "scene_versions" in tables:
        op.drop_index(
            "ix_scene_versions_scene", table_name="scene_versions")
        op.drop_table("scene_versions")
    if "scenes" in tables:
        op.drop_index("ix_scenes_updated_at", table_name="scenes")
        op.drop_index("ix_scenes_status", table_name="scenes")
        op.drop_table("scenes")
