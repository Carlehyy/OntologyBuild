"""palace folders: first-class directory rows

记忆宫殿目录升级为一等公民：新增 super_assistant_palace_folders 表，
支持空目录常驻与整目录移动/重命名；存量文件的 folder_path（含全部中间
目录）回填为目录行，迁移前后树形展示一致。

Revision ID: 0094_palace_folders
Revises: 0093_super_assistant_multica
Create Date: 2026-09-05
"""

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0094_palace_folders"
down_revision = "0093_super_assistant_multica"
branch_labels = None
depends_on = None

_TABLE = "super_assistant_palace_folders"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "super_assistant_palace_files" not in set(inspector.get_table_names()):
        # 与 0089/0090 同一防御口径：部分迁移测试场景只手工建被测表并 stamp
        # 到中间版本；此时跳过 DDL，真实库与全量 upgrade 正常执行。
        return
    if _TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("path", sa.String(length=500), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("owner_id", "path", name="uq_sa_palace_folders_owner_path"),
        )
        # 与模型 index=True 的约定名一致：全新库由迁移 0003 的
        # create_all 建表（本迁移守卫跳过），存量库由此处建表。
        op.create_index("ix_super_assistant_palace_folders_owner_id", _TABLE, ["owner_id"])

    # 回填：存量文件的目录（含全部中间目录）落为目录行；回填前树形仅由
    # 文件路径派生，回填后展示不变。
    seen: set[tuple[str, str]] = set()
    rows = bind.execute(
        sa.text(
            "SELECT DISTINCT owner_id, folder_path FROM super_assistant_palace_files "
            "WHERE folder_path <> ''"
        )
    ).fetchall()
    now = datetime.now(timezone.utc)
    for owner_id, folder_path in rows:
        parts = [segment for segment in str(folder_path or "").split("/") if segment]
        for depth in range(1, len(parts) + 1):
            path = "/".join(parts[:depth])
            key = (str(owner_id), path)
            if key in seen:
                continue
            seen.add(key)
            bind.execute(
                sa.text(
                    f"INSERT INTO {_TABLE} (id, owner_id, path, created_at, updated_at) "
                    "VALUES (:id, :owner_id, :path, :created_at, :updated_at)"
                ),
                {"id": str(uuid.uuid4()), "owner_id": str(owner_id), "path": path,
                 "created_at": now, "updated_at": now},
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if _TABLE in set(inspector.get_table_names()):
        op.drop_index("ix_super_assistant_palace_folders_owner_id", table_name=_TABLE)
        op.drop_table(_TABLE)
