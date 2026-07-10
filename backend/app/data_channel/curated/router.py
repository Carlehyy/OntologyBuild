"""v2 Curated Dataset API — reads from v2_datasets kind=curated"""
from __future__ import annotations
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from app.database import SessionLocal
from app.deps import get_current_user, require_admin
from app.models.v2.curated import CuratedDataset, CuratedReview

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class CuratedDatasetResponse(BaseModel):
    id: str
    name: str
    status: str
    row_count: Optional[int] = None
    quality_score: Optional[float] = None

    class Config:
        from_attributes = True


@router.get("", response_model=list[CuratedDatasetResponse])
def list_curated(db: Session = Depends(get_db)):
    """列出所有 Curated Dataset（从 v2_datasets 读 kind=curated）"""
    from app.models.v2.dataset import Dataset, DatasetVersion
    rows = db.query(Dataset).filter(Dataset.kind == "curated").order_by(Dataset.created_at.desc()).all()
    # Batch fetch all reviews for displayed datasets to avoid N+1
    dataset_ids = [r.id for r in rows]
    all_reviews = db.query(CuratedReview).filter(
        CuratedReview.curated_dataset_id.in_(dataset_ids)
    ).order_by(CuratedReview.created_at.desc()).all()

    result = []
    for r in rows:
        ver = db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == r.id
        ).order_by(DatasetVersion.version_no.desc()).first()
        # 从 schema_json 读质量分
        quality = None
        if r.schema_json and isinstance(r.schema_json, dict):
            quality = r.schema_json.get("quality_score")
        from app.data_channel.curated.review_service import review_matches_version
        candidates = [rev for rev in all_reviews if rev.curated_dataset_id == r.id]
        review = next((rev for rev in candidates if review_matches_version(rev, ver)), None)
        real_status = review.status if review else "pending_review"
        result.append(CuratedDatasetResponse(
            id=r.id, name=r.name,
            status=real_status,
            row_count=ver.rowcount if ver else None,
            quality_score=quality,
        ))
    return result


@router.delete("/{dataset_id}", status_code=204)
def delete_curated(dataset_id: str, force: bool = False,
                   db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """删除 Curated Dataset 及其版本数据（仅管理员）。

    安全约束：
    - 已审批通过（approved）的数据集不可删除——审批即背书，删除会让审批失去意义；
      如确需删除，请先在审核中「拒绝」撤回审批后再删。此约束不受 force 影响。
    - 被流水线 / 本体映射引用时默认拦截（force=true 强制删除，下游将断源）。
    """
    from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem
    ds = db.query(Dataset).filter(Dataset.id == dataset_id, Dataset.kind == "curated").first()
    if not ds:
        raise HTTPException(404, "Curated dataset not found")

    # 已审批通过 → 硬禁止删除（force 也不能绕）
    from app.data_channel.curated.review_service import current_version_review
    latest_review = current_version_review(db, dataset_id)
    if latest_review and latest_review.status == "approved":
        raise HTTPException(409, detail={
            "code": "approved_locked",
            "message": "该成品数据集已审批通过，不可删除。审批即背书，如确需删除请先在审核中「拒绝」撤回审批。",
        })

    # 依赖检查：被流水线 / 本体映射引用时拦截（force 可绕）
    if not force:
        from app.data_channel.datasets.router import _dataset_consumers
        from app.ontologies.mappings.consumers import dataset_mapping_bindings
        pipelines = _dataset_consumers(db, dataset_id)
        mappings = dataset_mapping_bindings(db, dataset_id)
        if pipelines or mappings:
            raise HTTPException(409, detail={
                "code": "in_use",
                "message": f"该数据集被 {len(pipelines)} 条流水线、{len(mappings)} 个本体映射引用，"
                           f"删除会导致下游断源。确认无碍可强制删除（force=true）。",
                "pipelines": pipelines, "mappings": mappings,
            })

    # 清理关联版本和媒体项
    ver_ids = [v.id for v in db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).all()]
    if ver_ids:
        db.query(MediaItem).filter(MediaItem.dataset_version_id.in_(ver_ids)).delete(synchronize_session=False)
    db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).delete(synchronize_session=False)
    # 清理审核记录
    db.query(CuratedReview).filter(CuratedReview.curated_dataset_id == dataset_id).delete(synchronize_session=False)
    db.delete(ds)
    db.commit()


