"""
数据同步引擎 (Data Sync Engine)
- SNAPSHOT: 全量快照，每次拉取全部数据写入新版本，覆盖目标 Dataset 当前视图
- APPEND:   基于 watermark_column 增量追加；支持按 primary_key 去重取最新；
            支持 is_deleted_column 软删除标记（不物理删除，在数据中加 __deleted__ 字段）

所有数据写入 Dataset，生成 DatasetVersion；同步成功后可选触发指定 Pipeline。
"""
from __future__ import annotations

import csv
import io
import json
import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.v2.sync_task import DataSyncTask, DataSyncHistory
from app.models.v2.connection import Connection
from app.models.v2.dataset import Dataset, DatasetVersion
from app.services.connection.registry import get_connector
from app.services import encryption_service

logger = logging.getLogger(__name__)


def _decrypt_config(conn: Connection) -> dict:
    raw = (conn.config or {}).get("_encrypted", "")
    if not raw:
        return conn.config or {}
    try:
        return json.loads(encryption_service.decrypt(raw))
    except Exception:
        return conn.config or {}


def _coerce_watermark(val: Any) -> str:
    """水印值统一序列化为字符串，兼容 datetime/int/str 等类型"""
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _compare_watermark(new_val: Any, old_str: str) -> bool:
    """判断 new_val 是否 > old_str (水印推进判断)"""
    if not old_str:
        return True
    if new_val is None:
        return False
    if isinstance(new_val, (int, float)):
        try:
            return new_val > type(new_val)(old_str)
        except Exception:
            return str(new_val) > old_str
    if isinstance(new_val, datetime):
        try:
            return new_val > datetime.fromisoformat(old_str)
        except Exception:
            return new_val.isoformat() > old_str
    return str(new_val) > old_str


def _pulled_to_rows(pulled: Any) -> list[dict]:
    """connector 拉取结果统一转行列表。

    FileConnector.pull_full 返回 bytes（CSV/JSON/XLSX 原始文件）——
    之前被 `isinstance(pulled, list) else []` 直接丢弃，file 类同步任务
    永远产出 0 行空数据集。此处按内容解析；解析不出时抛错让失败可见。
    """
    if isinstance(pulled, list):
        return pulled
    if isinstance(pulled, (bytes, bytearray)):
        raw = bytes(pulled)
        text = raw.decode("utf-8", errors="replace").lstrip("﻿")
        stripped = text.lstrip()
        if stripped.startswith("[") or stripped.startswith("{"):
            data = json.loads(text)
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
            if isinstance(data, dict):
                return [data]
            raise ValueError("JSON 文件内容不是对象或数组，无法转为行数据")
        if raw[:2] == b"PK":  # xlsx
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            rows_list: list[dict] = []
            headers: list[str] = []
            for ri, row in enumerate(ws.iter_rows(values_only=True)):
                if ri == 0:
                    headers = [str(c) if c is not None else f"col_{i}" for i, c in enumerate(row)]
                else:
                    rows_list.append({headers[i]: (v if v is not None else "")
                                      for i, v in enumerate(row) if i < len(headers)})
            wb.close()
            return rows_list
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(r) for r in reader]
        if not rows and text.strip():
            raise ValueError("文件内容无法按 CSV/JSON/XLSX 解析为行数据")
        return rows
    return []


def _rows_to_csv_bytes(rows: list[dict]) -> tuple[bytes, list[str]]:
    """行列表 → CSV bytes + 列名列表（保留列顺序，空数据返回空列）"""
    if not rows:
        return b"", []
    # 列顺序：第一个行的键序；后续行补充缺失列
    cols: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                cols.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        # 把非字符串值转成可序列化形式
        clean = {}
        for k in cols:
            v = r.get(k)
            if isinstance(v, datetime):
                clean[k] = v.isoformat()
            elif v is None:
                clean[k] = ""
            else:
                clean[k] = v
        writer.writerow(clean)
    return buf.getvalue().encode("utf-8"), cols


