"""Celery tasks for asynchronous manual spreadsheet imports."""
from __future__ import annotations

from app.tasks.celery_app import celery_app


def _first_sheet_name(raw: bytes, extension: str) -> str:
    if raw[:2] == b"PK":
        import io
        import openpyxl

        workbook = openpyxl.load_workbook(
            io.BytesIO(raw), read_only=True, data_only=True, keep_links=False)
        try:
            return workbook.worksheets[0].title if workbook.worksheets else "第一个工作表"
        finally:
            workbook.close()
    if extension == "xls":
        import xlrd

        workbook = xlrd.open_workbook(file_contents=raw, on_demand=True)
        try:
            return workbook.sheet_by_index(0).name if workbook.nsheets else "第一个工作表"
        finally:
            workbook.release_resources()
    return "CSV"


@celery_app.task(name="app.tasks.v2.dataset_import.inspect_dataset_import")
def inspect_dataset_import(job_id: str) -> None:
    from app.data_channel.datasets.import_jobs import (
        read_manifest, source_path, update_status)
    from app.data_channel.datasets.lake_gate import infer_columns_typed
    from app.data_channel.datasets.service import _parse_stored_rows, stored_columns

    try:
        update_status(job_id, status="parsing", error=None)
        manifest = read_manifest(job_id)
        raw = source_path(job_id, manifest["extension"]).read_bytes()
        rows = _parse_stored_rows(raw, limit=None)
        columns = stored_columns(raw)
        if not columns:
            raise ValueError("表格为空，请至少保留一行列名")
        duplicate = next(
            (column for index, column in enumerate(columns)
             if column in columns[:index]),
            None,
        )
        if duplicate:
            raise ValueError(f"列名「{duplicate}」重复，请先修改表格表头")

        inferred = {
            item["name"]: item["type"]
            for item in infer_columns_typed(rows)
        }
        update_status(
            job_id,
            status="ready",
            sheet_name=_first_sheet_name(raw, manifest["extension"]),
            rowcount=len(rows),
            columns=[
                {"name": column, "type": inferred.get(column, "string")}
                for column in columns
            ],
            preview_rows=rows[:200],
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - task failures are reported to the owner
        update_status(job_id, status="failed", error=str(exc) or type(exc).__name__)


@celery_app.task(name="app.tasks.v2.dataset_import.commit_dataset_import")
def commit_dataset_import(job_id: str) -> None:
    from app.database import SessionLocal
    from app.data_channel.datasets.import_jobs import (
        read_manifest, read_metadata, source_path, update_status)
    from app.data_channel.datasets.router import (
        CreateTableRequest, _build_manual_schema, _validate_manual_rows)
    from app.data_channel.datasets.service import (
        _parse_stored_rows, stored_columns, DatasetService)

    db = SessionLocal()
    try:
        update_status(job_id, status="importing", error=None)
        manifest = read_manifest(job_id)
        body = CreateTableRequest.model_validate(read_metadata(job_id))
        name, schema = _build_manual_schema(body, origin="upload")
        raw = source_path(job_id, manifest["extension"]).read_bytes()
        rows = _parse_stored_rows(raw, limit=None)
        physical_columns = stored_columns(raw)
        expected_columns = schema["columns"]
        if physical_columns != expected_columns:
            raise ValueError(
                "上传文件列结构已发生变化"
                f"（文件：{physical_columns}；当前设置：{expected_columns}），请重新选择文件"
            )
        _validate_manual_rows(rows, schema, dataset_name=name, scope="上传数据")

        service = DatasetService(db)
        dataset = service.create_dataset(
            name=name, kind="structured", schema_json=schema, commit=False)
        version = service.create_version(
            dataset.id,
            raw,
            rowcount=len(rows),
            schema_json=schema,
            _lock_held=True,
        )
        update_status(job_id, status="completed", result={
            "id": dataset.id,
            "name": dataset.name,
            "kind": dataset.kind,
            "columns": schema["columns"],
            "primary_key": schema.get("primary_key", ""),
            "version_no": version.version_no,
            "rowcount": len(rows),
            "source": "upload",
        }, error=None)
    except Exception as exc:  # noqa: BLE001 - validation details belong in job status
        db.rollback()
        detail = getattr(exc, "detail", None)
        update_status(
            job_id,
            status="failed",
            error=str(detail if detail is not None else exc) or type(exc).__name__,
        )
    finally:
        db.close()
