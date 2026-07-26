"""v2 Curated Dataset API — reads from v2_datasets kind=curated"""
from __future__ import annotations
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Optional
from app.database import SessionLocal
from app.deps import get_current_user, require_admin
from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


def _dispatch_approved_review(db: Session, review_id: str) -> dict:
    """返回与审核同事务落库的可靠自动化交接状态。

    实际 Mapping 由 DatasetVersion outbox 调度器执行；不能在审批事务提交后
    再依赖一次无持久凭据的 ``Celery.delay``，否则用户可能只看到“已审核”，
    却没有任何可恢复的下游任务。
    """
    try:
        from app.models.v2.dataset import DatasetVersionEvent
        review = db.query(CuratedReview).filter(
            CuratedReview.id == review_id).first()
        if review is None or not review.dataset_version_id:
            raise RuntimeError("审核未绑定可自动化的数据版本")
        event = db.query(DatasetVersionEvent).filter(
            DatasetVersionEvent.dataset_version_id == review.dataset_version_id,
            DatasetVersionEvent.event_type == "curated_review_approved",
        ).first()
        if event is None:
            raise RuntimeError("审核自动化 outbox 事件未创建")
        return {
            "status": "success" if event.status == "completed" else "queued",
            "event_id": event.id,
            "event_status": event.status,
            "durable": True,
        }
    except Exception as exc:  # 批准已落库，自动灌入失败必须可见、不可静默伪成功
        logger.exception(
            "Durable Mapping hand-off failed after review approve %s",
            review_id,
        )
        return {
            "status": "failed",
            "error": f"审核已批准，但自动灌入本体失败：{str(exc)[:500]}",
        }


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _require_current_approved_for_read(
    db: Session,
    dataset_id: str,
    *,
    action: str,
):
    """普通数据出口只允许读取当前且已批准的成品版本。

    pending/rejected 快照仍可通过 review-diff 按审核版本审计，但不能伪装成
    生产预览或普通导出。
    """
    from app.data_channel.curated.review_service import (
        ReviewApprovalError,
        latest_dataset_version,
        require_version_approved,
        version_review,
    )

    version = latest_dataset_version(db, dataset_id)
    if version is None:
        raise HTTPException(409, detail={
            "code": "dataset_version_not_approved",
            "message": f"该成品数据集尚无可用于{action}的数据版本。",
        })
    try:
        require_version_approved(db, dataset_id, version)
    except ReviewApprovalError as exc:
        review = version_review(db, dataset_id, version)
        rejected = review is not None and review.status == "rejected"
        raise HTTPException(409, detail={
            "code": (
                "dataset_version_rejected"
                if rejected else "dataset_version_not_approved"
            ),
            "message": (
                f"当前数据版本 v{version.version_no} 已拒绝，仅保留用于审核审计，"
                f"不能用于普通{action}或进入本体。"
                if rejected else
                f"当前数据版本 v{version.version_no} 尚未通过审核，"
                f"不能用于普通{action}或进入本体。"
            ),
            "dataset_version_id": version.id,
            "version_no": version.version_no,
            "review_status": review.status if review is not None else "pending_review",
        }) from exc
    return version


class CuratedDatasetResponse(BaseModel):
    id: str
    name: str
    status: str
    # Canonical lake identity contract.  Mapping clients must display this value
    # rather than asking users to define a second, potentially conflicting key.
    primary_key: str = ""
    row_count: Optional[int] = None
    quality_score: Optional[float] = None
    producer_pipeline_id: Optional[str] = None
    output_key: Optional[str] = None
    has_review_evidence: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


