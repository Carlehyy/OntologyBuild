"""add persistent model connection test state

Revision ID: 0023_model_test_state
Revises: 0022_manual_share_tokens
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
import json


revision = "0023_model_test_state"
down_revision = "0022_manual_share_tokens"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    inspector = sa_inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def _index_exists(table: str, index: str) -> bool:
    inspector = sa_inspect(op.get_bind())
    return index in {item["name"] for item in inspector.get_indexes(table)}


def upgrade() -> None:
    if not _column_exists("model_configs", "last_test_status"):
        op.add_column("model_configs", sa.Column("last_test_status", sa.String(20), nullable=True))
    if not _column_exists("model_configs", "last_tested_at"):
        op.add_column("model_configs", sa.Column("last_tested_at", sa.DateTime(), nullable=True))
    if not _column_exists("model_configs", "last_test_message"):
        op.add_column("model_configs", sa.Column("last_test_message", sa.String(500), nullable=True))
    # 隔离历史上由宽松校验留下的明显无效 LLM 配置。只停用，不删除。
    conn = op.get_bind()
    legacy_rows = conn.execute(sa.text(
        "SELECT id, config_type, api_base, models FROM model_configs WHERE enabled = true"
    )).fetchall()
    for model_id, config_type, api_base, models in legacy_rows:
        if (config_type or "llm") != "llm":
            continue
        parsed_models = models
        if isinstance(parsed_models, str):
            try:
                parsed_models = json.loads(parsed_models)
            except (TypeError, ValueError):
                parsed_models = []
        has_model = isinstance(parsed_models, list) and any(str(item).strip() for item in parsed_models)
        base = str(api_base or "").strip()
        invalid_base = bool(base) and not base.startswith(("http://", "https://"))
        if not has_model or invalid_base:
            reason = "迁移停用：缺少模型名" if not has_model else "迁移停用：API Base 不是有效的 HTTP(S) 地址"
            conn.execute(sa.text(
                "UPDATE model_configs SET enabled = false, is_default = false, "
                "last_test_status = 'error', last_test_message = :reason WHERE id = :id"
            ), {"id": model_id, "reason": reason})
    # 先修复历史脏数据，再用数据库约束兜住并发设置默认模型的竞态。
    default_ids = [row[0] for row in conn.execute(sa.text(
        "SELECT id FROM model_configs "
        "WHERE config_type = 'llm' AND is_default = true "
        "ORDER BY updated_at DESC"
    )).fetchall()]
    for duplicate_id in default_ids[1:]:
        conn.execute(
            sa.text("UPDATE model_configs SET is_default = false WHERE id = :id"),
            {"id": duplicate_id},
        )
    if not _index_exists("model_configs", "uq_model_configs_default_llm"):
        op.create_index(
            "uq_model_configs_default_llm",
            "model_configs",
            ["is_default"],
            unique=True,
            postgresql_where=sa.text("is_default = true AND config_type = 'llm'"),
            sqlite_where=sa.text("is_default = 1 AND config_type = 'llm'"),
        )


def downgrade() -> None:
    if _index_exists("model_configs", "uq_model_configs_default_llm"):
        op.drop_index("uq_model_configs_default_llm", table_name="model_configs")
    if _column_exists("model_configs", "last_test_message"):
        op.drop_column("model_configs", "last_test_message")
    if _column_exists("model_configs", "last_tested_at"):
        op.drop_column("model_configs", "last_tested_at")
    if _column_exists("model_configs", "last_test_status"):
        op.drop_column("model_configs", "last_test_status")
