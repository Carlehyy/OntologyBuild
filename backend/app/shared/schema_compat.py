"""Small, explicit compatibility repairs for databases created by ``create_all``.

Development SQLite databases historically have no Alembic revision.  SQLAlchemy's
``create_all`` creates new tables, but it cannot add columns to tables that already
exist.  Keep the repair list deliberately narrow: every entry must also have a real
Alembic migration for production deployments.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Connection, MetaData, inspect, text


CRITICAL_SCHEMA_TABLES = (
    "super_assistant_mcp_servers",
    "minio_config",
    "minio_operation_audits",
    "v2_dataset_versions",
)

_DEVELOPMENT_COLUMN_REPAIRS = (
    (
        "v2_steward_conversations",
        "context_summary",
        "ALTER TABLE v2_steward_conversations "
        "ADD COLUMN context_summary TEXT NOT NULL DEFAULT ''",
    ),
    (
        "v2_steward_conversations",
        "summary_message_count",
        "ALTER TABLE v2_steward_conversations "
        "ADD COLUMN summary_message_count INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "v2_steward_conversations",
        "working_memory",
        "ALTER TABLE v2_steward_conversations "
        "ADD COLUMN working_memory JSON NOT NULL DEFAULT '{}'",
    ),
    (
        "v2_steward_conversations",
        "context_stats",
        "ALTER TABLE v2_steward_conversations "
        "ADD COLUMN context_stats JSON NOT NULL DEFAULT '{}'",
    ),
    (
        "super_assistant_mcp_servers",
        "command",
        "ALTER TABLE super_assistant_mcp_servers "
        "ADD COLUMN command VARCHAR(1000)",
    ),
    (
        "super_assistant_mcp_servers",
        "args",
        "ALTER TABLE super_assistant_mcp_servers "
        "ADD COLUMN args JSON NOT NULL DEFAULT '[]'",
    ),
    (
        "super_assistant_mcp_servers",
        "env_encrypted",
        "ALTER TABLE super_assistant_mcp_servers "
        "ADD COLUMN env_encrypted TEXT",
    ),
    (
        "super_assistant_mcp_servers",
        "env_names",
        "ALTER TABLE super_assistant_mcp_servers "
        "ADD COLUMN env_names JSON NOT NULL DEFAULT '[]'",
    ),
    (
        "super_assistant_mcp_servers",
        "builtin_key",
        "ALTER TABLE super_assistant_mcp_servers "
        "ADD COLUMN builtin_key VARCHAR(50)",
    ),
    (
        "v2_dataset_versions",
        "data_blob",
        {
            "postgresql": (
                "ALTER TABLE v2_dataset_versions ADD COLUMN data_blob BYTEA"),
            "default": (
                "ALTER TABLE v2_dataset_versions ADD COLUMN data_blob BLOB"),
        },
    ),
    (
        "v2_dataset_versions",
        "data_size",
        "ALTER TABLE v2_dataset_versions ADD COLUMN data_size BIGINT",
    ),
)


def repair_development_schema(connection: Connection) -> list[str]:
    """Apply safe additive repairs needed by legacy, unversioned dev databases."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    repaired: list[str] = []

    columns_by_table: dict[str, set[str]] = {}
    for table_name, column_name, statement_spec in _DEVELOPMENT_COLUMN_REPAIRS:
        if table_name not in tables:
            continue
        columns = columns_by_table.setdefault(
            table_name,
            {
                column["name"]
                for column in inspector.get_columns(table_name)
            },
        )
        if column_name in columns:
            continue
        statement = (
            statement_spec.get(
                connection.dialect.name, statement_spec["default"])
            if isinstance(statement_spec, dict)
            else statement_spec
        )
        connection.execute(text(statement))
        connection.commit()
        columns.add(column_name)
        repaired.append(f"{table_name}.{column_name}")

    return repaired


def missing_model_schema(
    connection: Connection,
    metadata: MetaData,
    table_names: Iterable[str],
) -> list[str]:
    """Return missing model tables/columns in a stable, human-readable form."""
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    missing: list[str] = []

    for table_name in table_names:
        model_table = metadata.tables.get(table_name)
        if model_table is None:
            raise ValueError(f"ORM metadata does not contain table {table_name!r}")
        if table_name not in existing_tables:
            missing.append(table_name)
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        missing.extend(
            f"{table_name}.{column.name}"
            for column in model_table.columns
            if column.name not in existing_columns
        )

    return missing


def assert_critical_schema(connection: Connection, metadata: MetaData) -> None:
    """Fail during startup with an actionable error instead of request-time 500s."""
    missing = missing_model_schema(connection, metadata, CRITICAL_SCHEMA_TABLES)
    if missing:
        raise RuntimeError(
            "数据库 Schema 与当前模型不一致，缺少: "
            + ", ".join(missing)
            + "。请在 backend 目录执行 `alembic upgrade head` 后重启服务。"
        )