@router.get("")
def list_curated(
    db: Session = Depends(get_db),
    pipeline: str = "",
    task_id: str = "",
    status: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    paginated: bool = False,
):
    """列出成品数据集；分页模式固定按最近更新时间倒序。"""
    from app.models.v2.dataset import Dataset, DatasetVersion
    from app.models.v2.pipeline import Pipeline
    from app.data_channel.pipeline_tasks.models import PipelineTask

    q = db.query(Dataset).filter(Dataset.kind == "curated")
    pipeline_id = ""
    if task_id:
        task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
        pipeline_id = task.pipeline_id if task else "__missing_task__"
    elif pipeline:
        selected_pipeline = db.query(Pipeline).filter(or_(
            Pipeline.id == pipeline,
            Pipeline.name == pipeline,
        )).first()
        pipeline_id = selected_pipeline.id if selected_pipeline else pipeline

    if pipeline_id:
        selected_pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        legacy_target_ids = selected_pipeline.target_curated_ids or [] if selected_pipeline else []
        producer_filter = Dataset.producer_pipeline_id == pipeline_id
        if legacy_target_ids:
            q = q.filter(or_(producer_filter, Dataset.id.in_(legacy_target_ids)))
        else:
            q = q.filter(producer_filter)

    ordered = q.order_by(Dataset.updated_at.desc(), Dataset.id.desc())
    # 审核状态依赖“当前版本对应的审核记录”，无法只靠 Dataset 单表过滤；
    # 无状态筛选时直接在数据库层 offset/limit，有筛选时先解析真实状态再切页。
    if paginated and not status:
        total = q.count()
        rows = ordered.offset((page - 1) * page_size).limit(page_size).all()
    else:
        rows = ordered.all()
        total = len(rows)

    # Batch fetch all reviews for displayed datasets to avoid N+1
    dataset_ids = [r.id for r in rows]
    all_reviews = (db.query(CuratedReview).filter(
        CuratedReview.curated_dataset_id.in_(dataset_ids)
    ).order_by(CuratedReview.created_at.desc()).all()) if dataset_ids else []

    # 按数据集分组后仍需通过 review_matches_version 判断；历史 NULL 版本审核
    # 不能因为 ``NULL in (NULL, latest_id)`` 而永久泄漏到后续版本。
    reviews_by_dataset: dict[str, list] = {}
    for rev in all_reviews:
        reviews_by_dataset.setdefault(rev.curated_dataset_id, []).append(rev)
    result = []
    for r in rows:
        ver = db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == r.id
        ).order_by(DatasetVersion.version_no.desc()).first()
        # 从 schema_json 读质量分
        quality = None
        schema = r.schema_json if isinstance(r.schema_json, dict) else {}
        if schema:
            quality = schema.get("quality_score")
        from app.data_channel.datasets.lake_gate import split_pk
        from app.data_channel.curated.review_service import review_matches_version
        review = next((rev for rev in reviews_by_dataset.get(r.id, [])
                       if review_matches_version(rev, ver)), None)
        real_status = review.status if review else "pending_review"
        result.append(CuratedDatasetResponse(
            id=r.id, name=r.name,
            status=real_status,
            primary_key=",".join(split_pk(schema.get("primary_key"))),
            row_count=ver.rowcount if ver else None,
            quality_score=quality,
            producer_pipeline_id=r.producer_pipeline_id,
            output_key=r.output_key,
            has_review_evidence=bool(reviews_by_dataset.get(r.id)),
            created_at=r.created_at.isoformat() if r.created_at else None,
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
        ))
    if status:
        if status == "pending_review":
            result = [item for item in result if item.status in {"pending_review", "pending", "in_review"}]
        elif status == "reviewed":
            # 列表中的“审核状态”表达的是是否还有新数据需要人工处理，
            # 而不是审批决定本身。通过和拒绝都代表当前版本已经完成审核；
            # 具体决定仍保留在 CuratedReview.status 中作为不可变审计证据。
            result = [item for item in result if item.status in {"approved", "rejected"}]
        else:
            result = [item for item in result if item.status == status]
        total = len(result)
        if paginated:
            start = (page - 1) * page_size
            result = result[start:start + page_size]
    if paginated:
        return {"items": result, "total": total, "page": page, "page_size": page_size}
    return result


