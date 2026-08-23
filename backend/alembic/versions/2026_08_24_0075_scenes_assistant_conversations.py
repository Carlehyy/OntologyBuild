"""scenes assistant conversation tables

三维场景「场景建模助手」（阶段二）：左侧对话生成/修改草稿态场景。
新增两张表——会话与消息。会话可先于场景存在（从零新建模式，
scene_id 为空，首次应用定义时绑定）；消息记录 assistant 应用的
版本号，支撑右侧版本管理面板的联动回溯。

不涉及菜单权限变化（沿用 scenes 一级 key）。LLM 调用复用
app/model_configs 的选择器与解密通道，不在本域存储任何密钥。

Revision ID: 0075_scenes_assistant_conversations
Revises: 0074_scenes_domain
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0075_scenes_assistant_conversations"
down_revision = "0074_scenes_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())

    if "scene_conversations" not in tables:
        op.create_table(
            "scene_conversations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("scene_id", sa.String(), nullable=True),
            sa.Column("title", sa.String(length=200), nullable=True),
            sa.Column("model_config_id", sa.String(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["scene_id"], ["scenes.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_scene_conversations_scene",
            "scene_conversations",
            ["scene_id"],
        )

    if "scene_messages" not in tables:
        op.create_table(
            "scene_messages",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("role", sa.String(length=10), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["conversation_id"],
                ["scene_conversations.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_scene_messages_conversation",
            "scene_messages",
            ["conversation_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())

    if "scene_messages" in tables:
        op.drop_index(
            "ix_scene_messages_conversation", table_name="scene_messages")
        op.drop_table("scene_messages")
    if "scene_conversations" in tables:
        op.drop_index(
            "ix_scene_conversations_scene", table_name="scene_conversations")
        op.drop_table("scene_conversations")
