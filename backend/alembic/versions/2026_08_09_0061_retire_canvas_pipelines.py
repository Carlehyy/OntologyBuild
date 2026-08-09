"""archive legacy canvas/route pipelines after canvas engine retirement

Revision ID: 0061_retire_canvas_pipelines
Revises: 0060_pipeline_run_indexes

平台下线「系统自定义」（canvas 画布）流水线与更早一代 route A/B/C
转换流水线，只保留 n8n / python 两种采集引擎。engine 不是独立列，
存于 v2_pipelines.definition JSON 的 engine 键；缺省/""/"canvas"
历史上都被当作画布引擎。本迁移把不符合 n8n/python 口径的存量
流水线统一归档（status=archived、enabled=false）：保留发布版本与
运行记录的审计链，同时让任务池调度与链式触发不再执行它们。
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0061_retire_canvas_pipelines"
down_revision = "0060_pipeline_run_indexes"
branch_labels = None
depends_on = None


RETAINED_ENGINES = {"n8n", "python"}


def upgrade() -> None:
    connection = op.get_bind()
    pipelines = sa.table(
        "v2_pipelines",
        sa.column("id", sa.String()),
        sa.column("definition", sa.JSON()),
        sa.column("status", sa.String()),
        sa.column("enabled", sa.Boolean()),
        sa.column("updated_at", sa.DateTime()),
    )

    rows = connection.execute(
        sa.select(pipelines.c.id, pipelines.c.definition),
    ).all()

    now = datetime.now(timezone.utc)
    for pipeline_id, definition in rows:
        engine = (definition or {}).get("engine")
        if engine in RETAINED_ENGINES:
            continue
        connection.execute(
            pipelines.update()
            .where(pipelines.c.id == pipeline_id)
            .values(status="archived", enabled=False, updated_at=now),
        )


def downgrade() -> None:
    # 归档前的引擎信息已随能力下线失去意义，无法区分哪些行是被本迁移
    # 归档的、哪些是用户此前自行归档的，因此 downgrade 有意不回滚。
    pass
