"""
Connection 同步 — 把连接器数据落地为 Dataset 版本

把已配置的 Connection 通过其 Connector 拉取数据，序列化为 JSON 落成
Dataset + DatasetVersion，作为数据流水线的原始输入。

支持调用方显式同步执行，也支持经 NATS executor 异步派发。
"""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import nullcontext

logger = logging.getLogger(__name__)

_RESOURCE_ID_MAX_LENGTH = 500


def _decrypt_config(conn) -> dict:
    """解密 Connection.config。"""
    from app.services import encryption_service
    raw = (conn.config or {}).get("_encrypted", "")
    if not raw:
        return conn.config or {}
    try:
        return json.loads(encryption_service.decrypt(raw))
    except Exception:
        return conn.config or {}


def sync_connection(connection_id: str, mode: str = "full",
                    resource: str | None = None, db=None) -> dict:
    """
    同步 Connection 数据，落地为 Dataset 版本。

    Args:
        connection_id: 要同步的 Connection ID
        mode: "full" | "delta"
        resource: 指定资源（端点/表/集合）；缺省取连接器第一个资源
        db: 可选外部 session（不传则自建）

    Returns:
        {"status": "ok", "rows": int, "dataset_id": str, "version_no": int}
        或 {"status": "error", "error": str}
    """
    from app.database import SessionLocal
    from app.data_channel.connections.models import Connection
    from app.data_channel.datasets.models import Dataset, DatasetVersion  # noqa: F401
    from app.services.connection.registry import get_connector
    from app.data_channel.datasets.service import DatasetService

    own_db = db is None
    db = db or SessionLocal()
    try:
        conn = db.query(Connection).filter(Connection.id == connection_id).first()
        if not conn:
            return {"status": "error", "error": f"Connection {connection_id} not found"}

        config = _decrypt_config(conn)
        try:
            connector = get_connector(conn.kind, config)
        except Exception as e:
            conn.status = "error"
            db.commit()
            return {"status": "error", "error": f"connector init failed: {e}"}

        # 选资源
        res = resource
        if not res:
            try:
                resources = connector.list_resources()
                res = resources[0] if resources else ""
            except Exception:
                res = ""

        # resource 是 Connection 内部的数据集身份，不是展示名称。保持原字符串
        # （不 strip/改大小写），同时拒绝无法被 schema 无损保存的连接器返回值。
        if not isinstance(res, str):
            conn.status = "error"
            db.commit()
            return {
                "status": "error",
                "error": "connector resource identity must be a string",
            }
        if not res:
            conn.status = "error"
            db.commit()
            return {
                "status": "error",
                "error": "connector did not provide a resource to synchronize",
            }
        if len(res) > _RESOURCE_ID_MAX_LENGTH:
            conn.status = "error"
            db.commit()
            return {
                "status": "error",
                "error": (
                    "connector resource identity exceeds "
                    f"{_RESOURCE_ID_MAX_LENGTH} characters"
                ),
            }

        # 拉数据
        try:
            if mode == "delta":
                rows = connector.pull_delta(res)
            else:
                rows = connector.pull_full(res)
        except Exception as e:
            conn.status = "error"
            db.commit()
            return {"status": "error", "error": f"pull failed: {e}"}

        # 归一化为行列表
        if isinstance(rows, bytes):
            content = rows
            rowcount = None
            kind = "unstructured"
        elif isinstance(rows, list):
            content = json.dumps(rows, ensure_ascii=False).encode("utf-8")
            rowcount = len(rows)
            kind = "structured"
        else:
            content = json.dumps(rows, ensure_ascii=False, default=str).encode("utf-8")
            rowcount = None
            kind = "semi"

        # 解析/首建使用稳定的 connection+resource 锁，避免两个进程同时制造双胞胎。
        # 已有数据集还要进入 dataset 锁，与上传/其他写入共享完整版本序列。
        from app.data_channel.datasets.lock import dataset_write_lock
        ds_svc = DatasetService(db)
        resource_digest = hashlib.sha256(res.encode("utf-8")).hexdigest()
        stable_key = f"connection-sync::{connection_id}::{resource_digest}"
        try:
            with dataset_write_lock(stable_key, bind=db.get_bind(), wait_timeout=30):
                ds = (db.query(Dataset)
                      .filter(
                          Dataset.source_connection_id == connection_id,
                          Dataset.source_resource == res,
                      )
                      .order_by(Dataset.created_at.desc()).first())
                if ds is None:
                    ds_name = f"{conn.name}:{res}" if res else conn.name
                    # name 只用于展示；截断不能影响由 connection+resource 保存的身份。
                    if len(ds_name) > 200:
                        ds_name = f"{ds_name[:197]}..."
                    ds = ds_svc.create_dataset(
                        name=ds_name, kind=kind, connection_id=connection_id,
                        source_resource=res,
                        commit=False)
                    version_guard = nullcontext()
                else:
                    version_guard = dataset_write_lock(
                        f"dataset::{ds.id}", bind=db.get_bind(), wait_timeout=30)
                with version_guard:
                    ver = ds_svc.create_version(
                        ds.id, content, rowcount=rowcount, _lock_held=True)
        except Exception:
            db.rollback()
            failed_conn = db.query(Connection).filter(
                Connection.id == connection_id).first()
            if failed_conn is not None:
                failed_conn.status = "error"
                db.commit()
            raise

        conn.status = "active"
        db.commit()
        return {
            "status": "ok",
            "rows": rowcount if rowcount is not None else 0,
            "dataset_id": ds.id,
            "version_no": ver.version_no,
            "resource": res,
        }
    finally:
        if own_db:
            db.close()


def sync_all_connections() -> list[dict]:
    """顺序同步所有处于激活状态的 Connection。"""
    from app.database import SessionLocal
    from app.data_channel.connections.models import Connection

    db = SessionLocal()
    try:
        conns = db.query(Connection).filter(Connection.status == "active").all()
        return [sync_connection(c.id, db=db) for c in conns]
    finally:
        db.close()
