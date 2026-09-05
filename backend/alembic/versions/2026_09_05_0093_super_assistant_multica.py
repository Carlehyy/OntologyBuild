"""super assistant multica integration config

超级助手「外部集成」首个落地：multica 每用户单行配置表
`super_assistant_multica_configs`（base_url + workspace_id + PAT 加密存储 +
启用开关 + 最近连接测试记录）。未配置/未启用时 multica 工具不进入
工具目录，/multica: 命令得到确定性引导，无数据回填需求。

Revision ID: 0093_super_assistant_multica
Revises: 0092_user_token_version
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0093_super_assistant_multica"
down_revision = "0092_user_token_version"
branch_labels = None
depends_on = None

_TABLE = "super_assistant_multica_configs"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    # 与 0089 同一防御口径：部分迁移测试场景只手工建被测表并 stamp 到中间
    # 版本，users 表可能不存在；此时跳过 DDL，真实库与全量 upgrade 正常执行。
    if "users" not in tables:
        return

    if _TABLE not in tables:
        op.create_table(
            _TABLE,
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("base_url", sa.String(length=500), nullable=False),
            sa.Column("workspace_id", sa.String(length=100), nullable=False),
            sa.Column("token_encrypted", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("last_test_status", sa.String(length=20), nullable=True),
            sa.Column("last_test_message", sa.String(length=500), nullable=True),
            sa.Column("last_tested_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("owner_id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if _TABLE in set(inspector.get_table_names()):
        op.drop_table(_TABLE)