@router.delete("/{dataset_id}", status_code=204)
def delete_curated(dataset_id: str, force: bool = False,
                   db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """完整删除 Curated Dataset 及其审核、版本数据（仅管理员）。

    安全约束：
    - 删除数据集时同步删除其审核记录和行级修改；
    - 被流水线 / 本体映射引用时始终拦截；force 已禁用，避免外键层实际失败却
      向用户承诺“强删成功”。
    """
    from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem
    from app.data_channel.file_assets.models import PipelineFileAsset
    from app.data_channel.datasets.service import (
        drain_storage_deletion_outbox,
        enqueue_dataset_storage_deletions,
    )
    ds = db.query(Dataset).filter(Dataset.id == dataset_id, Dataset.kind == "curated").first()
    if not ds:
        raise HTTPException(404, "Curated dataset not found")

    if force:
        raise HTTPException(400, "force 强制删除已禁用；请先解除流水线和本体映射依赖")

    # 依赖检查：真实 FK 不允许绕过，API 也不再提供伪强删。
    from app.data_channel.datasets.router import _dataset_consumers
    from app.ontologies.mappings.consumers import dataset_mapping_bindings
    pipelines = _dataset_consumers(db, dataset_id)
    mappings = dataset_mapping_bindings(db, dataset_id)
    if pipelines or mappings:
        raise HTTPException(409, detail={
            "code": "in_use",
            "message": f"该数据集被 {len(pipelines)} 条流水线、{len(mappings)} 个本体映射引用，"
                       "请先解除依赖后再删除。",
            "pipelines": pipelines, "mappings": mappings,
        })

    # 完整清理审核证据、关联版本和媒体项。对象 URI 先在同一个数据库事务写入
    # outbox；提交成功后再物理删除，存储暂时不可用时保留任务供后续重试。
    enqueue_dataset_storage_deletions(db, dataset_id)
    review_ids = [review.id for review in db.query(CuratedReview.id).filter(
        CuratedReview.curated_dataset_id == dataset_id
    ).all()]
    if review_ids:
        db.query(CuratedRowEdit).filter(
            CuratedRowEdit.review_id.in_(review_ids)
        ).delete(synchronize_session=False)
        db.query(CuratedReview).filter(
            CuratedReview.curated_dataset_id == dataset_id
        ).delete(synchronize_session=False)
    ver_ids = [v.id for v in db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).all()]
    if ver_ids:
        db.query(MediaItem).filter(MediaItem.dataset_version_id.in_(ver_ids)).delete(synchronize_session=False)
        db.query(PipelineFileAsset).filter(
            PipelineFileAsset.dataset_version_id.in_(ver_ids)
        ).delete(synchronize_session=False)
    db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).delete(synchronize_session=False)
    db.delete(ds)
    db.commit()
    drain_storage_deletion_outbox(db)


@router.get("/{dataset_id}", response_model=CuratedDatasetResponse)
def get_curated(dataset_id: str, db: Session = Depends(get_db)):
    from app.models.v2.dataset import Dataset, DatasetVersion

    dataset = db.query(Dataset).filter(
        Dataset.id == dataset_id, Dataset.kind == "curated").first()
    if dataset:
        from app.data_channel.curated.review_service import current_version_review
        review = current_version_review(db, dataset_id)
        version = db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == dataset_id
        ).order_by(DatasetVersion.version_no.desc()).first()
        schema = dataset.schema_json if isinstance(dataset.schema_json, dict) else {}
        from app.data_channel.datasets.lake_gate import split_pk
        return CuratedDatasetResponse(
            id=dataset.id,
            name=dataset.name,
            status=review.status if review else "pending_review",
            primary_key=",".join(split_pk(schema.get("primary_key"))),
            row_count=version.rowcount if version else 0,
            quality_score=schema.get("quality_score"),
            producer_pipeline_id=dataset.producer_pipeline_id,
            output_key=dataset.output_key,
            has_review_evidence=db.query(CuratedReview).filter(
                CuratedReview.curated_dataset_id == dataset.id).first() is not None,
        )

    # legacy 只读兼容；新审核和写入已禁止再制造镜像记录。
    legacy = db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).first()
    if legacy:
        legacy_schema = legacy.schema_json if isinstance(legacy.schema_json, dict) else {}
        from app.data_channel.datasets.lake_gate import split_pk
        return CuratedDatasetResponse(
            id=legacy.id,
            name=legacy.name,
            status=legacy.status,
            primary_key=",".join(split_pk(legacy_schema.get("primary_key"))),
            row_count=legacy_schema.get("row_count"),
            quality_score=legacy.quality_score,
        )
    raise HTTPException(404, "Curated dataset not found")


