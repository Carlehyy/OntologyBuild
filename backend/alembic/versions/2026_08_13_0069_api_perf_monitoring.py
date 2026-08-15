"""api performance monitoring: minute rollups + slow request records

平台运行健康度（API 性能监控）新增两张表：

- api_perf_minute_rollups：按 (分钟, 方法, 路由模板, 状态类别) 聚合的
  请求计数、总耗时、最大耗时与 10 档耗时直方图桶计数（insert-only，
  查询期跨副本合并）；
- api_perf_slow_requests：超过慢阈值（默认 1000ms，环境变量
  API_PERF_SLOW_THRESHOLD_MS 可调）的单请求证据行，含 request_id、
  用户名、来源 IP、User-Agent 与 db/llm/http 分层耗时分解 JSON。

Revision ID: 0069_api_perf_monitoring
Revises: 0068_super_assistant_skill_governance
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0069_api_perf_monitoring"
down_revision = "0068_super_assistant_skill_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0003 historically bootstraps every registered model with create_all, so
    # a fresh database already carries the current metadata shape.  Guard the
    # dedicated DDL the same way 0068 does to stay idempotent.
    bind = op.get_bind()
    existing = set(sa_inspect(bind).get_table_names())
    if "api_perf_minute_rollups" in existing:
        return
    op.create_table(
        "api_perf_minute_rollups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("minute_ts", sa.DateTime(), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("route", sa.String(length=512), nullable=False),
        sa.Column("status_class", sa.String(length=8), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("max_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bucket_0", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bucket_1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bucket_2", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bucket_3", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bucket_4", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bucket_5", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bucket_6", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bucket_7", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bucket_8", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bucket_9", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_api_perf_minute_rollups_minute_ts",
        "api_perf_minute_rollups",
        ["minute_ts"],
    )

    op.create_table(
        "api_perf_slow_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("route", sa.String(length=512), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("username", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source_ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("user_agent", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("breakdown", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_api_perf_slow_requests_created_at",
        "api_perf_slow_requests",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_api_perf_slow_requests_created_at",
        table_name="api_perf_slow_requests",
    )
    op.drop_table("api_perf_slow_requests")
    op.drop_index(
        "ix_api_perf_minute_rollups_minute_ts",
        table_name="api_perf_minute_rollups",
    )
    op.drop_table("api_perf_minute_rollups")

