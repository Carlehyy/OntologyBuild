"""Dataset CRUD + 版本管理服务 (含 DuckDB 预览)"""
from __future__ import annotations
import hashlib
import json
import logging
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.v2.dataset import Dataset, DatasetVersion
from app.services.storage_service import StorageService, get_storage_service

logger = logging.getLogger(__name__)


class DatasetReadError(RuntimeError):
    """数据集内容读取/解析失败。

    与「数据集还没有数据」（返回空列表）严格区分：合并基座等场景把读失败
    当空列表处理，会让 append/upsert 把湖中存量折叠成本次增量。
    """


def rows_to_csv_bytes(rows: list[dict], columns: list[str]) -> bytes:
    """行列表 → CSV bytes（人工数据集在线编辑的写回路径）。

    列序由调用方给定（保持编辑前的展示顺序）；None → 空串，嵌套结构压 JSON。
    """
    import csv
    import io

    def _cell(v) -> str:
        if v is None:
            return ""
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", restval="")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: _cell(row.get(c)) for c in columns})
    return buf.getvalue().encode("utf-8")


def _parse_stored_rows(raw: bytes, limit: int | None, offset: int = 0) -> list[dict]:
    """把存储对象解析成行列表（JSON / Excel / CSV 自动检测）。

    limit=None 表示不设行数上限——合并基座必须全量，截断即丢数据。
    """
    offset = max(0, offset)
    text = raw.decode("utf-8", errors="replace").lstrip("﻿")

    # 检测是否 JSON
    stripped = text.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        data = json.loads(text)
        if isinstance(data, list):
            return data[offset:] if limit is None else data[offset:offset + limit]
        elif isinstance(data, dict):
            return [data] if offset == 0 else []
        return []

    # 检测是否 Excel (xlsx) 二进制格式
    if raw[:2] == b"PK":
        try:
            import openpyxl, io
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            rows_list = []
            headers = []
            data_idx = -1  # 数据行序号（不含表头）
            for ri, row in enumerate(ws.iter_rows(values_only=True)):
                if ri == 0:
                    headers = [str(c) if c is not None else f"col_{i}" for i, c in enumerate(row)]
                    continue
                data_idx += 1
                if data_idx < offset:
                    continue
                row_dict = {}
                for i, val in enumerate(row):
                    if i < len(headers):
                        row_dict[headers[i]] = val if val is not None else ""
                rows_list.append(row_dict)
                if limit is not None and len(rows_list) >= limit:
                    break
            wb.close()
            return rows_list
        except Exception:
            pass

    # 默认 CSV
    import csv, io
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for i, row in enumerate(reader):
        if i < offset:
            continue
        if limit is not None and len(rows) >= limit:
            break
        rows.append(dict(row))
    return rows


