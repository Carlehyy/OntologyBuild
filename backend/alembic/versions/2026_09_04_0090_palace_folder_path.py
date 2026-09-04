"""palace files: folder_path column for tree layout

记忆宫殿文件库增加目录层级（folder_path，"/" 分隔，根目录空串）：
- 单文件上传落根目录；
- ZIP 导入以压缩包名（去扩展名）为顶层目录，保留包内相对层级；
- 存量行回填空串（根目录），行为与迁移前一致。

Revision ID: 0090_palace_folder_path
Revises: 0089_super_assistant_palace
Create Date: 2026-09-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0090_palace_folder_path"
down_revision = "0089_super_assistant_palace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "super_assistant_palace_files" not in set(inspector.get_table_names()):
        # 与 0089 同一防御口径：部分迁移测试场景只手工建被测表并 stamp 到
        # 中间版本；此时跳过 DDL，真实库与全量 upgrade 正常执行。
        return
    columns = {column["name"] for column in inspector.get_columns("super_assistant_palace_files")}
    if "folder_path" not in columns:
        op.add_column(
            "super_assistant_palace_files",
            sa.Column("folder_path", sa.String(length=500), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "super_assistant_palace_files" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("super_assistant_palace_files")}
    if "folder_path" in columns:
        op.drop_column("super_assistant_palace_files", "folder_path")
