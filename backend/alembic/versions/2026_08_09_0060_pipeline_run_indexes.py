"""add indexes for pipeline run history queries

Revision ID: 0060_pipeline_run_indexes
Revises: 0059_pipeline_script_versions

v2_pipeline_runs 此前没有任何二级索引：任务池列表的最近影响聚合
（task_id IN ...）、按流水线的运行回看（pipeline_id + created_at 倒序）
和统计面板的 7 天窗口都会随运行历史积累退化为全表扫描，且 stats 是
重 JSON 列。三个索引分别覆盖上述访问路径。
"""

from alembic import op
from sqlalchemy import inspect


revision = "0060_pipeline_run_indexes"
down_revision = "0059_pipeline_script_versions"
branch_labels = None
depends_on = None


TABLE = "v2_pipeline_runs"
INDEXES = {
    "ix_v2_pipeline_runs_pipeline_created": ["pipeline_id", "created_at"],
    "ix_v2_pipeline_runs_task_created": ["task_id", "created_at"],
    "ix_v2_pipeline_runs_created_at": ["created_at"],
}


def _existing_indexes() -> set[str]:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return set()
    return {
        index["name"]
        for index in inspector.get_indexes(TABLE)
        if index.get("name")
    }


def _create_index(name: str, columns: list[str]) -> None:
    context = op.get_context()
    if op.get_bind().dialect.name == "postgresql":
        # 运行历史表只增不改且可能已积累大量行；在线构建索引不阻塞写入。
        with context.autocommit_block():
            op.create_index(
                name,
                TABLE,
                columns,
                unique=False,
                postgresql_concurrently=True,
            )
        return
    op.create_index(name, TABLE, columns, unique=False)


def _drop_index(name: str) -> None:
    context = op.get_context()
    if op.get_bind().dialect.name == "postgresql":
        with context.autocommit_block():
            op.drop_index(
                name,
                table_name=TABLE,
                postgresql_concurrently=True,
            )
        return
    op.drop_index(name, table_name=TABLE)


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return
    existing = _existing_indexes()
    for name, columns in INDEXES.items():
        if name not in existing:
            _create_index(name, columns)


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return
    existing = _existing_indexes()
    for name in reversed(INDEXES):
        if name in existing:
            _drop_index(name)
