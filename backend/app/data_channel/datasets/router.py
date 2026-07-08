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

    # 已声明主键契约的数据集：新文件先过三校验，坏身份的数据不落盘
    declared_pk = str((ds.schema_json or {}).get("primary_key") or "").strip()
    if declared_pk:
        from app.data_channel.datasets.lake_gate import LakeGateError, split_pk, validate_pk
        from app.data_channel.datasets.service import _parse_stored_rows
        try:
            new_rows = _parse_stored_rows(content, limit=None)
        except Exception as e:
            raise HTTPException(400, f"文件解析失败，无法校验主键契约：{e}")
        try:
            validate_pk(new_rows, split_pk(declared_pk), dataset_name=ds.name, scope="上传的新版本")
        except LakeGateError as e:
            raise HTTPException(400, str(e))

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


class ContractRequest(BaseModel):
    primary_key: str  # 逗号分隔支持复合主键


def _require_manual_dataset(ds, action: str):
    """人工维护类操作的准入：成品/同步数据集各有自己的维护通道。"""
    if ds.kind == "curated":
        raise HTTPException(400, f"成品数据集不支持{action}：主键契约由入湖闸门维护，行级修正请走审核编辑")
    if ds.name.startswith("SYNC::") or ds.source_connection_id:
        raise HTTPException(400, f"该数据集由同步任务维护，不支持{action}——人工改动会在下次同步时被覆盖")


@router.put("/{dataset_id}/contract")
def declare_contract(dataset_id: str, body: ContractRequest, db: Session = Depends(get_db)):
    """声明人工数据集的主键契约（存在·非空·唯一三校验，全量数据上验证）。

    声明后：上传新版本/在线编辑都会校验主键；本体映射可直接绑定该数据集，
    实例身份 = 主键值（否则退化为整行哈希，字段一变就堆积新实例）。
    已被本体映射绑定后主键锁定——改主键 = 整批实例身份作废。
    """
    from app.data_channel.datasets.lake_gate import (
        LakeGateError, infer_columns_typed, split_pk, validate_pk)
    from app.services.v2.dataset_service import DatasetReadError

    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    _require_manual_dataset(ds, "主键契约声明")

    pk_cols = split_pk(body.primary_key)
    if not pk_cols:
        raise HTTPException(400, "主键不能为空（支持逗号分隔的复合主键）")
    new_pk = ",".join(pk_cols)

    schema = dict(ds.schema_json or {})
    old_pk = str(schema.get("primary_key") or "").strip()
    if old_pk and old_pk != new_pk:
        from app.models.v2.mapping import OntologyMapping
        bound = db.query(OntologyMapping).filter(
            OntologyMapping.curated_dataset_id == dataset_id).count()
        if bound:
            raise HTTPException(400,
                f"该数据集已被 {bound} 条本体映射绑定，主键已锁定（改主键会让整批实例身份作废）。"
                f"如确需变更，请先删除相关映射")

    try:
        rows = svc.load_all_rows(dataset_id)
    except DatasetReadError as e:
        raise HTTPException(502, str(e))
    if rows:
        try:
            validate_pk(rows, pk_cols, dataset_name=ds.name, scope="现有数据")
        except LakeGateError as e:
            raise HTTPException(400, str(e))
        schema["columns"] = list(rows[0].keys())
        schema["columns_typed"] = infer_columns_typed(rows)

    schema["primary_key"] = new_pk
    schema["pk_source"] = "manual"
    ds.schema_json = schema  # 赋新 dict, 原地改 JSON 列不会被 SQLAlchemy 跟踪
    db.commit()
    return {"dataset_id": dataset_id, "primary_key": new_pk, "rows_validated": len(rows)}


class RowEditOp(BaseModel):
    key: dict | None = None     # 主键列→值（update/delete 用加载时的原值定位）
    values: dict | None = None  # update/insert 的列值


class RowEditsRequest(BaseModel):
    base_version_no: int  # 乐观并发：客户端所见的最新版本号
    updates: list[RowEditOp] = []
    inserts: list[RowEditOp] = []
    deletes: list[RowEditOp] = []