@router.get("/{dataset_id}", response_model=CuratedDatasetResponse)
def get_curated(dataset_id: str, db: Session = Depends(get_db)):
    # 旧 curated 表仅保存兼容字段；只要真实 Dataset 存在，状态必须按当前
    # DatasetVersion 的审批计算，不能返回镜像表里从 v1 遗留的 approved。
    ds = db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).first()
    from app.models.v2.dataset import Dataset, DatasetVersion
    d2 = db.query(Dataset).filter(
        Dataset.id == dataset_id, Dataset.kind == "curated").first()
    if d2:
        from app.data_channel.curated.review_service import current_version_review
        review = current_version_review(db, dataset_id)
        version = db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == dataset_id
        ).order_by(DatasetVersion.version_no.desc()).first()
        quality = ((d2.schema_json or {}).get("quality_score")
                   if isinstance(d2.schema_json, dict)
                   else getattr(ds, "quality_score", None))
        return CuratedDatasetResponse(
            id=d2.id, name=d2.name,
            status=review.status if review else "pending_review",
            row_count=version.rowcount if version else None,
            quality_score=quality)
    if not ds:
        raise HTTPException(404, "Curated dataset not found")
    return ds


@router.get("/{dataset_id}/preview")
def preview_curated(dataset_id: str, limit: int = 100, db: Session = Depends(get_db)):
    """数据预览 — 从 v2_datasets 存储读取实际数据行"""
    from app.services.v2.dataset_service import DatasetService
    from app.models.v2.dataset import Dataset as Ds2

    # 尝试旧 curated 表
    ds = db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).first()
    if ds:
        name = ds.name
    else:
        d2 = db.query(Ds2).filter(Ds2.id == dataset_id, Ds2.kind == "curated").first()
        if not d2:
            raise HTTPException(404, "Curated dataset not found")
        name = d2.name

    # 读最新版本数据（叠加行级审核编辑——预览必须与出口数据一致）
    try:
        from app.data_channel.curated.review_service import load_rows_with_edits
        rows = load_rows_with_edits(db, dataset_id, limit=limit)
        return {"dataset_id": dataset_id, "name": name, "rows": rows, "count": len(rows)}
    except Exception as e:
        return {"dataset_id": dataset_id, "name": name, "rows": [], "count": 0, "error": str(e)}


@router.get("/{dataset_id}/review-diff")
def review_diff(dataset_id: str, limit: int = 500, db: Session = Depends(get_db)):
    """审批三视角：① 变化量（相对上一版）② 上一版全量 ③ 本次全量（叠加审核编辑）。

    变化量 = compute_lake_impact(上一版全量, 本次全量)，按主键识别新增/更新/删除；
    无主键则退化为整行比对（只有新增/删除）。无上一版（v1 首次）→ ① 全为新增、② 空。
    审核者据此聚焦本次改动，而非盲审全量。
    """
    from app.models.v2.dataset import Dataset as Ds2
    from app.services.v2.dataset_service import DatasetService, DatasetReadError
    from app.data_channel.curated.review_service import apply_all_row_edits
    from app.data_channel.pipeline_tasks.merge import compute_lake_impact
    from app.data_channel.datasets.lake_gate import split_pk

    d2 = db.query(Ds2).filter(Ds2.id == dataset_id, Ds2.kind == "curated").first()
    if not d2:
        raise HTTPException(404, "Curated dataset not found")
    schema = d2.schema_json if isinstance(d2.schema_json, dict) else {}
    pk_cols = split_pk(schema.get("primary_key"))

    svc = DatasetService(db)
    versions = svc.list_versions(dataset_id)  # 按 version_no 升序
    empty = {"version_no": None, "total": 0, "rows": []}
    if not versions:
        return {"pk": pk_cols, "current": empty, "previous": empty, "delta": None}
    latest = versions[-1]
    prev = versions[-2] if len(versions) >= 2 else None

    try:
        current_full = apply_all_row_edits(
            db, dataset_id, svc.load_all_rows(dataset_id, latest.version_no))
        prev_full = svc.load_all_rows(dataset_id, prev.version_no) if prev else []
    except DatasetReadError as e:
        raise HTTPException(422, f"版本数据读取失败：{e}")

    delta = compute_lake_impact(prev_full, current_full, pk_cols, sample_limit=200)
    return {
        "pk": pk_cols,
        "current": {"version_no": latest.version_no, "total": len(current_full),
                    "rows": current_full[:limit]},
        "previous": {"version_no": prev.version_no if prev else None,
                     "total": len(prev_full), "rows": prev_full[:limit]},
        "delta": delta,
    }


