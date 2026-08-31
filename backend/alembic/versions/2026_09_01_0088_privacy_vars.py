"""user privacy vars: RSA keypair + reported vars table

新增用户隐私变量能力（个人资料 → 隐私变量 tab）：

- 新表 `user_privacy_keypairs`：每用户一行 RSA 密钥对。公钥 PEM 明文
  下发给用户的上报脚本（仅加密），私钥 PEM 经平台 Fernet 再加密后落库。
- 新表 `user_privacy_vars`：每用户 N 条隐私变量。value 为用户本地脚本
  用公钥 RSA 加密上报、平台私钥解密后再 Fernet 包一层落库（双层保险）。
- `users` 新增 `report_token_encrypted` nullable 列：用户级上报 token
  （Fernet 密文），为空表示该用户尚未启用隐私变量上报；兼容存量用户。

本期仅做存储与上报，不注入任何执行链路（与 user_env_vars 同一克制边界）。

Revision ID: 0088_privacy_vars
Revises: 0087_assistant_eval_flywheel_m3
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0088_privacy_vars"
down_revision = "0087_assistant_eval_flywheel_m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    # 该迁移给 users 表加列、并建两张外键指向 users.id 的表。部分迁移测试
    # 场景（如 skill 治理迁移测试）只手工建被测表并 stamp 到中间版本，
    # users 表此时不存在；此时直接跳过本迁移的全部 DDL，避免 NoSuchTableError。
    # 真实库与全量 upgrade 链路里 users 表必然存在，会正常执行。
    if "users" not in tables:
        return

    # users 表新增 report_token_encrypted 列（兼容存量：nullable）
    existing_columns = {c["name"] for c in inspector.get_columns("users")}
    if "report_token_encrypted" not in existing_columns:
        op.add_column(
            "users",
            sa.Column("report_token_encrypted", sa.Text(), nullable=True),
        )

    if "user_privacy_keypairs" not in tables:
        op.create_table(
            "user_privacy_keypairs",
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("public_key_pem", sa.Text(), nullable=False),
            sa.Column("private_key_pem_encrypted", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id"),
        )

    if "user_privacy_vars" not in tables:
        op.create_table(
            "user_privacy_vars",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("key", sa.String(length=128), nullable=False),
            sa.Column("value_encrypted", sa.Text(), nullable=False),
            sa.Column("last_reported_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "key", name="uq_user_privacy_vars_user_key"),
        )
        op.create_index(
            "ix_user_privacy_vars_user_id",
            "user_privacy_vars",
            ["user_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "user_privacy_vars" in tables:
        op.drop_index("ix_user_privacy_vars_user_id", table_name="user_privacy_vars")
        op.drop_table("user_privacy_vars")
    if "user_privacy_keypairs" in tables:
        op.drop_table("user_privacy_keypairs")

    existing_columns = {c["name"] for c in inspector.get_columns("users")}
    if "report_token_encrypted" in existing_columns:
        op.drop_column("users", "report_token_encrypted")