@router.get("/{dataset_id}/preview")
def preview_curated(
    dataset_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """生产预览 — 仅读取当前已批准版本，并支持 offset/limit 分页。"""
    from app.models.v2.dataset import Dataset as Ds2

    d2 = db.query(Ds2).filter(Ds2.id == dataset_id, Ds2.kind == "curated").first()
    if not d2:
        raise HTTPException(404, "Curated dataset not found")
    name = d2.name
    version = _require_current_approved_for_read(
        db, dataset_id, action="预览")

    # 读最新版本数据（叠加行级审核编辑——预览必须与出口数据一致）
    try:
        from app.data_channel.curated.review_service import load_all_rows_with_edits
        all_rows = load_all_rows_with_edits(
            db, dataset_id, require_approved=True, version=version)
        total_rows = len(all_rows)
        rows = all_rows[offset:offset + limit]
        schema_columns = (d2.schema_json or {}).get("columns") or []
        columns = [
            str(item.get("name") or "") if isinstance(item, dict) else str(item)
            for item in schema_columns
        ]
        columns = [column for column in columns if column]
        if not columns and all_rows:
            columns = list(all_rows[0].keys())
        return {
            "dataset_id": dataset_id,
            "name": name,
            "rows": rows,
            "count": len(rows),
            "columns": columns,
            "total_rows": total_rows,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(rows) < total_rows,
        }
    except Exception as e:
        raise HTTPException(502, f"成品数据读取失败：{e}")


@router.get("/{dataset_id}/export")
def export_curated(
    dataset_id: str,
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    db: Session = Depends(get_db),
):
    """导出当前已批准成品版本全量数据，并叠加该版本已批准的行级修改。"""
    import io
    import json
    from urllib.parse import quote

    from app.models.v2.dataset import Dataset
    from app.services.v2.dataset_service import DatasetReadError, rows_to_csv_bytes
    from app.data_channel.curated.review_service import load_all_rows_with_edits

    dataset = db.query(Dataset).filter(
        Dataset.id == dataset_id,
        Dataset.kind == "curated",
    ).first()
    if not dataset:
        raise HTTPException(404, "Curated dataset not found")
    version = _require_current_approved_for_read(
        db, dataset_id, action="生产导出")

    try:
        rows = load_all_rows_with_edits(
            db, dataset_id, require_approved=True, version=version)
    except DatasetReadError as exc:
        raise HTTPException(502, f"成品数据导出失败：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(409, detail={
            "code": "review_edit_identity_error",
            "message": str(exc),
        }) from exc

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

    safe_name = "".join(c for c in dataset.name if c not in '\\/:*?"<>|').strip() or "成品数据集"
    filename = f"{safe_name}.{format}"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"

    if format == "csv":
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
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )


