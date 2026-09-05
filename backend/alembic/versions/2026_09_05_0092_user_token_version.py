"""users.token_version 会话吊销代数

JWT 增加 ver claim，与 users.token_version 不一致即 401；改密（自助或
管理员重置）时 +1 吊销全部已签发 token。存量库补列默认 0，存量 token
无 ver claim 按校验侧约定视为 0，升级不强制全员重新登录。

部署影响：全新库经迁移 0003 create_all 已含该列，本迁移守卫跳过；存量
库补列 server_default '0'；回滚删列不损数据；运维零动作。

Revision ID: 0092_user_token_version
Revises: 0091_drop_ingest_key_plaintext
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "0092_user_token_version"
down_revision = "0091_drop_ingest_key_plaintext"
branch_labels = None
depends_on = None


def _columns(bind) -> set[str]:
    return {
        column["name"]
        for column in sa_inspect(bind).get_columns("users")
    }


def upgrade() -> None:
    bind = op.get_bind()
    if not sa_inspect(bind).has_table("users"):
        return
    if "token_version" not in _columns(bind):
        op.add_column(
            "users",
            sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa_inspect(bind).has_table("users"):
        return
    if "token_version" in _columns(bind):
        op.drop_column("users", "token_version")
