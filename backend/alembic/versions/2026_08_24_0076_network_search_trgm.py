"""add pg_trgm search indexes for ontology-network instance keyword lookup

Revision ID: 0076_network_search_trgm
Revises: 0075_scenes_assistant_conversations
"""

from alembic import op
from sqlalchemy import inspect

revision = "0076_network_search_trgm"
down_revision = "0075_scenes_assistant_conversations"
branch_labels = None
depends_on = None

TABLE = "fo_object_instances"
# (index 名, 建索引表达式)。properties/computed 是 JSON 列，取其 text 投影
# 做 trgm 索引，与查询里的 CAST(… AS TEXT) 表达式严格一致才会被规划器命中。
INDEXES = (
    ("ix_fo_object_instances_id_trgm", "id"),
    ("ix_fo_object_instances_external_id_trgm", "external_id"),
    ("ix_fo_object_instances_properties_trgm", "(properties::text)"),
    ("ix_fo_object_instances_computed_trgm", "(computed::text)"),
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _existing_indexes() -> set[str]:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return set()
    return {
        index["name"] for index in inspector.get_indexes(TABLE) if index.get("name")
    }


def upgrade() -> None:
    # pg_trgm 是 PostgreSQL 专有扩展；迁移测试跑在 sqlite 上，非 PG 方言无操作，
    # 生产 PostgreSQL 由本迁移补齐索引。
    if not _is_postgres():
        return
    context = op.get_context()
    with context.autocommit_block():
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    existing = _existing_indexes()
    for name, expression in INDEXES:
        if name in existing:
            continue
        # 大表（上亿实例）上建索引必须并发，避免锁死灌数写入。
        with context.autocommit_block():
            op.execute(
                f"CREATE INDEX CONCURRENTLY {name} ON {TABLE} "
                f"USING gin ({expression} gin_trgm_ops)"
            )


def downgrade() -> None:
    if not _is_postgres():
        return
    context = op.get_context()
    existing = _existing_indexes()
    for name, _expression in reversed(INDEXES):
        if name not in existing:
            continue
        with context.autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    # 不删除 pg_trgm 扩展：共享资源，其他索引/未来功能可能依赖。