@router.get("/{dataset_id}/review-diff")
def review_diff(
    dataset_id: str,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    review_id: str | None = None,
                db: Session = Depends(get_db)):
    """审批三视角：① 变化量（相对上一版）② 上一版全量 ③ 本次全量（叠加审核编辑）。

    变化量 = compute_lake_impact(上一版全量, 本次全量)，按主键识别新增/更新/删除；
    无主键则退化为整行比对（只有新增/删除）。无上一版（v1 首次）→ ① 全为新增、② 空。
    审核者据此聚焦本次改动，而非盲审全量。
    """
    from app.models.v2.dataset import Dataset as Ds2
    from app.services.v2.dataset_service import DatasetService, DatasetReadError
    from app.data_channel.curated.review_service import (
        apply_all_row_edits, encode_row_pk, review_matches_version)
    from app.data_channel.pipeline_tasks.merge import compute_lake_impact
    from app.data_channel.datasets.lake_gate import split_pk

    d2 = db.query(Ds2).filter(Ds2.id == dataset_id, Ds2.kind == "curated").first()
    if not d2:
        raise HTTPException(404, "Curated dataset not found")
    schema = d2.schema_json if isinstance(d2.schema_json, dict) else {}
    pk_cols = split_pk(schema.get("primary_key"))

    svc = DatasetService(db)
    versions = svc.list_versions(dataset_id)  # 按 version_no 升序
    empty = {
        "version_no": None, "total": 0, "rows": [],
        "offset": offset, "limit": limit, "has_more": False,
    }
    if not versions:
        return {
            "pk": pk_cols,
            "row_pk_encoding": "plain-string" if len(pk_cols) == 1 else "json-array",
            "current_row_pks": [],
            "current": empty,
            "previous": empty,
            "delta": None,
        }

    selected_review = None
    if review_id:
        selected_review = db.query(CuratedReview).filter(
            CuratedReview.id == review_id,
            CuratedReview.curated_dataset_id == dataset_id).first()
        if not selected_review:
            raise HTTPException(404, "Review not found for this dataset")
    else:
        # 页面未显式传 review_id 时优先展示当前 pending 审核绑定的版本；否则
        # 新版本到达后页面会悄悄从 v1 跳到 v2，让审核者误以为仍在审原快照。
        selected_review = (db.query(CuratedReview).filter(
            CuratedReview.curated_dataset_id == dataset_id,
            CuratedReview.status == "pending")
            .order_by(CuratedReview.created_at.desc()).first())

    latest = versions[-1]
    current = latest
    if selected_review and selected_review.dataset_version_id:
        current = next(
            (v for v in versions if v.id == selected_review.dataset_version_id), None)
        if current is None:
            raise HTTPException(409, detail={
                "code": "review_version_unavailable",
                "message": "该审核绑定的数据版本已不存在或不可用，不能继续审核。",
                "dataset_version_id": selected_review.dataset_version_id,
            })
    elif selected_review:
        # 迁移前审核没有 version_id：按创建时间绑定到当时已存在的最新版本，
        # 不能把旧审核悄悄改为审阅当前最新版本。
        historical_versions = [
            version for version in versions
            if review_matches_version(selected_review, version)
        ]
        if historical_versions:
            current = historical_versions[-1]
    current_index = versions.index(current)
    prev = versions[current_index - 1] if current_index > 0 else None

    try:
        current_full = apply_all_row_edits(
            db, dataset_id, svc.load_all_rows(dataset_id, current.version_no),
            dataset_version_id=current.id,
            include_review_id=selected_review.id if selected_review else None)
        prev_full = (apply_all_row_edits(
            db, dataset_id, svc.load_all_rows(dataset_id, prev.version_no),
            dataset_version_id=prev.id) if prev else [])
    except DatasetReadError as e:
        raise HTTPException(422, f"版本数据读取失败：{e}")
    except ValueError as e:
        raise HTTPException(409, detail={
            "code": "review_edit_identity_error",
            "message": str(e),
        })

    delta = compute_lake_impact(prev_full, current_full, pk_cols, sample_limit=200)
    current_page = current_full[offset:offset + limit]
    current_row_pks: list[str | None] = []
    if pk_cols:
        for row in current_page:
            try:
                # 浏览器不应自行猜测/重现 Python 对 bool、float 等值的字符串化；
                # 审核写接口使用的 canonical row_pk 由同一后端编码器直接下发。
                current_row_pks.append(
                    encode_row_pk(row, pk_cols, dataset_name=d2.name))
            except ValueError:
                # 只禁用身份不完整的具体行，仍允许审核者查看整个版本并选择拒绝。
                current_row_pks.append(None)
    return {
        "pk": pk_cols,
        "row_pk_encoding": "plain-string" if len(pk_cols) == 1 else "json-array",
        "current_row_pks": current_row_pks,
        "current": {"version_no": current.version_no,
                    "dataset_version_id": current.id, "total": len(current_full),
                    "rows": current_page,
                    "offset": offset, "limit": limit,
                    "has_more": offset + limit < len(current_full)},
        "previous": {"version_no": prev.version_no if prev else None,
                     "total": len(prev_full),
                     "rows": prev_full[offset:offset + limit],
                     "offset": offset, "limit": limit,
                     "has_more": offset + limit < len(prev_full)},
        "delta": delta,
        "review": ({
            "id": selected_review.id,
            "dataset_version_id": selected_review.dataset_version_id,
            "status": selected_review.status,
            "stale": not review_matches_version(selected_review, latest),
            "latest_dataset_version_id": latest.id,
            "latest_version_no": latest.version_no,
        } if selected_review else None),
    }


