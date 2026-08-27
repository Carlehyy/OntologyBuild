"""user private env vars table

新增用户私有环境变量表 `user_env_vars`（MYW-56）：个人资料弹窗允许每个
用户维护自己的 key/value 环境变量。key 明文存储用于展示与 (user_id, key)
唯一约束；value 仅存 Fernet 密文（加密设施与 MCP 服务器配置一致）。

Revision ID: 0080_user_env_vars
Revises: 0079_assistant_evaluation_rubrics
Create Date: 2026-08-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0080_user_env_vars"
down_revision = "0079_assistant_evaluation_rubrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0003 historically calls current Base.metadata.create_all(), so
    # a fresh database can already contain this current ORM table before the
    # revision chain reaches 0080.  Upgraded production databases do not.
    if sa.inspect(op.get_bind()).has_table("user_env_vars"):
        return
    op.create_table(
        "user_env_vars",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key", name="uq_user_env_vars_user_key"),
    )
    op.create_index("ix_user_env_vars_user_id", "user_env_vars", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_env_vars_user_id", table_name="user_env_vars")
    op.drop_table("user_env_vars")
