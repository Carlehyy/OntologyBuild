"""super assistant multica config workspace display name

multica 配置表补充 workspace_name：保存/测试连接时从 multica 回填工作区
显示名，配置弹窗下拉兜底显示名称而非裸 UUID（存量行回填空串，前端回落
显示 workspace_id，下次测试连接即补齐名称）。

Revision ID: 0095_super_assistant_multica_workspace_name
Revises: 0094_palace_folders
Create Date: 2026-09-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0095_super_assistant_multica_workspace_name"
down_revision = "0094_palace_folders"
branch_labels = None
depends_on = None

_TABLE = "super_assistant_multica_configs"
_COLUMN = "workspace_name"


def _columns(bind) -> set[str]:
    return {
        column["name"]
        for column in sa_inspect(bind).get_columns(_TABLE)
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    # 与 0089/0093 同一防御口径：部分迁移测试场景只手工建被测表并 stamp
    # 到中间版本，被测表可能不存在；此时跳过 DDL，真实库与全量 upgrade
    # 正常执行。
    if _TABLE not in tables:
        return

    if _COLUMN not in _columns(bind):
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.String(length=200), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    if _COLUMN in _columns(bind):
        op.drop_column(_TABLE, _COLUMN)