@router.get("/{dataset_id}/quality")
def get_quality_report(dataset_id: str, db: Session = Depends(get_db)):
    """获取质量报告（支持旧 curated 表和新 v2_datasets curated）"""
    from app.services.v2.curated.quality_service import QualityService
    from app.services.v2.dataset_service import DatasetService

    from app.models.v2.dataset import Dataset as Ds2
    ds = db.query(Ds2).filter(Ds2.id == dataset_id, Ds2.kind == "curated").first()
    sample_data = []
    if ds:
        # 读 canonical 最新版本（叠加已批准编辑），质量分不能被 legacy mirror
        # 中陈旧的 sample_rows 覆盖。
        try:
            from app.data_channel.curated.review_service import load_rows_with_edits
            sample_data = load_rows_with_edits(db, dataset_id, limit=200)
        except Exception as e:
            raise HTTPException(502, f"质量评估读取数据失败：{e}")
    else:
        legacy = db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).first()
        if not legacy:
            raise HTTPException(404, "Curated dataset not found")
        if isinstance(legacy.schema_json, dict):
            sample_data = legacy.schema_json.get("sample_rows", [])

    svc = QualityService(db)
    report = svc.compute_report(dataset_id, sample_data)
    return report.to_dict()


@router.post("/{dataset_id}/review")
def submit_review(
    dataset_id: str,
    action: str,  # "approve" | "reject"
    notes: str = "",
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),  # PRD Security Logic: only admin can approve curated rows
):
    """提交审核结果（approve/reject）"""
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action 仅支持 approve 或 reject")
    # 兼容旧的一步审批端点，但内部仍必须先创建“绑定当前版本”的审核，随后
    # 再走统一的版本漂移检查，不能另造一条无 dataset_version_id 的旁路。
    # 若用户刚在详情页保存了行编辑，必须决定那一条 pending 审核；另开一条
    # 空审核会造成按钮显示“已批准”但人工修改永远没有被批准。
    from app.data_channel.curated.review_service import ReviewService
    svc = ReviewService(db)
    review = svc.start_review(dataset_id)
    review = svc.approve(review.id, notes) if action == "approve" else svc.reject(review.id, notes)

    dispatch = _dispatch_approved_review(db, review.id) if action == "approve" else None
    return {"review_id": review.id, "status": review.status,
            "mapping_dispatch": dispatch}


# ── 审核工作流端点 ─────────────────────────────────────────────────────

class BatchEditRequest(BaseModel):
    edits: list[dict]  # [{row_pk, field_name, old_value, new_value}]


@router.post("/{dataset_id}/reviews")
def start_review(dataset_id: str, db: Session = Depends(get_db)):
    """为数据集启动审核流程"""
    from app.services.v2.curated.review_service import ReviewService
    svc = ReviewService(db)
    review = svc.start_review(dataset_id)
    return {"review_id": review.id, "status": review.status,
            "dataset_version_id": review.dataset_version_id}


@router.get("/reviews/{review_id}")
def get_review(review_id: str, db: Session = Depends(get_db)):
    """获取审核记录详情"""
    from app.models.v2.curated import CuratedReview
    review = db.query(CuratedReview).filter(CuratedReview.id == review_id).first()
    if not review:
        raise HTTPException(404, "Review not found")
    return {
        "id": review.id,
        "curated_dataset_id": review.curated_dataset_id,
        "dataset_version_id": review.dataset_version_id,
        "status": review.status,
        "notes": review.notes,
        "decided_at": review.decided_at,
    }


@router.post("/reviews/{review_id}/edits")
def add_edit(review_id: str, body: BatchEditRequest, db: Session = Depends(get_db)):
    """批量提交行编辑"""
    from app.services.v2.curated.review_service import ReviewService
    svc = ReviewService(db)
    edits = svc.batch_edit_rows(review_id, body.edits)
    return {"saved": len(edits)}


@router.post("/reviews/{review_id}/approve")
def approve_review(review_id: str, notes: str = "", db: Session = Depends(get_db),
                   _admin=Depends(require_admin)):
    """审核通过"""
    from app.services.v2.curated.review_service import ReviewService
    svc = ReviewService(db)
    review = svc.approve(review_id, notes)
    return {
        "review_id": review.id,
        "status": review.status,
        "mapping_dispatch": _dispatch_approved_review(db, review.id),
    }


@router.post("/reviews/{review_id}/reject")
def reject_review(review_id: str, notes: str = "", db: Session = Depends(get_db),
                  _admin=Depends(require_admin)):
    """审核拒绝"""
    from app.services.v2.curated.review_service import ReviewService
    svc = ReviewService(db)
    review = svc.reject(review_id, notes)
    return {"review_id": review.id, "status": review.status}
