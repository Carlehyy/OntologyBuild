"""assistant widget config（悬浮 AI 助手 · 页面可见范围配置）

系统设置 → 超级助手：平台级单例表，存"隐藏名单"（左导航叶子菜单键）。
管理员勾选哪些目录显示/隐藏右下角悬浮 AI 助手；未配置（无行）或空名单
表示全部页面可见，与功能上线前行为一致；后续新增导航页面默认可见，
无需数据回填。

Revision ID: 0081_assistant_widget_config
Revises: 0080_user_env_vars
Create Date: 2026-08-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0081_assistant_widget_config"
down_revision = "0080_user_env_vars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())

    if "super_assistant_widget_config" not in tables:
        op.create_table(
            "super_assistant_widget_config",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("hidden_menu_keys", sa.JSON(), nullable=False),
            sa.Column("updated_by", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())
    if "super_assistant_widget_config" in tables:
        op.drop_table("super_assistant_widget_config")
