"""tickets: category + page_url (工单分类与提交页面地址)

工单体验迭代：提交表单增加分类单选（系统故障/体验优化/新增功能/其他），
并默认记录用户提交工单时所在页面的完整地址（含 hash 路由）供审查定位。
存量工单分类回填为 other。

Revision ID: 0083_tickets_category_page
Revises: 0082_tickets
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0083_tickets_category_page"
down_revision = "0082_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa_inspect(bind).get_columns("tickets")}

    if "category" not in columns:
        # server_default 兼容存量行（回填 other），新行由应用层写入显式分类
        op.add_column(
            "tickets",
            sa.Column("category", sa.String(length=50), nullable=False,
                      server_default="other"),
        )
    if "page_url" not in columns:
        op.add_column(
            "tickets",
            sa.Column("page_url", sa.String(length=1000), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("tickets", "page_url")
    op.drop_column("tickets", "category")
