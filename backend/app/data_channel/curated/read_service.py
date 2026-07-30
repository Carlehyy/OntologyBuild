"""Approved curated-data preview and export workflows."""

from __future__ import annotations

import io
import json
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.data_channel.curated.approved_version_reader import (
    ReviewApprovalError,
    latest_dataset_version,
    load_all_rows_with_edits,
    require_version_approved,
    version_review,
)
from app.models.v2.dataset import Dataset
from app.services.v2.dataset_service import DatasetReadError, rows_to_csv_bytes


def require_current_approved_for_read(
    db: Session,
    dataset_id: str,
    *,
    action: str,
):
    """Require the canonical current version to have approval evidence."""
    version = latest_dataset_version(db, dataset_id)
    if version is None:
        raise HTTPException(
            409,
            detail={
                "code": "dataset_version_not_approved",
                "message": f"该成品数据集尚无可用于{action}的数据版本。",
            },
        )
    try:
        require_version_approved(db, dataset_id, version)
    except ReviewApprovalError as exc:
        review = version_review(db, dataset_id, version)
        rejected = review is not None and review.status == "rejected"
        raise HTTPException(
            409,
            detail={
                "code": (
                    "dataset_version_rejected"
                    if rejected
                    else "dataset_version_not_approved"
                ),
                "message": (
                    (
                        f"当前数据版本 v{version.version_no} 已拒绝，"
                        f"仅保留用于审核审计，不能用于普通{action}或进入本体。"
                    )
                    if rejected
                    else (
                        f"当前数据版本 v{version.version_no} 尚未通过审核，"
                        f"不能用于普通{action}或进入本体。"
                    )
                ),
                "dataset_version_id": version.id,
                "version_no": version.version_no,
                "review_status": (
                    review.status if review is not None else "pending_review"
                ),
            },
        ) from exc
    return version


def preview_curated(
    db: Session,
    dataset_id: str,
    *,
    limit: int,
    offset: int,
) -> dict:
    """Return a page from the approved current version."""
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.kind == "curated")
        .first()
    )
    if not dataset:
        raise HTTPException(404, "Curated dataset not found")
    version = require_current_approved_for_read(
        db,
        dataset_id,
        action="预览",
    )

    try:
        all_rows = load_all_rows_with_edits(
            db,
            dataset_id,
            require_approved=True,
            version=version,
        )
        total_rows = len(all_rows)
        rows = all_rows[offset : offset + limit]
        schema_columns = (dataset.schema_json or {}).get("columns") or []
        columns = [
            (
                str(item.get("name") or "")
                if isinstance(item, dict)
                else str(item)
            )
            for item in schema_columns
        ]
        columns = [column for column in columns if column]
        if not columns and all_rows:
            columns = list(all_rows[0].keys())
        return {
            "dataset_id": dataset_id,
            "name": dataset.name,
            "rows": rows,
            "count": len(rows),
            "columns": columns,
            "total_rows": total_rows,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(rows) < total_rows,
        }
    except Exception as exc:
        raise HTTPException(502, f"成品数据读取失败：{exc}") from exc


def export_curated(
    db: Session,
    dataset_id: str,
    *,
    output_format: str,
) -> StreamingResponse:
    """Export all rows from the approved current version as CSV or XLSX."""
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.kind == "curated")
        .first()
    )
    if not dataset:
        raise HTTPException(404, "Curated dataset not found")
    version = require_current_approved_for_read(
        db,
        dataset_id,
        action="生产导出",
    )

    try:
        rows = load_all_rows_with_edits(
            db,
            dataset_id,
            require_approved=True,
            version=version,
        )
    except DatasetReadError as exc:
        raise HTTPException(502, f"成品数据导出失败：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(
            409,
            detail={
                "code": "review_edit_identity_error",
                "message": str(exc),
            },
        ) from exc

    schema = dataset.schema_json if isinstance(dataset.schema_json, dict) else {}
    columns: list[str] = []

    def add_column(value) -> None:
        name = str(value or "").strip()
        if name and name not in columns:
            columns.append(name)

    for item in schema.get("columns") or []:
        add_column(item.get("name") if isinstance(item, dict) else item)
    for item in schema.get("columns_typed") or []:
        if isinstance(item, dict):
            add_column(item.get("name"))
    for row in rows:
        for name in row:
            add_column(name)

    safe_name = (
        "".join(
            character
            for character in dataset.name
            if character not in '\\/:*?"<>|'
        ).strip()
        or "成品数据集"
    )
    filename = f"{safe_name}.{output_format}"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"

    if output_format == "csv":
        data = b"\xef\xbb\xbf" + rows_to_csv_bytes(rows, columns)
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": disposition},
        )

    import openpyxl

    workbook = openpyxl.Workbook(write_only=True)
    sheet = workbook.create_sheet(title="数据")
    sheet.append(columns)
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column, "")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            values.append(value)
        sheet.append(values)
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": disposition},
    )
