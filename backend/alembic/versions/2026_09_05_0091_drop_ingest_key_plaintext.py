"""event_ingest_keys 不再留存明文密钥

商用加固：密钥明文仅创建时一次性返回，落库只存 sha256（key_hash）。
删除存量库的 secret_plain 列；全新库经迁移 0003 的 create_all 按最新
模型建表、本迁移守卫跳过。反转早期「内部平台留存明文便于反复复制」
的权衡（审计决策：明文副本使 DB/备份泄露直接暴露全部有效密钥）。

Revision ID: 0091_drop_ingest_key_plaintext
Revises: 0090_palace_folder_path
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "0091_drop_ingest_key_plaintext"
down_revision = "0090_palace_folder_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not sa_inspect(bind).has_table("event_ingest_keys"):
        return
    existing = {
        column["name"]
        for column in sa_inspect(bind).get_columns("event_ingest_keys")
    }
    if "secret_plain" in existing:
        op.drop_column("event_ingest_keys", "secret_plain")


def downgrade() -> None:
    # 明文不恢复：仅保证迁移链可回放，列以空值形态补回
    op.add_column(
        "event_ingest_keys",
        sa.Column("secret_plain", sa.String(length=120), nullable=True),
    )