@router.get("/{dataset_id}/quality")
def get_quality_report(dataset_id: str, db: Session = Depends(get_db)):
    """获取质量报告（支持旧 curated 表和新 v2_datasets curated）"""
    from app.services.v2.curated.quality_service import QualityService
    from app.services.v2.dataset_service import DatasetService

    # 查旧 curated 表
    ds = db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).first()
    sample_data = []
    if ds:
        if ds.schema_json and isinstance(ds.schema_json, dict):
            sample_data = ds.schema_json.get("sample_rows", [])
    else:
        # 尝试从 v2_datasets 读取样本数据
        from app.models.v2.dataset import Dataset as Ds2
        d2 = db.query(Ds2).filter(Ds2.id == dataset_id, Ds2.kind == "curated").first()
        if not d2:
            raise HTTPException(404, "Curated dataset not found")
        # 读最新版本数据作为样本（叠加行级审核编辑，质量分反映修正后的数据）
        try:
            from app.data_channel.curated.review_service import load_rows_with_edits
            sample_data = load_rows_with_edits(db, dataset_id, limit=200)
        except Exception:
            sample_data = []

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
    from app.models.v2.dataset import Dataset
    ds = db.query(Dataset).filter(
        Dataset.id == dataset_id, Dataset.kind == "curated").first()
    if not ds:
        raise HTTPException(404, "Curated dataset not found")

    from datetime import datetime, timezone
    from app.data_channel.curated.review_service import latest_dataset_version
    version = latest_dataset_version(db, dataset_id)
    if version is None:
        raise HTTPException(409, "Curated dataset has no version to review")
    review = CuratedReview(
        curated_dataset_id=dataset_id,
        dataset_version_id=version.id if version else None,
        status="approved" if action == "approve" else "rejected",
        notes=notes,
        decided_at=datetime.now(timezone.utc),
    )
    db.add(review)
    db.commit()
    db.refresh(review)

    if action == "approve":
        try:
            from app.services.v2.incremental.orchestrator import IncrementalOrchestrator
            orch = IncrementalOrchestrator(db)
            orch.on_review_approved(review.id)
        except Exception as e:
            logger.warning(f"Mapping trigger failed after review approve {review.id}: {e}")

    return {"review_id": review.id, "status": review.status}


# ── 审核工作流端点 ─────────────────────────────────────────────────────

class BatchEditRequest(BaseModel):
    edits: list[dict]  # [{row_pk, field_name, old_value, new_value}]


@router.post("/{dataset_id}/reviews")
def start_review(dataset_id: str, db: Session = Depends(get_db)):
    """为数据集启动审核流程"""
    from app.services.v2.curated.review_service import ReviewService
    svc = ReviewService(db)
    review = svc.start_review(dataset_id)
    return {"review_id": review.id, "status": review.status}


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
    return {"review_id": review.id, "status": review.status}


@router.post("/reviews/{review_id}/reject")
def reject_review(review_id: str, notes: str = "", db: Session = Depends(get_db),
                  _admin=Depends(require_admin)):
    """审核拒绝"""
    from app.services.v2.curated.review_service import ReviewService
    svc = ReviewService(db)
    review = svc.reject(review_id, notes)
    return {"review_id": review.id, "status": review.status}
