"""v2 Dataset API"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.deps import get_current_user
from app.services.v2.dataset_service import DatasetService

router = APIRouter(dependencies=[Depends(get_current_user)])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class DatasetResponse(BaseModel):
    id: str
    name: str
    kind: str
    class Config:
        from_attributes = True


def _check_upload_file(filename: str | None, content: bytes) -> str:
    """校验上传文件扩展名与大小，返回小写扩展名"""
    from app.config import settings

    ext = (filename or "").rsplit(".", 1)[-1].lower()
    allowed = {e.strip() for e in settings.allowed_upload_extensions.split(",") if e.strip()}
    if ext not in allowed:
        raise HTTPException(400, f"不支持的文件类型: .{ext} (允许: {settings.allowed_upload_extensions})")
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"文件超过大小限制 {settings.max_upload_mb}MB")
    return ext


def _estimate_rowcount(content: bytes, ext: str) -> int | None:
    """估算数据行数（CSV 按换行数-表头；XLSX 读工作表）"""
    try:
        if ext == "csv":
            return max(0, content.count(b"\n") - 1)
        if ext in ("xlsx", "xls") and content[:2] == b"PK":
            import io
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.active
            n = max(0, (ws.max_row or 1) - 1)
            wb.close()
            return n
    except Exception:
        pass
    return None


def _dataset_consumers(db: Session, dataset_id: str) -> list[dict]:
    """找出引用了该数据集的流水线（connector files 或 source_dataset_id）"""
    return _consumer_map(db).get(dataset_id, [])


def _consumer_map(db: Session) -> dict[str, list[dict]]:
    """一次扫描所有流水线，构建 dataset_id → 消费流水线列表 的映射"""
    from app.models.v2.pipeline import Pipeline

    mapping: dict[str, list[dict]] = {}

    def add(ds_id: str | None, pl: Pipeline):
        if not ds_id:
            return
        entry = {
            "id": pl.id,
            "name": pl.name,
            "status": pl.status or "draft",
            "domain": pl.domain or "通用",
        }
        bucket = mapping.setdefault(ds_id, [])
        if all(e["id"] != pl.id for e in bucket):
            bucket.append(entry)

    for pl in db.query(Pipeline).all():
        add(pl.source_dataset_id, pl)
        definition = pl.definition or {}
        for node in definition.get("nodes", []) or []:
            if node.get("type") != "connector":
                continue
            for fi in (node.get("config") or {}).get("files", []) or []:
                add(fi.get("dataset_id"), pl)
    return mapping


@router.post("/upload", status_code=201)
async def upload_dataset(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传 CSV/Excel 文件，自动创建 raw Dataset + DatasetVersion"""
    import os

    name = os.path.splitext(file.filename or "upload")[0]
    content = await file.read()
    ext = _check_upload_file(file.filename, content)
    # 推断 kind
    if ext in ("csv", "xlsx", "xls"):
        kind = "structured"
    elif ext in ("json", "xml"):
        kind = "semi"
    else:
        kind = "unstructured"

    svc = DatasetService(db)
    ds = svc.create_dataset(name=name, kind=kind)
    svc.create_version(ds.id, content, rowcount=_estimate_rowcount(content, ext))
    return {"data": {"id": ds.id, "name": ds.name, "kind": ds.kind, "dataset_type": "raw_dataset", "schema_type": "tabular"}}


