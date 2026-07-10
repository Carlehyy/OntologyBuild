"""数据管理主链路正式入迁移：n8n、任务池、统一资产 FK 与审核版本

此前这些结构部分依赖应用启动期 create_all/ALTER TABLE，导致 Alembic 空库虽能
stamp 到 head，却没有可运行的数据管理表。本迁移把运行时真实依赖收归 schema
契约，并把映射/审核统一指向 v2_datasets。

Revision ID: 0017_data_management_contract
Revises: 0016_link_projection_lineage
Create Date: 2026-07-11
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0017_data_management_contract"
down_revision = "0016_link_projection_lineage"
branch_labels = None
depends_on = None


_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _inspector():
    return sa_inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return _inspector().has_table(name)


def _columns(table: str) -> set[str]:
    return {c["name"] for c in _inspector().get_columns(table)} if _has_table(table) else set()


def _index_names(table: str) -> set[str]:
    if not _has_table(table):
        return set()
    names = {i["name"] for i in _inspector().get_indexes(table) if i.get("name")}
    names |= {u["name"] for u in _inspector().get_unique_constraints(table) if u.get("name")}
    return names


def _check_names(table: str) -> set[str]:
    if not _has_table(table):
        return set()
    return {c["name"] for c in _inspector().get_check_constraints(table) if c.get("name")}


def _ensure_column(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def _ensure_index(name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
    if name not in _index_names(table):
        op.create_index(name, table, columns, unique=unique)


def _ensure_check(table: str, name: str, expression: str) -> None:
    if name in _check_names(table):
        return
    with op.batch_alter_table(table, naming_convention=_NAMING) as batch:
        batch.create_check_constraint(name, expression)


def _fk(table: str, column: str) -> dict | None:
    for item in _inspector().get_foreign_keys(table):
        if item.get("constrained_columns") == [column]:
            return item
    return None


def _replace_fk(table: str, column: str, target: str, *, ondelete: str | None = None) -> None:
    existing = _fk(table, column)
    if existing and existing.get("referred_table") == target \
            and (existing.get("options") or {}).get("ondelete") == ondelete:
        return
    with op.batch_alter_table(table, naming_convention=_NAMING) as batch:
        if existing:
            old_name = existing.get("name") or (
                f"fk_{table}_{column}_{existing.get('referred_table')}")
            batch.drop_constraint(old_name, type_="foreignkey")
        batch.create_foreign_key(
            f"fk_{table}_{column}_{target}", target, [column], ["id"], ondelete=ondelete)


def _fail_on_duplicates(table: str, columns: list[str], label: str) -> None:
    where = " AND ".join(f"{c} IS NOT NULL" for c in columns)
    cols = ", ".join(columns)
    rows = op.get_bind().execute(sa.text(
        f"SELECT {cols}, COUNT(*) AS n FROM {table} WHERE {where} "
        f"GROUP BY {cols} HAVING COUNT(*) > 1 LIMIT 5"
    )).fetchall()
    if rows:
        raise RuntimeError(f"无法建立{label}唯一约束；请先处理重复数据：{rows}")


def _create_steward_tables() -> None:
    if not _has_table("v2_steward_conversations"):
        op.create_table(
            "v2_steward_conversations",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("title", sa.String(200), nullable=False, server_default="新对话"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        )
    else:
        op.get_bind().execute(sa.text(
            "UPDATE v2_steward_conversations SET user_id=NULL WHERE user_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id=v2_steward_conversations.user_id)"))
        _replace_fk("v2_steward_conversations", "user_id", "users", ondelete="SET NULL")
    _ensure_index("ix_v2_steward_conversations_user_id", "v2_steward_conversations", ["user_id"])

    if not _has_table("v2_n8n_pipelines"):
        op.create_table(
            "v2_n8n_pipelines",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True, server_default=""),
            sa.Column("n8n_workflow_id", sa.String(100), nullable=False),
            sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
            sa.Column("workflow_snapshot", sa.JSON(), nullable=True),
            sa.Column("last_test_result", sa.JSON(), nullable=True),
            sa.Column("pipeline_id", sa.String(), nullable=True),
            sa.Column("conversation_id", sa.String(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["pipeline_id"], ["v2_pipelines.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["conversation_id"], ["v2_steward_conversations.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.CheckConstraint("status IN ('draft','archived')", name="ck_n8n_pipelines_status"),
        )
    else:
        _ensure_column("v2_n8n_pipelines", sa.Column("last_test_result", sa.JSON(), nullable=True))
        conn = op.get_bind()
        conn.execute(sa.text(
            "UPDATE v2_n8n_pipelines SET status='draft' "
            "WHERE status IS NULL OR status NOT IN ('draft','archived')"))
        conn.execute(sa.text(
            "UPDATE v2_n8n_pipelines SET pipeline_id=NULL WHERE pipeline_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM v2_pipelines p WHERE p.id=v2_n8n_pipelines.pipeline_id)"))
        conn.execute(sa.text(
            "UPDATE v2_n8n_pipelines SET created_by=NULL WHERE created_by IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id=v2_n8n_pipelines.created_by)"))
        conn.execute(sa.text(
            "UPDATE v2_n8n_pipelines SET conversation_id=NULL WHERE conversation_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM v2_steward_conversations c "
            "WHERE c.id=v2_n8n_pipelines.conversation_id)"))
        _replace_fk("v2_n8n_pipelines", "pipeline_id", "v2_pipelines", ondelete="SET NULL")
        _replace_fk("v2_n8n_pipelines", "conversation_id", "v2_steward_conversations", ondelete="SET NULL")
        _replace_fk("v2_n8n_pipelines", "created_by", "users", ondelete="SET NULL")
        _ensure_check("v2_n8n_pipelines", "ck_n8n_pipelines_status", "status IN ('draft','archived')")
    _fail_on_duplicates("v2_n8n_pipelines", ["n8n_workflow_id"], "n8n workflow")
    _fail_on_duplicates("v2_n8n_pipelines", ["pipeline_id"], "n8n 影子流水线")
    _ensure_index("uq_n8n_pipelines_workflow", "v2_n8n_pipelines", ["n8n_workflow_id"], unique=True)
    _ensure_index("uq_n8n_pipelines_shadow_pipeline", "v2_n8n_pipelines", ["pipeline_id"], unique=True)
    _ensure_index("ix_v2_n8n_pipelines_n8n_workflow_id", "v2_n8n_pipelines", ["n8n_workflow_id"])
    _ensure_index("ix_v2_n8n_pipelines_pipeline_id", "v2_n8n_pipelines", ["pipeline_id"])
    _ensure_index("ix_v2_n8n_pipelines_status", "v2_n8n_pipelines", ["status"])

    if not _has_table("v2_steward_messages"):
        op.create_table(
            "v2_steward_messages",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("role", sa.String(20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("steps", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("touched_pipeline_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("model", sa.String(200), nullable=True),
            sa.Column("token_usage", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["conversation_id"], ["v2_steward_conversations.id"], ondelete="CASCADE"),
        )
    else:
        orphan_messages = op.get_bind().execute(sa.text(
            "SELECT id,conversation_id FROM v2_steward_messages m WHERE NOT EXISTS "
            "(SELECT 1 FROM v2_steward_conversations c WHERE c.id=m.conversation_id) LIMIT 5"
        )).fetchall()
        if orphan_messages:
            raise RuntimeError(f"数据管家消息缺少所属对话，拒绝丢弃审计血缘：{orphan_messages}")
        _replace_fk(
            "v2_steward_messages", "conversation_id",
            "v2_steward_conversations", ondelete="CASCADE")
    _ensure_index("ix_v2_steward_messages_conversation_id", "v2_steward_messages", ["conversation_id"])


def _create_pipeline_task_table() -> None:
    if not _has_table("v2_pipeline_tasks"):
        op.create_table(
            "v2_pipeline_tasks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True, server_default=""),
            sa.Column("pipeline_id", sa.String(36), nullable=False),
            sa.Column("write_mode", sa.String(20), nullable=False, server_default="overwrite"),
            sa.Column("primary_key", sa.String(200), nullable=True, server_default=""),
            sa.Column("soft_delete_column", sa.String(200), nullable=True, server_default=""),
            sa.Column("skip_empty", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("schedule_type", sa.String(20), nullable=False, server_default="MANUAL"),
            sa.Column("cron_expression", sa.String(100), nullable=True, server_default=""),
            sa.Column("interval_seconds", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("status", sa.String(20), nullable=False, server_default="idle"),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_rows", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["pipeline_id"], ["v2_pipelines.id"], ondelete="RESTRICT"),
            sa.CheckConstraint("write_mode IN ('overwrite','append','upsert','append_dedup')", name="ck_pipeline_tasks_write_mode"),
            sa.CheckConstraint("schedule_type IN ('MANUAL','CRON','INTERVAL')", name="ck_pipeline_tasks_schedule_type"),
            sa.CheckConstraint("status IN ('idle','running','success','failed')", name="ck_pipeline_tasks_status"),
        )
    else:
        orphan = op.get_bind().execute(sa.text(
            "SELECT id, pipeline_id FROM v2_pipeline_tasks t WHERE NOT EXISTS "
            "(SELECT 1 FROM v2_pipelines p WHERE p.id=t.pipeline_id) LIMIT 5"
        )).fetchall()
        if orphan:
            raise RuntimeError(f"存在关联流水线已丢失的数据任务，无法建立外键：{orphan}")
        _replace_fk("v2_pipeline_tasks", "pipeline_id", "v2_pipelines", ondelete="RESTRICT")
        _ensure_check("v2_pipeline_tasks", "ck_pipeline_tasks_write_mode", "write_mode IN ('overwrite','append','upsert','append_dedup')")
        _ensure_check("v2_pipeline_tasks", "ck_pipeline_tasks_schedule_type", "schedule_type IN ('MANUAL','CRON','INTERVAL')")
        _ensure_check("v2_pipeline_tasks", "ck_pipeline_tasks_status", "status IN ('idle','running','success','failed')")
    _ensure_index("ix_v2_pipeline_tasks_name", "v2_pipeline_tasks", ["name"])
    _ensure_index("ix_v2_pipeline_tasks_pipeline_id", "v2_pipeline_tasks", ["pipeline_id"])


def _create_legacy_sync_tables() -> None:
    """仅为存量迁移/显式停用保留；新任务统一使用 PipelineTask。"""
    if not _has_table("v2_data_sync_tasks"):
        op.create_table(
            "v2_data_sync_tasks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True, server_default=""),
            sa.Column("connection_id", sa.String(36), nullable=False),
            sa.Column("source_table", sa.String(200), nullable=True, server_default=""),
            sa.Column("source_query", sa.Text(), nullable=True, server_default=""),
            sa.Column("sync_mode", sa.String(20), nullable=False, server_default="APPEND"),
            sa.Column("primary_key", sa.String(200), nullable=True, server_default=""),
            sa.Column("watermark_column", sa.String(200), nullable=True, server_default=""),
            sa.Column("is_deleted_column", sa.String(200), nullable=True, server_default=""),
            sa.Column("schedule_type", sa.String(20), nullable=False, server_default="MANUAL"),
            sa.Column("cron_expression", sa.String(100), nullable=True, server_default=""),
            sa.Column("interval_seconds", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("trigger_pipeline_id", sa.String(36), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="idle"),
            sa.Column("last_sync_at", sa.DateTime(), nullable=True),
            sa.Column("last_rows", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True, server_default=""),
            sa.Column("last_watermark", sa.Text(), nullable=True, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["connection_id"], ["v2_connections.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["trigger_pipeline_id"], ["v2_pipelines.id"], ondelete="SET NULL"),
        )
    _ensure_index("ix_v2_data_sync_tasks_name", "v2_data_sync_tasks", ["name"])
    _ensure_index("ix_v2_data_sync_tasks_connection_id", "v2_data_sync_tasks", ["connection_id"])
    _ensure_index("idx_sync_conn", "v2_data_sync_tasks", ["connection_id"])
    _ensure_index("idx_sync_status", "v2_data_sync_tasks", ["status"])

    if not _has_table("v2_data_sync_histories"):
        op.create_table(
            "v2_data_sync_histories",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("task_id", sa.String(36), nullable=False),
            sa.Column("trigger_type", sa.String(20), nullable=False, server_default="manual"),
            sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("ended_at", sa.DateTime(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("status", sa.String(20), nullable=False, server_default="running"),
            sa.Column("source_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("inserted_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("updated_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("deleted_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True, server_default=""),
            sa.Column("watermark_before", sa.Text(), nullable=True, server_default=""),
            sa.Column("watermark_after", sa.Text(), nullable=True, server_default=""),
            sa.Column("dataset_id", sa.String(36), nullable=True),
            sa.Column("dataset_version", sa.Integer(), nullable=True, server_default="0"),
            sa.ForeignKeyConstraint(["task_id"], ["v2_data_sync_tasks.id"], ondelete="CASCADE"),
        )
    _ensure_index("ix_v2_data_sync_histories_task_id", "v2_data_sync_histories", ["task_id"])
    _ensure_index("idx_sync_history_task", "v2_data_sync_histories", ["task_id", "started_at"])
    op.get_bind().execute(sa.text("UPDATE v2_data_sync_tasks SET status=lower(status)"))
    op.get_bind().execute(sa.text("UPDATE v2_data_sync_histories SET status=lower(status)"))


def _json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return value


def _ensure_canonical_dataset_refs(table: str, column: str) -> None:
    """把旧 curated FK 引用迁到统一 v2_datasets；无法证明归属时硬失败。"""
    if not _has_table(table) or column not in _columns(table):
        return
    conn = op.get_bind()
    refs = conn.execute(sa.text(
        f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"
    )).fetchall()
    datasets = sa.table(
        "v2_datasets",
        sa.column("id", sa.String), sa.column("name", sa.String),
        sa.column("source_connection_id", sa.String), sa.column("kind", sa.String),
        sa.column("schema_json", sa.JSON), sa.column("latest_version_id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for (ref_id,) in refs:
        if conn.execute(sa.text("SELECT 1 FROM v2_datasets WHERE id=:i"), {"i": ref_id}).first():
            continue
        legacy = conn.execute(sa.text(
            "SELECT id,name,schema_json,created_at,updated_at FROM v2_curated_datasets WHERE id=:i"
        ), {"i": ref_id}).mappings().first()
        if not legacy:
            raise RuntimeError(f"{table}.{column} 引用了不存在的数据集 {ref_id}，拒绝静默断开血缘")
        same_name = conn.execute(sa.text(
            "SELECT id FROM v2_datasets WHERE kind='curated' AND name=:n LIMIT 1"
        ), {"n": legacy["name"]}).first()
        if same_name:
            conn.execute(sa.text(
                f"UPDATE {table} SET {column}=:new WHERE {column}=:old"
            ), {"new": same_name[0], "old": ref_id})
            continue
        now = datetime.now(timezone.utc)
        conn.execute(datasets.insert().values(
            id=ref_id,
            name=legacy["name"],
            source_connection_id=None,
            kind="curated",
            schema_json=_json_value(legacy["schema_json"]),
            latest_version_id=None,
            created_at=legacy["created_at"] or now,
            updated_at=legacy["updated_at"] or now,
        ))


def _upgrade_asset_references() -> None:
    _ensure_column("v2_ontology_mappings", sa.Column("target_object_type_id", sa.String(), nullable=True))
    _ensure_column("v2_ontology_link_mappings", sa.Column("link_type_id", sa.String(), nullable=True))
    _ensure_column("v2_ontology_link_mappings", sa.Column("edge_dataset_id", sa.String(), nullable=True))
    _ensure_column(
        "v2_ontology_link_mappings",
        sa.Column("field_mapping", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    _ensure_column("v2_curated_reviews", sa.Column("dataset_version_id", sa.String(), nullable=True))

    for table, column in [
        ("v2_ontology_mappings", "curated_dataset_id"),
        ("v2_ontology_link_mappings", "src_dataset_id"),
        ("v2_ontology_link_mappings", "tgt_dataset_id"),
        ("v2_ontology_link_mappings", "edge_dataset_id"),
        ("v2_curated_reviews", "curated_dataset_id"),
    ]:
        _ensure_canonical_dataset_refs(table, column)

    _replace_fk("v2_ontology_mappings", "curated_dataset_id", "v2_datasets")
    _replace_fk("v2_ontology_link_mappings", "src_dataset_id", "v2_datasets")
    _replace_fk("v2_ontology_link_mappings", "tgt_dataset_id", "v2_datasets")
    _replace_fk("v2_ontology_link_mappings", "edge_dataset_id", "v2_datasets")
    _replace_fk("v2_curated_reviews", "curated_dataset_id", "v2_datasets", ondelete="CASCADE")

    dangling_review_versions = op.get_bind().execute(sa.text(
        "SELECT id,dataset_version_id FROM v2_curated_reviews r "
        "WHERE dataset_version_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM v2_dataset_versions v WHERE v.id=r.dataset_version_id) LIMIT 5"
    )).fetchall()
    if dangling_review_versions:
        raise RuntimeError(f"审核记录引用了不存在的数据版本：{dangling_review_versions}")
    _replace_fk("v2_curated_reviews", "dataset_version_id", "v2_dataset_versions")
    _ensure_index(
        "ix_v2_curated_reviews_dataset_version_id",
        "v2_curated_reviews",
        ["dataset_version_id"],
    )


def _upgrade_run_and_release_integrity() -> None:
    _ensure_column("v2_pipeline_runs", sa.Column("task_id", sa.String(), nullable=True))
    op.get_bind().execute(sa.text(
        "UPDATE v2_pipeline_runs SET task_id=NULL WHERE task_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM v2_pipeline_tasks t WHERE t.id=v2_pipeline_runs.task_id)"
    ))
    _replace_fk("v2_pipeline_runs", "task_id", "v2_pipeline_tasks", ondelete="SET NULL")

    _fail_on_duplicates(
        "v2_pipeline_versions", ["pipeline_id", "version"], "流水线发布版本")
    _ensure_index(
        "uq_pipeline_versions_pipeline_version",
        "v2_pipeline_versions",
        ["pipeline_id", "version"],
        unique=True,
    )


def upgrade() -> None:
    _create_steward_tables()
    _create_pipeline_task_table()
    _create_legacy_sync_tables()
    _upgrade_asset_references()
    _upgrade_run_and_release_integrity()


def downgrade() -> None:
    # 本迁移会把旧/新双资产引用收敛到一个 canonical id。自动逆转将重新制造双
    # 真源并可能让人工数据集映射无处可挂，因此选择显式阻止破坏性降级。
    raise RuntimeError(
        "0011 是数据身份收敛的前向迁移，禁止自动降级；如需回退请从迁移前备份恢复。")
