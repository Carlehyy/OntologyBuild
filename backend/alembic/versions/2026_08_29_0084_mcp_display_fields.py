"""super_assistant_mcp_servers: display_name + description (插件社区 MCP 名称与描述)

插件社区 MCP 登记从单一 name（标识）扩展为标识 + 名称 + 描述：
name 保持唯一标识与工具命名空间不变，display_name 为用户可读名称，
description 为用途说明；存量行 display_name 回填为 name。

Revision ID: 0084_mcp_display_fields
Revises: 0083_tickets_category_page
Create Date: 2026-08-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0084_mcp_display_fields"
down_revision = "0083_tickets_category_page"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # 0043 的修复路径之外仍可能存在未建该表的库（ stamped 越过 0032/0043 的
    # 开发库）：表缺失时跳过，由修复迁移负责重建。
    if not sa_inspect(bind).has_table("super_assistant_mcp_servers"):
        return
    columns = {c["name"] for c in sa_inspect(bind).get_columns("super_assistant_mcp_servers")}

    if "display_name" not in columns:
        # server_default 兼容存量行（回填空串），随后回填为标识保持列表可读
        op.add_column(
            "super_assistant_mcp_servers",
            sa.Column("display_name", sa.String(length=200), nullable=False,
                      server_default=""),
        )
        op.execute(
            "UPDATE super_assistant_mcp_servers "
            "SET display_name = name WHERE display_name = ''"
        )
    if "description" not in columns:
        op.add_column(
            "super_assistant_mcp_servers",
            sa.Column("description", sa.String(length=500), nullable=False,
                      server_default=""),
        )


def downgrade() -> None:
    op.drop_column("super_assistant_mcp_servers", "description")
    op.drop_column("super_assistant_mcp_servers", "display_name")
