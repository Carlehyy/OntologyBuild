"""add sentinel firing edge columns

Revision ID: 0003_sentinel_firing_edges
Revises: 0002_mcp_interface_configs
Create Date: 2026-07-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0003_sentinel_firing_edges"
down_revision = "0002_mcp_interface_configs"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    inspector = sa_inspect(conn)
    if not inspector.has_table(table):
        return False
    return column in [c["name"] for c in inspector.get_columns(table)]


def upgrade() -> None:
    # The original baseline predates the formal/sentinel tables and older
    # deployments relied on application startup create_all before Alembic.
    # Fresh production databases run Alembic first, so bootstrap every missing
    # model table here. create_all is additive only; later guarded migrations
    # still handle columns on existing installations.
    from app.database import Base
    from app.model_registry import import_all_models
    import_all_models()
    Base.metadata.create_all(bind=op.get_bind())
    # Sentinel 在部分精简部署中是可选子系统；即使模型注册表
    # 未创建该表，迁移也不应阻断数据管理主链路。
    if not sa_inspect(op.get_bind()).has_table("sentinel_firings"):
        return
    if not _column_exists("sentinel_firings", "entered"):
        op.add_column(
            "sentinel_firings",
            sa.Column("entered", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        )
    if not _column_exists("sentinel_firings", "left"):
        op.add_column(
            "sentinel_firings",
            sa.Column("left", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        )


def downgrade() -> None:
    if not sa_inspect(op.get_bind()).has_table("sentinel_firings"):
        return
    if _column_exists("sentinel_firings", "left"):
        op.drop_column("sentinel_firings", "left")
    if _column_exists("sentinel_firings", "entered"):
        op.drop_column("sentinel_firings", "entered")