@router.post("/{dataset_id}/upload", status_code=201)
async def upload_dataset_version(
    dataset_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """给已有数据集上传新数据文件，追加为新版本（数据集 ID 保持不变，
    流水线中的绑定不需要改动，下次运行自动读取最新版本）。"""
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    if ds.kind == "curated":
        raise HTTPException(400, "成品数据集由流水线生成，不支持手动上传数据")

    content = await file.read()
    ext = _check_upload_file(file.filename, content)

    # 记录旧列，供列变化提示
    old_rows = svc.preview(dataset_id, None, limit=1)
    old_cols = set(old_rows[0].keys()) if old_rows else set()

    ver = svc.create_version(dataset_id, content, rowcount=_estimate_rowcount(content, ext))

    new_rows = svc.preview(dataset_id, None, limit=1)
    new_cols = set(new_rows[0].keys()) if new_rows else set()
    columns_added = sorted(new_cols - old_cols) if old_cols else []
    columns_removed = sorted(old_cols - new_cols) if old_cols else []

    return {
        "dataset_id": dataset_id,
        "dataset_name": ds.name,
        "version_no": ver.version_no,
        "rowcount": ver.rowcount,
        "columns_added": columns_added,
        "columns_removed": columns_removed,
        "consumers": _dataset_consumers(db, dataset_id),
    }


@router.get("/overview")
def datasets_overview(db: Session = Depends(get_db)):
    """原始数据集总览（资产湖用）：版本、行数、来源、消费流水线"""
    from app.models.v2.dataset import DatasetVersion
    from app.models.v2.connection import Connection

    svc = DatasetService(db)
    datasets = [d for d in svc.list_datasets() if d.kind != "curated"]
    consumer_map = _consumer_map(db)
    conn_names = {c.id: c.name for c in db.query(Connection).all()}

    # 一次取全部版本，避免 N+1
    versions_by_ds: dict[str, list[DatasetVersion]] = {}
    for v in db.query(DatasetVersion).order_by(DatasetVersion.version_no).all():
        versions_by_ds.setdefault(v.dataset_id, []).append(v)

    items = []
    for ds in datasets:
        vers = versions_by_ds.get(ds.id, [])
        latest = vers[-1] if vers else None
        is_sync = ds.name.startswith("SYNC::") or bool(ds.source_connection_id)
        items.append({
            "id": ds.id,
            "name": ds.name.removeprefix("SYNC::"),
            "raw_name": ds.name,
            "kind": ds.kind,
            "source": "sync" if is_sync else "upload",
            "connection_name": conn_names.get(ds.source_connection_id or "", ""),
            "version_count": len(vers),
            "latest_version_no": latest.version_no if latest else 0,
            "rowcount": latest.rowcount if latest else None,
            "consumers": consumer_map.get(ds.id, []),
            "created_at": ds.created_at.isoformat() if ds.created_at else None,
            "updated_at": ds.updated_at.isoformat() if ds.updated_at else None,
        })
    items.sort(key=lambda x: x["updated_at"] or "", reverse=True)
    return {"items": items, "total": len(items)}


@router.get("/{dataset_id}/consumers")
def dataset_consumers(dataset_id: str, db: Session = Depends(get_db)):
    """查询哪些流水线使用该数据集作为数据源"""
    svc = DatasetService(db)
    if not svc.get_dataset(dataset_id):
        raise HTTPException(404, "Dataset not found")
    return {"dataset_id": dataset_id, "consumers": _dataset_consumers(db, dataset_id)}


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str, force: bool = False, db: Session = Depends(get_db)):
    """删除原始数据集及其版本。被流水线引用时返回 409（force=true 强制删除）。"""
    from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem

    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(404, "Dataset not found")
    if ds.kind == "curated":
        raise HTTPException(400, "成品数据集请在资产湖「成品数据集」中删除")

    consumers = _dataset_consumers(db, dataset_id)
    if consumers and not force:
        raise HTTPException(409, detail={
            "message": f"数据集被 {len(consumers)} 条流水线引用，删除后这些流水线将无法运行",
            "consumers": consumers,
        })

    ver_ids = [v.id for v in db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).all()]
    if ver_ids:
        db.query(MediaItem).filter(MediaItem.dataset_version_id.in_(ver_ids)).delete(synchronize_session=False)
    db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).delete(synchronize_session=False)
    db.delete(ds)
    db.commit()
    return {"status": "deleted", "id": dataset_id}

