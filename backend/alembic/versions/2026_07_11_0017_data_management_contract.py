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
    # 治理记录是 n8n 影子流水线 owner 的可证明来源。旧版本漏写 Pipeline.owner
    # 会让任意 editor 按“legacy 无 owner”规则接管，迁移时必须补齐。
    op.get_bind().execute(sa.text(
        "UPDATE v2_pipelines SET created_by=("
        " SELECT n.created_by FROM v2_n8n_pipelines n"
        " WHERE n.pipeline_id=v2_pipelines.id AND n.created_by IS NOT NULL"
        ") WHERE created_by IS NULL AND EXISTS ("
        " SELECT 1 FROM v2_n8n_pipelines n2"
        " WHERE n2.pipeline_id=v2_pipelines.id AND n2.created_by IS NOT NULL)"
    ))

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
            sa.Column("execution_token", sa.String(36), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
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
        _ensure_column("v2_pipeline_tasks", sa.Column("execution_token", sa.String(36), nullable=True))
        _ensure_column("v2_pipeline_tasks", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
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
    _ensure_index("ix_v2_pipeline_tasks_execution_token", "v2_pipeline_tasks", ["execution_token"])
    _ensure_index("ix_v2_pipeline_tasks_lease_expires_at", "v2_pipeline_tasks", ["lease_expires_at"])


def _create_storage_deletion_outbox() -> None:
    """为数据库外对象提供可恢复的删除语义。

    Dataset/Version/Media 元数据与本表记录在同一事务删除/写入；对象存储在提交后
    幂等清理。这里不对 storage_uri 建唯一约束：历史上若两个资产误共享 URI，
    并发删除也不能因为 outbox 唯一键冲突而回滚整个元数据事务。
    """
    if not _has_table("v2_storage_deletion_outbox"):
        op.create_table(
            "v2_storage_deletion_outbox",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("storage_uri", sa.Text(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    else:
        _ensure_column(
            "v2_storage_deletion_outbox",
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        )
        _ensure_column(
            "v2_storage_deletion_outbox",
            sa.Column("last_error", sa.Text(), nullable=True),
        )
        _ensure_column(
            "v2_storage_deletion_outbox",
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        _ensure_column(
            "v2_storage_deletion_outbox",
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    _ensure_index(
        "ix_v2_storage_deletion_outbox_storage_uri",
        "v2_storage_deletion_outbox", ["storage_uri"])
    _ensure_index(
        "ix_v2_storage_deletion_outbox_created_at",
        "v2_storage_deletion_outbox", ["created_at"])


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
    # 旧 DataSyncTask 会绕过“已发布 n8n 流水线 → PipelineTask → 资产湖契约”
    # 主链路。升级时隔离存量任务，保留配置与历史供人工迁移，不再后台偷跑。
    op.get_bind().execute(sa.text(
        "UPDATE v2_data_sync_tasks SET enabled=false, "
        "last_error='已由 0017 升级隔离：请迁移为已发布 n8n 流水线的数据任务后再启用' "
        "WHERE enabled=true"
    ))


def _ensure_canonical_dataset_refs(table: str, column: str) -> None:
    """确认旧引用已经按同一 ID 收敛到 canonical 数据集。

    数据集名称不是身份：同名可能来自重跑、复制或并发创建。这里不能再像旧逻辑
    那样按名称改写引用，也不能仅凭 legacy 元数据创建一个没有任何版本的空壳
    ``v2_datasets``。只有相同 ID 已存在于 canonical 表时，引用归属才可证明；
    否则输出可操作的审计清单并中止迁移。
    """
    if not _has_table(table) or column not in _columns(table):
        return
    conn = op.get_bind()
    refs = conn.execute(sa.text(
        f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"
    )).fetchall()
    unresolved: list[dict] = []
    for (ref_id,) in refs:
        if conn.execute(sa.text("SELECT 1 FROM v2_datasets WHERE id=:i"), {"i": ref_id}).first():
            continue
        legacy = conn.execute(sa.text(
            "SELECT id,name,latest_version_id FROM v2_curated_datasets WHERE id=:i"
        ), {"i": ref_id}).mappings().first()
        exact_versions = conn.execute(sa.text(
            "SELECT id,version_no FROM v2_dataset_versions "
            "WHERE dataset_id=:i ORDER BY version_no,id LIMIT 10"
        ), {"i": ref_id}).fetchall()
        same_name_candidates = []
        if legacy and legacy["name"]:
            candidates = conn.execute(sa.text(
                "SELECT d.id,d.latest_version_id,COUNT(v.id) AS version_count "
                "FROM v2_datasets d LEFT JOIN v2_dataset_versions v ON v.dataset_id=d.id "
                "WHERE d.kind='curated' AND d.name=:n "
                "GROUP BY d.id,d.latest_version_id ORDER BY d.id LIMIT 10"
            ), {"n": legacy["name"]}).mappings().all()
            same_name_candidates = [dict(candidate) for candidate in candidates]
        unresolved.append({
            "reference": f"{table}.{column}",
            "referenced_id": ref_id,
            "legacy_dataset": dict(legacy) if legacy else None,
            "versions_with_exact_id": [
                {"id": row[0], "version_no": row[1]} for row in exact_versions],
            "same_name_candidates_not_used": same_name_candidates,
            "required_action": (
                "restore/migrate the canonical v2_datasets row with this exact id and "
                "verify its DatasetVersion lineage before retrying"
            ),
        })

    if unresolved:
        raise RuntimeError(
            "数据集身份预检失败：以下引用无法通过相同 ID 证明归属；迁移不会按名称猜测，"
            "也不会创建无版本的空 canonical 数据集。请完成逐项血缘审计后重试。"
            f" 审计清单={unresolved}"
        )


def _schema_object(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _upgrade_dataset_output_identity() -> None:
    """把流水线产物身份从展示 JSON 提升为数据库约束。

    只回填能够同时证明 pipeline 真实存在、且 JSON 同时给出 output_key 的记录。
    名称不参与身份推断；残缺/悬空的显式列以及重复身份都会中止迁移。
    """
    _ensure_column(
        "v2_datasets",
        sa.Column("producer_pipeline_id", sa.String(), nullable=True),
    )
    _ensure_column(
        "v2_datasets",
        sa.Column("output_key", sa.String(500), nullable=True),
    )
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id,schema_json,producer_pipeline_id,output_key FROM v2_datasets"
    )).mappings().all()
    for row in rows:
        if row["producer_pipeline_id"] is not None or row["output_key"] is not None:
            continue
        schema = _schema_object(row["schema_json"])
        pipeline_id = str(schema.get("pipeline_id") or "").strip()
        output_key = str(schema.get("output_key") or "").strip()
        if not pipeline_id or not output_key:
            continue
        if not conn.execute(sa.text(
            "SELECT 1 FROM v2_pipelines WHERE id=:pipeline_id"
        ), {"pipeline_id": pipeline_id}).first():
            # 悬空 JSON 只是历史展示元数据，不足以建立真实外键。
            continue
        conn.execute(sa.text(
            "UPDATE v2_datasets SET producer_pipeline_id=:pipeline_id,output_key=:output_key "
            "WHERE id=:dataset_id AND producer_pipeline_id IS NULL AND output_key IS NULL"
        ), {
            "dataset_id": row["id"],
            "pipeline_id": pipeline_id,
            "output_key": output_key,
        })

    incomplete = conn.execute(sa.text(
        "SELECT id,producer_pipeline_id,output_key FROM v2_datasets "
        "WHERE (producer_pipeline_id IS NULL AND output_key IS NOT NULL) "
        "OR (producer_pipeline_id IS NOT NULL AND (output_key IS NULL OR TRIM(output_key)='')) "
        "LIMIT 20"
    )).fetchall()
    if incomplete:
        raise RuntimeError(
            "流水线产物身份必须同时包含 producer_pipeline_id 与非空 output_key；"
            f"请先修复以下资产：{incomplete}"
        )

    dangling = conn.execute(sa.text(
        "SELECT d.id,d.producer_pipeline_id,d.output_key FROM v2_datasets d "
        "WHERE d.producer_pipeline_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM v2_pipelines p WHERE p.id=d.producer_pipeline_id) LIMIT 20"
    )).fetchall()
    if dangling:
        raise RuntimeError(
            "流水线产物身份引用了不存在的 producer pipeline，拒绝删除/猜测归属；"
            f"审计清单={dangling}"
        )

    duplicates = conn.execute(sa.text(
        "SELECT producer_pipeline_id,output_key,COUNT(*) AS n FROM v2_datasets "
        "WHERE producer_pipeline_id IS NOT NULL AND output_key IS NOT NULL "
        "GROUP BY producer_pipeline_id,output_key HAVING COUNT(*) > 1 LIMIT 20"
    )).fetchall()
    if duplicates:
        audit = []
        for pipeline_id, output_key, count in duplicates:
            ids = conn.execute(sa.text(
                "SELECT id FROM v2_datasets WHERE producer_pipeline_id=:pipeline_id "
                "AND output_key=:output_key ORDER BY id LIMIT 20"
            ), {"pipeline_id": pipeline_id, "output_key": output_key}).scalars().all()
            audit.append({
                "producer_pipeline_id": pipeline_id,
                "output_key": output_key,
                "count": count,
                "dataset_ids": list(ids),
            })
        raise RuntimeError(
            "同一流水线产物槽位对应多个数据资产，无法建立唯一约束；"
            f"审计清单={audit}"
        )

    _replace_fk(
        "v2_datasets", "producer_pipeline_id",
        "v2_pipelines", ondelete="RESTRICT")
    if "uq_datasets_producer_output" not in _index_names("v2_datasets"):
        op.create_index(
            "uq_datasets_producer_output",
            "v2_datasets",
            ["producer_pipeline_id", "output_key"],
            unique=True,
            sqlite_where=sa.text("producer_pipeline_id IS NOT NULL"),
            postgresql_where=sa.text("producer_pipeline_id IS NOT NULL"),
        )


def _upgrade_connection_resource_identity() -> None:
    """把连接同步身份从 connection 提升为 connection + resource。

    同一个连接可暴露多个表、集合、端点或文件。历史版本没有持久化 resource，
    因而不能从 Dataset.name 反推（名称可改、可截断、也可能重名）；这些记录保留
    ``NULL`` 供审计，新同步会建立具有明确双键身份的数据集。
    """
    _ensure_column(
        "v2_datasets",
        sa.Column("source_resource", sa.String(500), nullable=True),
    )
    conn = op.get_bind()
    invalid = conn.execute(sa.text(
        "SELECT id,source_connection_id,source_resource FROM v2_datasets "
        "WHERE source_resource IS NOT NULL AND source_connection_id IS NULL LIMIT 20"
    )).fetchall()
    if invalid:
        raise RuntimeError(
            "数据集 source_resource 缺少所属 connection，无法证明来源身份；"
            f"审计清单={invalid}"
        )

    duplicates = conn.execute(sa.text(
        "SELECT source_connection_id,source_resource,COUNT(*) AS n FROM v2_datasets "
        "WHERE source_connection_id IS NOT NULL AND source_resource IS NOT NULL "
        "GROUP BY source_connection_id,source_resource HAVING COUNT(*) > 1 LIMIT 20"
    )).fetchall()
    if duplicates:
        audit = []
        for connection_id, resource, count in duplicates:
            ids = conn.execute(sa.text(
                "SELECT id FROM v2_datasets WHERE source_connection_id=:connection_id "
                "AND source_resource=:resource ORDER BY id LIMIT 20"
            ), {
                "connection_id": connection_id,
                "resource": resource,
            }).scalars().all()
            audit.append({
                "source_connection_id": connection_id,
                "source_resource": resource,
                "count": count,
                "dataset_ids": list(ids),
            })
        raise RuntimeError(
            "同一连接资源对应多个数据集，无法建立唯一身份约束；"
            f"审计清单={audit}"
        )

    if "uq_datasets_connection_resource" not in _index_names("v2_datasets"):
        op.create_index(
            "uq_datasets_connection_resource",
            "v2_datasets",
            ["source_connection_id", "source_resource"],
            unique=True,
            sqlite_where=sa.text(
                "source_connection_id IS NOT NULL AND source_resource IS NOT NULL"),
            postgresql_where=sa.text(
                "source_connection_id IS NOT NULL AND source_resource IS NOT NULL"),
        )


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
    # 审核记录是不可变的治理证据。删除数据集或版本必须先经过显式审计流程，
    # 不能级联删除审核，也不能把版本引用静默置空。
    _replace_fk("v2_curated_reviews", "curated_dataset_id", "v2_datasets", ondelete="RESTRICT")

    dangling_review_versions = op.get_bind().execute(sa.text(
        "SELECT id,dataset_version_id FROM v2_curated_reviews r "
        "WHERE dataset_version_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM v2_dataset_versions v WHERE v.id=r.dataset_version_id) LIMIT 5"
    )).fetchall()
    if dangling_review_versions:
        raise RuntimeError(f"审核记录引用了不存在的数据版本：{dangling_review_versions}")
    _replace_fk(
        "v2_curated_reviews", "dataset_version_id",
        "v2_dataset_versions", ondelete="RESTRICT")
    _ensure_index(
        "ix_v2_curated_reviews_dataset_version_id",
        "v2_curated_reviews",
        ["dataset_version_id"],
    )

    # 复合主键以 canonical JSON 保存，多个业务键/UUID 可能超过 200 字符；
    # 截断会让审核修改命中错误行。
    row_pk_column = next(
        (item for item in _inspector().get_columns("v2_curated_row_edits")
         if item["name"] == "row_pk"),
        None,
    )
    if row_pk_column is not None and str(row_pk_column["type"]).upper() != "TEXT":
        with op.batch_alter_table(
            "v2_curated_row_edits", naming_convention=_NAMING
        ) as batch:
            batch.alter_column(
                "row_pk", existing_type=row_pk_column["type"],
                type_=sa.Text(), existing_nullable=False)


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


def _upgrade_latest_version_integrity() -> None:
    dangling = op.get_bind().execute(sa.text(
        "SELECT id,latest_version_id FROM v2_datasets d "
        "WHERE latest_version_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM v2_dataset_versions v "
        " WHERE v.id=d.latest_version_id AND v.dataset_id=d.id) LIMIT 20"
    )).fetchall()
    if dangling:
        raise RuntimeError(
            "数据集 latest_version_id 指向不存在或属于其他数据集的版本；"
            f"拒绝静默清空当前版本身份，审计清单={dangling}"
        )
    _replace_fk(
        "v2_datasets", "latest_version_id",
        "v2_dataset_versions", ondelete="SET NULL")


def upgrade() -> None:
    _create_steward_tables()
    _create_pipeline_task_table()
    _create_storage_deletion_outbox()
    _create_legacy_sync_tables()
    _upgrade_connection_resource_identity()
    _upgrade_dataset_output_identity()
    _upgrade_asset_references()
    _upgrade_run_and_release_integrity()
    _upgrade_latest_version_integrity()


def downgrade() -> None:
    # 本迁移会把旧/新双资产引用收敛到一个 canonical id。自动逆转将重新制造双
    # 真源并可能让人工数据集映射无处可挂，因此选择显式阻止破坏性降级。
    raise RuntimeError(
        "0017 是数据身份收敛的前向迁移，禁止自动降级；如需回退请从迁移前备份恢复。")
