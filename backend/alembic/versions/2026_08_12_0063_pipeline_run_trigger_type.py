"""pipeline run trigger_type as a real indexed column

Revision ID: 0063_pipeline_run_trigger_type
Revises: 0062_curated_lake_tables
Create Date: 2026-08-12

全局执行历史按触发方式（manual|scheduled）过滤此前只能匹配
``stats['trigger_type']`` JSON 键——无索引可用，历史积累后退化为全表扫描。
本迁移为 v2_pipeline_runs 增加真实列 trigger_type（nullable String）与索引，
并从 stats JSON 回填存量行：NULL 与 stats 缺键同义，过滤口径统一按
manual 处理（stats JSON 键保留不动，HTTP 契约不变）。

回填按主键序分批读取、分批提交，避免存量大表回填拖成单个长事务；
任一批失败后可安全重跑——列守卫跳过已建结构，IS NULL 过滤跳过已回填行。

迁移 0003 会对当前 Base.metadata 做 create_all：全新库在到达本迁移前
已按当前模型建好该列与索引，此处按守卫跳过重复创建，回填自然为空。
"""

from alembic import op
import sqlalchemy as sa


revision = "0063_pipeline_run_trigger_type"
down_revision = "0062_curated_lake_tables"
branch_labels = None
depends_on = None


TABLE = "v2_pipeline_runs"
COLUMN = "trigger_type"
INDEX = "ix_v2_pipeline_runs_trigger_type"
_BACKFILL_BATCH = 500


def _create_index() -> None:
    context = op.get_context()
    if op.get_bind().dialect.name == "postgresql":
        # 与 0060 同一口径：运行历史表只增不改，在线构建索引不阻塞写入。
        with context.autocommit_block():
            op.create_index(
                INDEX,
                TABLE,
                [COLUMN],
                unique=False,
                postgresql_concurrently=True,
            )
        return
    op.create_index(INDEX, TABLE, [COLUMN], unique=False)


def _drop_index() -> None:
    context = op.get_context()
    if op.get_bind().dialect.name == "postgresql":
        with context.autocommit_block():
            op.drop_index(
                INDEX,
                table_name=TABLE,
                postgresql_concurrently=True,
            )
        return
    op.drop_index(INDEX, table_name=TABLE)


def _backfill_trigger_type(bind) -> None:
    """从 stats JSON 提取 trigger_type 回填真实列，按主键序分批提交。"""
    runs = sa.table(
        TABLE,
        sa.column("id", sa.String()),
        sa.column("stats", sa.JSON()),
        sa.column(COLUMN, sa.String(length=20)),
    )
    update_stmt = (
        runs.update()
        .where(runs.c.id == sa.bindparam("rid"))
        .values(**{COLUMN: sa.bindparam("ttype")})
    )
    last_id = ""
    while True:
        rows = bind.execute(
            sa.select(runs.c.id, runs.c.stats)
            .where(runs.c.id > last_id, runs.c.trigger_type.is_(None))
            .order_by(runs.c.id)
            .limit(_BACKFILL_BATCH)
        ).all()
        if not rows:
            return
        batch = []
        for row in rows:
            stats = row.stats if isinstance(row.stats, dict) else {}
            value = stats.get("trigger_type")
            if isinstance(value, str) and value:
                batch.append({"rid": row.id, "ttype": value})
        if batch:
            bind.execute(update_stmt, batch)
        bind.commit()
        last_id = rows[-1].id


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE):
        return
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    if COLUMN not in columns:
        op.add_column(
            TABLE,
            sa.Column(COLUMN, sa.String(length=20), nullable=True),
        )
    existing_indexes = {
        index["name"]
        for index in sa.inspect(bind).get_indexes(TABLE)
        if index.get("name")
    }
    if INDEX not in existing_indexes:
        _create_index()
    _backfill_trigger_type(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE):
        return
    existing_indexes = {
        index["name"]
        for index in inspector.get_indexes(TABLE)
        if index.get("name")
    }
    if INDEX in existing_indexes:
        _drop_index()
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    if COLUMN in columns:
        op.drop_column(TABLE, COLUMN)
