"""world model top-level nav: services table + menu key migration

世界模型从「本体管理」子项提升为一级导航（手误纠偏）：
- 菜单 key 体系从 ontologies.library / ontologies.world_model 迁移为
  world_model（父）+ world_model.models / world_model.calls（子），
  本体管理恢复为单项（ontologies 直挂）。
- 存量角色平滑迁移：持有 ontologies.world_model 的角色改授
  world_model 全家；ontologies.library 剥除（ontologies 保留）。
- 新建 world_model_services 表：推演服务一等实体（端点、状态、
  本体语义注册字段），一期建表落位，二期发布动作直接写入。
- world_model_call_records 增加 service_id 外键（调用记录挂服务）。

Revision ID: 0066_world_model_top_level
Revises: 0065_world_model
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0066_world_model_top_level"
down_revision = "0065_world_model"
branch_labels = None
depends_on = None

_OLD_WORLD_MODEL_KEY = "ontologies.world_model"
_OLD_LIBRARY_KEY = "ontologies.library"
_ONTOLOGIES_KEY = "ontologies"
_NEW_GROUP_KEY = "world_model"
_NEW_CHILD_KEYS = ("world_model.models", "world_model.calls")


def _migrate_menu_keys_forward(bind) -> None:
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
        if _OLD_LIBRARY_KEY in keys:
            keys.remove(_OLD_LIBRARY_KEY)
            updated = True
        if _OLD_WORLD_MODEL_KEY in keys:
            keys.remove(_OLD_WORLD_MODEL_KEY)
            for new_key in (_NEW_GROUP_KEY, *_NEW_CHILD_KEYS):
                if new_key not in keys:
                    keys.append(new_key)
            updated = True
        if updated:
            bind.execute(
                role_menu.update()
                .where(role_menu.c.role == role)
                .values(menu_keys=keys)
            )


def _migrate_menu_keys_backward(bind) -> None:
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
        if _NEW_GROUP_KEY in keys or any(k in keys for k in _NEW_CHILD_KEYS):
            keys = [k for k in keys
                    if k != _NEW_GROUP_KEY and k not in _NEW_CHILD_KEYS]
            if _OLD_WORLD_MODEL_KEY not in keys:
                keys.append(_OLD_WORLD_MODEL_KEY)
            updated = True
        # 恢复 0065 的语义：持有 ontologies 的角色回补两个旧子 key 中的 library
        if _ONTOLOGIES_KEY in keys and _OLD_LIBRARY_KEY not in keys:
            keys.append(_OLD_LIBRARY_KEY)
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

    if "world_model_services" not in tables:
        op.create_table(
            "world_model_services",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("version_id", sa.String(), nullable=True),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "status", sa.String(length=20), nullable=False,
                server_default="draft",
            ),
            sa.Column("endpoint_path", sa.String(length=200), nullable=True),
            sa.Column("applicable_object_types", sa.JSON(), nullable=True),
            sa.Column("preconditions", sa.JSON(), nullable=True),
            sa.Column("input_mapping", sa.JSON(), nullable=True),
            sa.Column("output_mapping", sa.JSON(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["project_id"], ["world_model_projects.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["version_id"], ["world_model_script_versions.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_world_model_services_project",
            "world_model_services",
            ["project_id"],
        )
        op.create_index(
            "ix_world_model_services_status",
            "world_model_services",
            ["status"],
        )

    if "world_model_call_records" in tables:
        call_record_columns = {
            column["name"]
            for column in inspector.get_columns("world_model_call_records")
        }
        if "service_id" not in call_record_columns:
            # SQLite 不支持 ALTER ADD CONSTRAINT，用 batch（copy-and-move）模式
            # 加列并挂外键；PostgreSQL 下等价于常规 ALTER
            with op.batch_alter_table("world_model_call_records") as batch_op:
                batch_op.add_column(sa.Column("service_id", sa.String(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_world_model_call_records_service",
                    "world_model_services",
                    ["service_id"],
                    ["id"],
                    ondelete="SET NULL",
                )

    if "role_menu_permissions" in tables:
        _migrate_menu_keys_forward(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "role_menu_permissions" in tables:
        _migrate_menu_keys_backward(bind)

    if "world_model_call_records" in tables:
        call_record_columns = {
            column["name"]
            for column in inspector.get_columns("world_model_call_records")
        }
        if "service_id" in call_record_columns:
            # batch 重建表会连带移除该列上的外键，无需单独 drop_constraint
            # （SQLite 无法在元数据中按名字反射 FK）
            with op.batch_alter_table("world_model_call_records") as batch_op:
                batch_op.drop_column("service_id")

    if "world_model_services" in tables:
        op.drop_index(
            "ix_world_model_services_status",
            table_name="world_model_services",
        )
        op.drop_index(
            "ix_world_model_services_project",
            table_name="world_model_services",
        )
        op.drop_table("world_model_services")
