"""成品数据集 → 人工数据集 异步迁移。

业务目标：把一个成品数据集的当前数据快照拷贝为一份可在线维护的人工
数据集（结构与数据一致），源资产保持不变。大表拷贝不宜占用 Web 请求
事务，因此走与数据集导入相同的 JetStream 工作队列 + 文件系统状态机：
API 只负责校验、建任务并投递，executor 进程执行真正拷贝。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.data_channel.datasets import migration_jobs
from app.data_channel.datasets.models import Dataset

logger = logging.getLogger(__name__)

# 迁移副本命名：显式后缀向用户说明来源；重名时追加序号（人工数据集名
# 无唯一约束，但重名会让映射/流水线选 dataset 时产生歧义）。
COPY_SUFFIX = "（人工副本）"


def _manual_schema_from_curated(schema: dict | None, row_sample: list[dict]) -> tuple[list[str], dict]:
    """把 curated schema_json 映射为人工数据集可维护的契约。

    保留湖内列名、声明类型、主键与中文展示名；丢弃 review/pipeline 专属
    的契约段落（contract_definitions 属于发布管线的 source_key 改名语义，
    与人工维护无关）。行样例用于兜底列顺序缺失的情况。
    """
    from app.data_channel.datasets.lake_gate import infer_columns_typed

    schema = dict(schema or {})
    typed = schema.get("columns_typed")
    columns: list[str] = [str(c) for c in (schema.get("columns") or [])]
    if not columns:
        seen: list[str] = []
        for row in row_sample[:1]:
            for key in row.keys():
                if key not in seen:
                    seen.append(str(key))
        columns = seen
    typed_map = {
        str(item.get("name")): str(item.get("type") or "string")
        for item in (typed or [])
        if isinstance(item, dict) and item.get("name")
    }
    inferred = {item["name"]: item["type"] for item in infer_columns_typed(row_sample)}
    field_names = schema.get("field_names")
    columns_typed = [
        {
            "name": column,
            "display_name": (
                str(field_names.get(column)) if isinstance(field_names, dict) and field_names.get(column)
                else column
            ),
            "type": typed_map.get(column) or inferred.get(column) or "string",
            "nullable": True,
        }
        for column in columns
    ]
    manual_schema: dict = {
        "columns": columns,
        "columns_typed": columns_typed,
        "field_names": {item["name"]: item["display_name"] for item in columns_typed},
        "types_source": "declared",
        "origin": "upload",
        # 湖固化主键随副本保留，人工侧可直接被本体映射绑定
        **({"primary_key": str(schema["primary_key"]), "pk_source": "manual"}
           if schema.get("primary_key") else {}),
        "migrated_from": {
            "kind": "curated",
            "dataset_id": None,  # 由调用方填充
        },
    }
    return columns, manual_schema


def resolve_target_name(db: Session, source_name: str) -> str:
    """返回不与非成品数据集重名的副本名：「源名（人工副本）」→「… 2）」…"""
    names = {
        name for (name,) in db.query(Dataset.name).filter(
            Dataset.kind != "curated").all()
    }
    base = f"{source_name}{COPY_SUFFIX}"
    if base not in names:
        return base
    index = 2
    while f"{source_name}{COPY_SUFFIX[:-1]} {index}）" in names:
        index += 1
    return f"{source_name}{COPY_SUFFIX[:-1]} {index}）"


def start_migration(
    db: Session,
    curated_dataset_id: str,
    current_user: Any,
) -> dict:
    """创建迁移任务并投递到后台队列（fail-closed）。

    同步阶段只做存在性校验和任务建档；真正的读取、结构转换与版本写入
    全部发生在 executor 进程。
    """
    from app.data_channel.pipeline_tasks.dispatch import (
        DATASET_MIGRATE_SUBJECT,
        dispatch_task,
    )

    dataset = db.query(Dataset).filter(
        Dataset.id == curated_dataset_id, Dataset.kind == "curated").first()
    if dataset is None:
        raise HTTPException(404, "成品数据集不存在或已被删除")

    target_name = resolve_target_name(db, dataset.name)
    manifest = migration_jobs.create_migration_job(
        owner_id=current_user.id,
        source_dataset_id=dataset.id,
        source_name=dataset.name,
        target_name=target_name,
    )
    job_id = manifest["job_id"]
    try:
        dispatch_task(
            DATASET_MIGRATE_SUBJECT,
            {"job_id": job_id, "source_dataset_id": dataset.id},
        )
    except Exception as exc:  # noqa: BLE001 - dispatch failures are fail-closed
        migration_jobs.update_status(
            job_id,
            status="failed",
            phase="后台任务投递失败",
            error="后台任务通道不可用",
        )
        logger.error(
            "后台任务通道无法投递数据集迁移任务 %s；任务未执行（%s）",
            job_id, type(exc).__name__,
        )
        raise HTTPException(
            503, "后台任务通道不可用，迁移任务未投递") from exc

    from app.data_channel.datasets.migration_jobs import update_status

    status = update_status(job_id, execution_mode="nats", progress=5)
    logger.info(
        "数据集迁移任务 %s 已投递（source=%s owner=%s mode=nats）",
        job_id, dataset.id, current_user.id,
    )
    return {"data": {"job_id": job_id, **{
        key: status.get(key)
        for key in ("status", "progress", "phase",
                    "source_dataset_name", "target_name")
    }}}


def get_migration(job_id: str, current_user: Any) -> dict:
    try:
        migration_jobs.assert_job_owner(job_id, current_user.id)
        return {"data": migration_jobs.read_status(job_id)}
    except FileNotFoundError:
        raise HTTPException(404, "迁移任务不存在或已被清理")
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def list_migrations(current_user: Any, limit: int = 20) -> dict:
    return {"data": migration_jobs.list_jobs(current_user.id, limit=limit)}


def run_migration(job_id: str, source_dataset_id: str) -> None:
    """executor 线程内执行：读最新版本（叠加已批准编辑）→ 建人工数据集 v1。"""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        _run_with_session(db, job_id, source_dataset_id)
    except Exception as exc:  # noqa: BLE001 - task failures are reported to the owner
        logger.exception("数据集迁移任务 %s 执行失败", job_id)
        try:
            migration_jobs.update_status(
                job_id,
                status="failed",
                phase="迁移执行失败",
                error=str(exc) or type(exc).__name__,
            )
        except Exception:  # noqa: BLE001 - 状态文件失败不能掩盖原始异常
            logger.exception("迁移任务 %s 失败状态写入失败", job_id)
    finally:
        db.close()


def _run_with_session(db: Session, job_id: str, source_dataset_id: str) -> None:
    from app.data_channel.curated.row_edit_overlay import iter_rows_with_edits
    from app.data_channel.datasets.manual_contract import serialize_manual_contract_rows
    from app.data_channel.datasets.migration_jobs import (
        read_manifest,
        update_status,
    )
    from app.data_channel.datasets.service import DatasetService

    def _set(**patch) -> None:
        update_status(job_id, **patch)

    manifest = read_manifest(job_id)

    dataset = db.query(Dataset).filter(
        Dataset.id == source_dataset_id, Dataset.kind == "curated").first()
    if dataset is None:
        raise ValueError("源成品数据集不存在或已被删除")

    _set(status="running", progress=10, phase="正在读取成品数据集最新版本", error=None)

    rows: list[dict] = []
    batch_size = 2000
    loaded_batches = 0
    for batch in iter_rows_with_edits(db, source_dataset_id, batch_size=batch_size):
        rows.extend(batch)
        loaded_batches += 1
        # 每批一次文件写太密；每 10 批（约 2 万行）刷新一次进度即可
        if loaded_batches % 10 == 0:
            _set(progress=min(60, 15 + len(rows) // 1000),
                 phase=f"正在读取数据（已加载 {len(rows)} 行）")
    total_rows = len(rows)

    _set(progress=70, phase="正在转换字段结构")
    columns, manual_schema = _manual_schema_from_curated(dataset.schema_json, rows)
    migrated_from = dict(manual_schema.get("migrated_from") or {})
    migrated_from["dataset_id"] = source_dataset_id
    manual_schema["migrated_from"] = migrated_from

    _set(progress=80, phase="正在序列化副本数据")
    content = serialize_manual_contract_rows(
        [{column: row.get(column) for column in columns} for row in rows],
        columns,
    )

    _set(progress=90, phase="正在创建人工数据集首个版本")
    service = DatasetService(db)
    created = service.create_dataset(
        name=str(manifest.get("target_name") or dataset.name),
        kind="structured",
        schema_json=manual_schema,
        commit=False,
    )
    version = service.create_version(
        created.id,
        content,
        rowcount=total_rows,
        schema_json=manual_schema,
        _lock_held=True,
    )
    _set(
        status="completed",
        progress=100,
        phase="迁移完成",
        error=None,
        result={
            "id": created.id,
            "name": created.name,
            "kind": created.kind,
            "columns": columns,
            "primary_key": str(manual_schema.get("primary_key") or ""),
            "version_no": version.version_no,
            "rowcount": total_rows,
            "source_dataset_id": source_dataset_id,
            "source": "upload",
        },
    )