@router.get("", response_model=list[DatasetResponse])
def list_datasets(kind: str | None = None, db: Session = Depends(get_db)):
    svc = DatasetService(db)
    return svc.list_datasets(kind=kind)

@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)):
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return ds

@router.get("/{dataset_id}/versions")
def list_versions(dataset_id: str, db: Session = Depends(get_db)):
    svc = DatasetService(db)
    versions = svc.list_versions(dataset_id)
    return [{"id": v.id, "version_no": v.version_no, "rowcount": v.rowcount, "storage_uri": v.storage_uri} for v in versions]

@router.get("/{dataset_id}/versions/{version_no}/preview")
def preview_data(dataset_id: str, version_no: int, limit: int = 100, db: Session = Depends(get_db)):
    svc = DatasetService(db)
    return svc.preview(dataset_id, version_no, limit)


@router.get("/{dataset_id}/schema")
def get_schema(dataset_id: str, db: Session = Depends(get_db)):
    """返回数据集的 schema（列名、类型、样本值）"""
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    # Use latest version for schema inference
    versions = svc.list_versions(dataset_id)
    if not versions:
        return {"dataset_id": dataset_id, "columns": []}

    latest_version_no = versions[-1].version_no
    rows = svc.preview(dataset_id, latest_version_no, limit=10)
    if not rows:
        return {"dataset_id": dataset_id, "columns": []}

    columns = []
    all_keys = list(rows[0].keys()) if rows else []
    for key in all_keys:
        sample_values = [row.get(key) for row in rows if row.get(key) is not None][:5]
        # Infer type from sample values
        col_type = "string"
        for val in sample_values:
            if isinstance(val, bool):
                col_type = "boolean"
                break
            elif isinstance(val, int):
                col_type = "integer"
                break
            elif isinstance(val, float):
                col_type = "float"
                break
            elif isinstance(val, str):
                try:
                    int(val)
                    col_type = "integer"
                except ValueError:
                    try:
                        float(val)
                        col_type = "float"
                    except ValueError:
                        col_type = "string"
                break
        columns.append({"name": key, "type": col_type, "sample_values": sample_values})

    return {"dataset_id": dataset_id, "columns": columns}


@router.get("/{dataset_id}/stats")
def get_stats(dataset_id: str, db: Session = Depends(get_db)):
    """返回数据集统计信息"""
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    versions = svc.list_versions(dataset_id)
    version_count = len(versions)

    # Use latest version for row/column counts and null rates
    row_count = 0
    column_count = 0
    null_rates: dict = {}

    if versions:
        latest = versions[-1]
        row_count = latest.rowcount or 0
        rows = svc.preview(dataset_id, latest.version_no, limit=100)
        if rows:
            column_count = len(rows[0].keys())
            # Compute null rates per column
            for key in rows[0].keys():
                null_count = sum(1 for row in rows if row.get(key) is None or row.get(key) == "")
                null_rates[key] = round(null_count / len(rows), 4)

    return {
        "dataset_id": dataset_id,
        "row_count": row_count,
        "column_count": column_count,
        "null_rates": null_rates,
        "version_count": version_count,
    }


@router.get("/{dataset_id}/preview")
def preview_dataset(dataset_id: str, limit: int = 20, db: Session = Depends(get_db)):
    """预览数据集最新版本的前 N 行数据（默认20行）。"""
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    versions = svc.list_versions(dataset_id)
    if not versions:
        return {"dataset_id": dataset_id, "rows": [], "columns": [], "total_rows": 0}
    latest = versions[-1]
    rows = svc.preview(dataset_id, latest.version_no, limit=min(limit, 500))
    columns = list(rows[0].keys()) if rows else []
    return {
        "dataset_id": dataset_id,
        "dataset_name": ds.name,
        "version_no": latest.version_no,
        "total_rows": latest.rowcount or 0,
        "columns": columns,
        "rows": rows,
    }
