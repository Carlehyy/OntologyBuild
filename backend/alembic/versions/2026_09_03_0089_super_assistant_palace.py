"""super assistant memory palace: user file library + graph build runs

记忆宫殿（超级助手用户级长期知识记忆，参照 semantica 核心管线原生实现）：

- 新表 `super_assistant_palace_files`：用户级文件库登记行。文件本体与
  解析文本存本地工作区（SessionWorkspace、独立根目录），本表持有抽取
  状态机 pending → building → built/failed 与图谱计数。
- 新表 `super_assistant_palace_builds`：图谱抽取任务执行记录，作为
  NATS 消费侧幂等锚点（同 (file_id, content_hash) 成功即跳过）。

图谱实体与关系存 Neo4j（新标签 PalaceEntity / RELATED，owner_id 属性
隔离），无本库 DDL；索引由启动时 index_setup 幂等创建。

Revision ID: 0089_super_assistant_palace
Revises: 0088_privacy_vars
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0089_super_assistant_palace"
down_revision = "0088_privacy_vars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    # 与 0088 同一防御口径：部分迁移测试场景只手工建被测表并 stamp 到中间
    # 版本，users 表可能不存在；此时跳过 DDL，真实库与全量 upgrade 正常执行。
    if "users" not in tables:
        return

    if "super_assistant_palace_files" not in tables:
        op.create_table(
            "super_assistant_palace_files",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("artifact_id", sa.String(length=64), nullable=False),
            sa.Column("mime_type", sa.String(length=120), nullable=False),
            sa.Column("size", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("extracted_chars", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("entity_count", sa.Integer(), nullable=False),
            sa.Column("relation_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_super_assistant_palace_files_owner_id",
            "super_assistant_palace_files",
            ["owner_id"],
        )
        op.create_index(
            "ix_sa_palace_files_owner_updated",
            "super_assistant_palace_files",
            ["owner_id", "updated_at"],
        )

    if "super_assistant_palace_builds" not in tables:
        op.create_table(
            "super_assistant_palace_builds",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("file_id", sa.String(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("chunk_count", sa.Integer(), nullable=False),
            sa.Column("entity_count", sa.Integer(), nullable=False),
            sa.Column("relation_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["file_id"], ["super_assistant_palace_files.id"], ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_super_assistant_palace_builds_owner_id",
            "super_assistant_palace_builds",
            ["owner_id"],
        )
        op.create_index(
            "ix_super_assistant_palace_builds_file_id",
            "super_assistant_palace_builds",
            ["file_id"],
        )
        op.create_index(
            "ix_sa_palace_builds_file_created",
            "super_assistant_palace_builds",
            ["file_id", "created_at"],
        )
        op.create_index(
            "ix_sa_palace_builds_owner_created",
            "super_assistant_palace_builds",
            ["owner_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "super_assistant_palace_builds" in tables:
        op.drop_index("ix_sa_palace_builds_owner_created", table_name="super_assistant_palace_builds")
        op.drop_index("ix_sa_palace_builds_file_created", table_name="super_assistant_palace_builds")
        op.drop_index("ix_super_assistant_palace_builds_file_id", table_name="super_assistant_palace_builds")
        op.drop_index("ix_super_assistant_palace_builds_owner_id", table_name="super_assistant_palace_builds")
        op.drop_table("super_assistant_palace_builds")
    if "super_assistant_palace_files" in tables:
        op.drop_index("ix_sa_palace_files_owner_updated", table_name="super_assistant_palace_files")
        op.drop_index("ix_super_assistant_palace_files_owner_id", table_name="super_assistant_palace_files")
        op.drop_table("super_assistant_palace_files")