def _infer_schema(rows: list[dict], cols: list[str]) -> dict:
    """从样本推断 schema，仅取前 50 行"""
    sample = rows[:50]
    schema = {}
    for c in cols:
        types: set[str] = set()
        for r in sample:
            v = r.get(c)
            if v is None or v == "":
                continue
            if isinstance(v, bool):
                types.add("boolean")
            elif isinstance(v, int):
                types.add("integer")
            elif isinstance(v, float):
                types.add("number")
            elif isinstance(v, datetime):
                types.add("datetime")
            else:
                types.add("string")
        if not types:
            schema[c] = "string"
        elif len(types) == 1:
            schema[c] = next(iter(types))
        elif "string" in types:
            schema[c] = "string"
        else:
            schema[c] = next(iter(types))
    return {"columns": cols, "types": schema, "row_count_estimate": len(rows)}


def _get_or_create_dataset(db: Session, task: DataSyncTask, conn: Connection) -> Dataset:
    """每个 SyncTask 对应一个独立 Dataset（首次同步时自动创建）"""
    ds = db.query(Dataset).filter(Dataset.name == f"SYNC::{task.name}").first()
    if ds:
        return ds
    ds = Dataset(
        name=f"SYNC::{task.name}",
        kind="structured",
        source_connection_id=conn.id,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


def _write_dataset_version(
    db: Session, ds: Dataset, rows: list[dict], trigger: str, connection_id: str
) -> tuple[DatasetVersion, list[str]]:
    """把行列表写入 Dataset 新版本，返回版本和列名。

    版本发布统一委托 DatasetService，避免本同步入口绕过 UUID 对象键、写锁和
    完整 checksum 校验。
    """
    content, cols = _rows_to_csv_bytes(rows)
    # 推断 schema_json
    schema_json = _infer_schema(rows, cols)
    # 记录源信息
    schema_json["source"] = {"connection_id": connection_id, "trigger": trigger}

    from app.services.v2.dataset_service import DatasetService
    ver = DatasetService(db).create_version(ds.id, content, rowcount=len(rows))
    ds.schema_json = schema_json
    ds.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(ver)
    return ver, cols


def _load_previous_version_rows(db: Session, ds: Dataset) -> list[dict]:
    """严格加载上一个版本的全部数据（用于 APPEND 主键去重合并）。

    存储读取/解析/checksum 失败必须让本次同步失败；把失败伪装成空基座会使
    新版本只剩本次增量，构成静默数据丢失。
    """
    from app.services.v2.dataset_service import DatasetService
    return DatasetService(db).load_all_rows(ds.id)


def _dedup_by_key(rows: list[dict], key_cols: list[str]) -> list[dict]:
    """按主键列去重：保留最后出现的记录（即最新）"""
    if not key_cols or not rows:
        return rows
    seen: dict[tuple, int] = {}
    for i, r in enumerate(rows):
        key = tuple(str(r.get(k, "")) for k in key_cols)
        seen[key] = i
    keep_idx = sorted(seen.values())
    return [rows[i] for i in keep_idx]


def _apply_soft_delete(rows: list[dict], deleted_col: str) -> tuple[list[dict], int]:
    """在 APPEND 合并后扫描整表，把 is_deleted_column 为真值的行标记 __deleted__=True"""
    if not deleted_col or not rows:
        return rows, 0
    deleted_cnt = 0
    truthy = {"1", "true", "yes", "y", "t", "是", "删除", "已删除"}
    for r in rows:
        v = r.get(deleted_col)
        is_del = str(v).strip().lower() in truthy if v is not None else False
        if is_del:
            r["__deleted__"] = True
            r["__deleted_at__"] = datetime.utcnow().isoformat()
            deleted_cnt += 1
        else:
            r.pop("__deleted__", None)
            r.pop("__deleted_at__", None)
    return rows, deleted_cnt


def _extract_new_watermark(rows: list[dict], watermark_col: str) -> str:
    """从新拉取的行中找到最大的水印值"""
    if not watermark_col or not rows:
        return ""
    max_val: Any = None
    for r in rows:
        v = r.get(watermark_col)
        if v is None or v == "":
            continue
        # 统一到可比较类型
        try:
            if isinstance(v, str):
                # 尝试解析为 datetime
                try:
                    v_parsed = datetime.fromisoformat(v)
                    v_cmp: Any = v_parsed
                except Exception:
                    v_cmp = v
            else:
                v_cmp = v
        except Exception:
            v_cmp = str(v)
        if max_val is None:
            max_val = v_cmp
        else:
            try:
                if v_cmp > max_val:
                    max_val = v_cmp
            except Exception:
                if str(v_cmp) > str(max_val):
                    max_val = v_cmp
    return _coerce_watermark(max_val)


def _run_pipeline_if_configured(db: Session, task: DataSyncTask) -> None:
    """如果任务配置了 trigger_pipeline_id，同步完成后触发 Pipeline 执行"""
    if not task.trigger_pipeline_id:
        return
    try:
        from app.models.v2.pipeline import Pipeline
        from app.services.v2.pipeline.engine import execute_pipeline
        pipe = db.query(Pipeline).filter(
            Pipeline.id == task.trigger_pipeline_id,
            Pipeline.status == "published",
        ).first()
        if not pipe:
            logger.info(f"触发 Pipeline 跳过：Pipeline {task.trigger_pipeline_id} 不存在或未发布")
            return
        # 提交后再触发，保证数据可见
        db.commit()
        execute_pipeline(task.trigger_pipeline_id, triggered_by=f"sync:{task.id}")
    except Exception as e:
        logger.error(f"触发 Pipeline 异常（不影响同步结果）: {e}")


def execute_sync_task(task_id: str, trigger_type: str = "MANUAL") -> dict:
    """
    执行一个同步任务。
    Returns: {"status": "ok"|"error", "task_id", "history_id", ...}
    """
    from app.database import SessionLocal

    db = SessionLocal()
    history: DataSyncHistory | None = None
    try:
        task = db.query(DataSyncTask).filter(DataSyncTask.id == task_id).first()
        if not task:
            return {"status": "error", "error": f"SyncTask {task_id} not found"}
        conn = db.query(Connection).filter(Connection.id == task.connection_id).first()
        if not conn:
            return {"status": "error", "error": f"Connection {task.connection_id} not found"}

        # 创建历史记录。状态统一用小写枚举值（models.SyncStatus/HistoryStatus）——
        # 曾经写大写导致 router 的并发守卫（比较 "running"）与统计全部失灵
        history = DataSyncHistory(
            task_id=task.id,
            trigger_type=trigger_type,
            status="running",
            watermark_before=task.last_watermark or "",
        )
        db.add(history)
        task.status = "running"
        task.last_error = ""
        db.commit()
        db.refresh(history)

        start_ts = time.monotonic()

        # 初始化 connector
        config = _decrypt_config(conn)
        # 把 watermark_column 注入 connector 配置（pull_delta 使用）
        if task.watermark_column:
            config = {**config, "watermark_column": task.watermark_column}
        connector = get_connector(conn.kind, config)

        # 确定资源（表/集合/端点）
        resource = task.source_table or ""
        if not resource and not task.source_query:
            resources = connector.list_resources()
            resource = resources[0] if resources else ""

        # 文件 Connector：将文件名解析为完整的 S3 URI。
        # 找不到时抛错走统一失败路径——直接 return 会把已 commit 的
        # RUNNING 状态永久留在任务与历史上
        if conn.kind == "file" and resource and not resource.startswith("s3://"):
            resources = connector.list_resources()
            found = False
            for r in resources:
                if r.endswith(resource) or resource in r:
                    resource = r
                    found = True
                    break
            if not found:
                raise ValueError(f"文件「{resource}」不存在于连接器中。可用资源: {resources}")

        # 自定义 SQL 查询模式：复用 pull_full 的 query 配置
        if task.source_query:
            config["query"] = task.source_query
            connector = get_connector(conn.kind, config)

        # ── 根据同步模式拉取数据 ──
        new_rows: list[dict] = []
        if task.sync_mode == "SNAPSHOT":
            pulled = connector.pull_full(resource)
            new_rows = _pulled_to_rows(pulled)
            final_rows = new_rows
            inserted = len(new_rows)
            updated = 0
            deleted_cnt = 0
            new_watermark = ""
        else:  # APPEND
            since_val = task.last_watermark or None
            if since_val:
                pulled = connector.pull_delta(resource, since=since_val)
            else:
                pulled = connector.pull_full(resource)
            new_rows = _pulled_to_rows(pulled)
            history.source_count = len(new_rows)

            # 加载历史数据，合并并按主键去重
            ds = _get_or_create_dataset(db, task, conn)
            prev_rows = _load_previous_version_rows(db, ds)
            merged = prev_rows + new_rows

            key_cols = [c.strip() for c in task.primary_key.split(",") if c.strip()]
            if key_cols:
                merged = _dedup_by_key(merged, key_cols)
                updated = len(new_rows)  # 简化：新增行中主键已存在的计为 updated
                inserted = len(new_rows) - max(0, updated - (len(merged) - len(prev_rows)))
                # 修正计数：merged - prev 是新增行数
                inserted = len(merged) - len(prev_rows)
                # updated 难以精确计算（需要比对每个字段），这里用"主键冲突数量"近似
                prev_keys = {tuple(str(r.get(k, "")) for k in key_cols) for r in prev_rows}
                updated = sum(
                    1 for r in new_rows
                    if tuple(str(r.get(k, "")) for k in key_cols) in prev_keys
                )
                inserted = len(new_rows) - updated
            else:
                inserted = len(new_rows)
                updated = 0

            # 软删除处理
            final_rows, deleted_cnt = _apply_soft_delete(merged, task.is_deleted_column or "")

            # 推进水印：无新行时保持原水印——曾经被空串覆盖导致下一轮退化为全量重拉
            new_watermark = _extract_new_watermark(new_rows, task.watermark_column) or (task.last_watermark or "")

        # ── 写入 Dataset 新版本 ──
        ds = _get_or_create_dataset(db, task, conn)
        ver, cols = _write_dataset_version(
            db, ds, final_rows if task.sync_mode == "APPEND" else new_rows,
            trigger="sync_task", connection_id=conn.id,
        )

        # ── 更新任务 & 历史状态 ──
        duration_ms = int((time.monotonic() - start_ts) * 1000)
        task.status = "success"
        task.last_sync_at = datetime.utcnow()
        task.last_rows = ver.rowcount
        if task.sync_mode == "APPEND":
            task.last_watermark = new_watermark
        else:
            task.last_watermark = ""
        task.last_error = ""

        history.ended_at = datetime.utcnow()
        history.duration_ms = duration_ms
        history.status = "success"
        history.source_count = len(new_rows)
        history.inserted_count = inserted if task.sync_mode == "APPEND" else len(new_rows)
        history.updated_count = updated if task.sync_mode == "APPEND" else 0
        history.deleted_count = deleted_cnt if task.sync_mode == "APPEND" else 0
        history.watermark_after = task.last_watermark
        history.dataset_id = ds.id
        history.dataset_version = ver.version_no
        db.commit()

        # ── 链式触发 Pipeline ──
        _run_pipeline_if_configured(db, task)
        # 增量编排：让声明了 on_dataset_version 触发的管道也能被自动唤起
        # （之前 on_connection_sync 只有 HTTP 回调端点、内部无任何调用点，自动链路首跳断链）
        try:
            from app.data_channel.sync_tasks.incremental_orchestrator import IncrementalOrchestrator
            IncrementalOrchestrator(db).on_connection_sync(conn.id, ds.id)
        except Exception:  # noqa: BLE001
            logger.warning("增量编排触发失败（不影响同步结果）", exc_info=True)

        return {
            "status": "ok",
            "task_id": task.id,
            "history_id": history.id,
            "dataset_id": ds.id,
            "dataset_version": ver.version_no,
            "rows": ver.rowcount,
            "inserted": history.inserted_count,
            "updated": history.updated_count,
            "deleted": history.deleted_count,
            "duration_ms": duration_ms,
        }
    except Exception as e:
        logger.exception(f"同步任务执行失败 task={task_id}")
        try:
            if history:
                history.ended_at = datetime.utcnow()
                history.duration_ms = int((datetime.utcnow() - history.started_at).total_seconds() * 1000) if history.started_at else 0
                history.status = "failed"
                history.error_message = str(e)[:2000]
            task = db.query(DataSyncTask).filter(DataSyncTask.id == task_id).first()
            if task:
                task.status = "failed"
                task.last_error = str(e)[:500]
            db.commit()
        except Exception:
            db.rollback()
        return {"status": "error", "task_id": task_id, "error": str(e)}
    finally:
        db.close()
