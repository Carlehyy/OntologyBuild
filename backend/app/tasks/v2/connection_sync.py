"""
Connection 同步 — 把连接器数据落地为 Dataset 版本

把已配置的 Connection 通过其 Connector 拉取数据，序列化为 JSON 落成
Dataset + DatasetVersion，作为数据流水线的原始输入。

可同步执行（Celery/Redis 不可用时直接调用），也可作为 Celery 任务派发。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


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
    from app.models.v2.connection import Connection
    from app.models.v2.dataset import Dataset, DatasetVersion  # noqa: F401
    from app.services.connection.registry import get_connector
    from app.services.v2.dataset_service import DatasetService

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
            return {"status": "error", "error": f"connector init failed: {e}"}

        # 选资源
        res = resource
        if not res:
            try:
                resources = connector.list_resources()
                res = resources[0] if resources else ""
            except Exception:
                res = ""

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

        # 落 Dataset：复用同名连接数据集（按 connection_id 关联），追加版本
        ds_svc = DatasetService(db)
        ds = (db.query(Dataset)
              .filter(Dataset.source_connection_id == connection_id)
              .order_by(Dataset.created_at.desc()).first())
        if ds is None:
            ds_name = f"{conn.name}:{res}" if res else conn.name
            ds = ds_svc.create_dataset(name=ds_name, kind=kind,
                                       connection_id=connection_id)
        ver = ds_svc.create_version(ds.id, content, rowcount=rowcount)

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
    from app.models.v2.connection import Connection

    db = SessionLocal()
    try:
        conns = db.query(Connection).filter(Connection.status == "active").all()
        return [sync_connection(c.id, db=db) for c in conns]
    finally:
        db.close()