@router.post("/{dataset_id}/rows/edit")
def edit_rows(dataset_id: str, body: RowEditsRequest, db: Session = Depends(get_db)):
    """人工数据集在线维护：改单元格 / 新增行 / 删除行，整体生成一个新版本。

    update/delete 按声明的主键定位行（未声明主键只能追加）；编辑后的全量
    数据重新过主键三校验，坏身份的数据不落盘。base_version_no 不等于当前
    最新版本时返回 409——说明期间有人上传/编辑过，客户端须刷新重做。
    """
    from app.data_channel.datasets.lake_gate import LakeGateError, infer_columns_typed, split_pk, validate_pk
    from app.data_channel.datasets.lock import DatasetLockTimeout, dataset_write_lock
    from app.services.v2.dataset_service import DatasetReadError, rows_to_csv_bytes
    from app.models.v2.dataset import DatasetVersion

    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    _require_manual_dataset(ds, "在线编辑")
    if not (body.updates or body.inserts or body.deletes):
        raise HTTPException(400, "没有任何修改")

    schema = dict(ds.schema_json or {})
    pk_cols = split_pk(schema.get("primary_key"))
    if (body.updates or body.deletes) and not pk_cols:
        raise HTTPException(400, "修改/删除行需要先声明主键契约（用于定位行并保证身份稳定）；未声明主键时仅支持新增行")

    try:
        with dataset_write_lock(f"dataset::{dataset_id}", bind=db.get_bind(), wait_timeout=30):
            latest = db.query(DatasetVersion).filter(
                DatasetVersion.dataset_id == dataset_id).order_by(DatasetVersion.version_no.desc()).first()
            latest_no = latest.version_no if latest else 0
            if body.base_version_no != latest_no:
                raise HTTPException(409, detail={
                    "message": f"数据已更新到 v{latest_no}（你正在编辑的是 v{body.base_version_no}），请刷新后重新修改",
                    "current_version_no": latest_no,
                })

            try:
                rows = svc.load_all_rows(dataset_id)
            except DatasetReadError as e:
                raise HTTPException(502, str(e))

            columns = list(rows[0].keys()) if rows else list(schema.get("columns") or [])
            if not columns:
                # 空数据集首次插入：列 = 全部插入行值键的并集（保持出现顺序）
                for op in body.inserts:
                    for k in (op.values or {}).keys():
                        if k not in columns:
                            columns.append(k)
            if not columns:
                raise HTTPException(400, "无法确定列结构：数据集为空且插入行未携带任何列")

            def check_known_cols(values: dict, what: str):
                unknown = [k for k in values.keys() if k not in columns]
                if unknown:
                    raise HTTPException(400, f"{what}包含不存在的列 {unknown}（在线编辑不支持加列，请通过上传新版本调整列结构）")

            # 主键 → 行号 索引（用编辑前的快照定位，key 是客户端加载时的原值）
            pk_index: dict[tuple, int] = {}
            for i, r in enumerate(rows):
                kt = tuple(str(r.get(c, "") or "").strip() for c in pk_cols) if pk_cols else (i,)
                if pk_cols:
                    pk_index[kt] = i

            def locate(op: RowEditOp, what: str) -> int:
                key = op.key or {}
                missing = [c for c in pk_cols if str(key.get(c, "") or "").strip() == ""]
                if missing:
                    raise HTTPException(400, f"{what}缺少主键列 {missing} 的定位值")
                kt = tuple(str(key.get(c, "")).strip() for c in pk_cols)
                idx = pk_index.get(kt)
                if idx is None:
                    raise HTTPException(400, f"{what}未找到主键为 {dict(zip(pk_cols, kt))} 的行（可能已被删除，请刷新）")
                return idx

            work = [dict(r) for r in rows]
            for op in body.updates:
                check_known_cols(op.values or {}, "修改的行")
                work[locate(op, "修改")].update(op.values or {})
            tombstones = {locate(op, "删除") for op in body.deletes}
            new_rows = [r for i, r in enumerate(work) if i not in tombstones]
            for op in body.inserts:
                check_known_cols(op.values or {}, "新增的行")
                new_rows.append({c: (op.values or {}).get(c, "") for c in columns})

            if pk_cols:
                try:
                    validate_pk(new_rows, pk_cols, dataset_name=ds.name, scope="编辑后的数据")
                except LakeGateError as e:
                    raise HTTPException(400, str(e))

            ver = svc.create_version(dataset_id, rows_to_csv_bytes(new_rows, columns), rowcount=len(new_rows))
            if new_rows:
                schema["columns"] = columns
                schema["columns_typed"] = infer_columns_typed(new_rows)
                ds.schema_json = schema
            db.commit()
    except DatasetLockTimeout as e:
        raise HTTPException(423, str(e))

    return {
        "dataset_id": dataset_id,
        "version_no": ver.version_no,
        "rowcount": len(new_rows),
        "updated": len(body.updates),
        "inserted": len(body.inserts),
        "deleted": len(body.deletes),
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
            "primary_key": str((ds.schema_json or {}).get("primary_key") or ""),
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
def preview_dataset(dataset_id: str, limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
    """预览数据集最新版本的数据，支持 offset/limit 分页。默认前 20 行。"""
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    versions = svc.list_versions(dataset_id)
    if not versions:
        return {"dataset_id": dataset_id, "rows": [], "columns": [], "total_rows": 0,
                "offset": 0, "limit": limit}
    latest = versions[-1]
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    rows = svc.preview(dataset_id, latest.version_no, limit=limit, offset=offset)
    # 分页表头稳定性：offset>0 的页可能因该页某列全空而缺列，优先用契约列
    schema_cols = (ds.schema_json or {}).get("columns") if ds.schema_json else None
    columns = list(schema_cols) if schema_cols else (list(rows[0].keys()) if rows else [])
    return {
        "dataset_id": dataset_id,
        "dataset_name": ds.name,
        "version_no": latest.version_no,
        "total_rows": latest.rowcount or 0,
        "offset": offset,
        "limit": limit,
        "columns": columns,
        "rows": rows,
    }