class DatasetService:
    def __init__(self, db: Session, storage: StorageService | None = None):
        self._db = db
        self._storage = storage or get_storage_service()

    def create_dataset(self, name: str, kind: str, connection_id: str | None = None,
                       schema_json: dict | None = None) -> Dataset:
        ds = Dataset(name=name, kind=kind, source_connection_id=connection_id,
                     schema_json=schema_json)
        self._db.add(ds)
        self._db.commit()
        self._db.refresh(ds)
        return ds

    def create_version(self, dataset_id: str, data: bytes, rowcount: int | None = None) -> DatasetVersion:
        """将数据存入 MinIO 并创建 DatasetVersion"""
        ds = self._db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if not ds:
            raise ValueError(f"Dataset {dataset_id} not found")

        checksum = hashlib.sha256(data).hexdigest()[:16]
        ver: DatasetVersion | None = None
        # 版本号 = 查最大值+1，并发写会撞唯一约束 (dataset_id, version_no)——
        # 撞了就重算重试，而不是静默产生重复版本号
        for attempt in range(3):
            last_ver = self._db.query(DatasetVersion).filter(
                DatasetVersion.dataset_id == dataset_id
            ).order_by(DatasetVersion.version_no.desc()).first()
            version_no = (last_ver.version_no + 1) if last_ver else 1

            key = f"datasets/{dataset_id}/v{version_no}/data.bin"
            uri = self._storage.put_bytes("raw-datasets", key, data)

            ver = DatasetVersion(
                dataset_id=dataset_id,
                version_no=version_no,
                rowcount=rowcount,
                storage_uri=uri,
                checksum=checksum,
            )
            self._db.add(ver)
            try:
                # id 的 uuid 默认值在 flush 时才生成——flush 前取 ver.id 是 None，
                # 会把 latest_version_id 永远写成 NULL（存量数据即如此）
                self._db.flush()
                ds.latest_version_id = ver.id
                self._db.commit()
                break
            except IntegrityError:
                self._db.rollback()
                if attempt == 2:
                    raise
        self._db.refresh(ver)
        self._prune_versions_best_effort(dataset_id)
        return ver

    def _prune_versions_best_effort(self, dataset_id: str) -> None:
        """机会式版本保留：只留最近 N 个全量快照，防止存储无限膨胀。

        任何失败都不影响本次写入；删不掉的对象保留记录，下次写入再试。
        """
        try:
            self._prune_versions(dataset_id)
        except Exception:
            self._db.rollback()
            logger.warning(f"数据集 {dataset_id} 旧版本清理失败（不影响本次写入）", exc_info=True)

    def _prune_versions(self, dataset_id: str) -> None:
        from app.config import settings
        from app.models.v2.dataset import MediaItem

        keep = int(getattr(settings, "dataset_version_keep", 0) or 0)
        if keep <= 0:
            return  # 0/负数 = 不清理
        versions = self._db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == dataset_id
        ).order_by(DatasetVersion.version_no.desc()).all()
        candidates = versions[keep:]
        if not candidates:
            return
        # 挂着媒体文件（OCR 等）的版本不清理，避免级联删除媒体记录
        media_ver_ids = {row[0] for row in self._db.query(MediaItem.dataset_version_id).filter(
            MediaItem.dataset_version_id.in_([v.id for v in candidates])).all()}

        removed = 0
        for v in candidates:
            if v.id in media_ver_ids:
                continue
            if v.storage_uri:
                try:
                    self._storage.delete_object(v.storage_uri)
                except Exception:
                    logger.warning(f"删除版本对象失败，保留记录下次再试: {v.storage_uri}", exc_info=True)
                    continue
            self._db.delete(v)
            removed += 1
        if removed:
            self._db.commit()
            logger.info(f"数据集 {dataset_id} 版本保留清理：删除 {removed} 个旧版本（保留最近 {keep} 个）")

    def get_dataset(self, dataset_id: str) -> Dataset | None:
        return self._db.query(Dataset).filter(Dataset.id == dataset_id).first()

    def list_datasets(self, kind: str | None = None) -> list[Dataset]:
        q = self._db.query(Dataset)
        if kind:
            q = q.filter(Dataset.kind == kind)
        return q.all()

    def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        return self._db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == dataset_id
        ).order_by(DatasetVersion.version_no).all()

    def _resolve_version(self, dataset_id: str, version_no: int | None) -> DatasetVersion | None:
        q = self._db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == dataset_id)
        if version_no is None:
            return q.order_by(DatasetVersion.version_no.desc()).first()
        return q.filter(DatasetVersion.version_no == version_no).first()

    def load_all_rows(self, dataset_id: str, version_no: int | None = None) -> list[dict]:
        """严格全量读：不设行数上限，读失败硬报错（DatasetReadError）。

        入湖合并基座、需要「必须完整」语义的消费方一律用这个，不要用
        preview()——它容错返回空列表，会把「读失败」伪装成「湖是空的」。
        数据集尚无版本/内容为空 → 返回 []（这是合法状态，不是错误）。
        """
        ver = self._resolve_version(dataset_id, version_no)
        if not ver or not ver.storage_uri:
            return []
        try:
            raw = self._storage.get_object(ver.storage_uri)
        except Exception as e:
            raise DatasetReadError(
                f"数据集 {dataset_id} v{ver.version_no} 存储对象读取失败"
                f"（{ver.storage_uri}）：{e}") from e
        if not raw:
            return []
        try:
            return _parse_stored_rows(raw, limit=None)
        except Exception as e:
            raise DatasetReadError(
                f"数据集 {dataset_id} v{ver.version_no} 内容解析失败：{e}") from e

    def preview(self, dataset_id: str, version_no: int | None,
                limit: int = 100, offset: int = 0) -> list[dict]:
        """CSV/JSON 数据预览。无需 DuckDB, 纯 Python 处理。

        version_no=None → 最新版本。消费方（映射/管道/质量报告）除非明确要
        历史版本，一律应传 None——增量同步产生 v2+ 后钉死 v1 会读到陈旧数据。

        offset：跳过前 N 行数据行（表头不计），配合 limit 做分页预览。
        容错语义：读取/解析失败返回 []，仅适合 UI 展示；「必须完整」的
        场景（如合并基座）用 load_all_rows()。
        """
        ver = self._resolve_version(dataset_id, version_no)
        if not ver or not ver.storage_uri:
            return []
        try:
            raw = self._storage.get_object(ver.storage_uri)
            return _parse_stored_rows(raw, limit=limit, offset=offset)
        except Exception:
            return []
