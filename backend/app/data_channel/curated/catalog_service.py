"""Catalog queries and projections for curated datasets."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.data_channel.curated.contracts import CuratedDatasetResponse
from app.data_channel.curated.approved_version_reader import (
    current_version_review,
    review_matches_version,
)
from app.data_channel.datasets.lake_gate import split_pk
from app.data_channel.pipeline_tasks.models import PipelineTask
from app.models.v2.curated import CuratedDataset, CuratedReview
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.pipeline import Pipeline


def list_curated(
    db: Session,
    *,
    pipeline: str,
    task_id: str,
    status: str,
    page: int,
    page_size: int,
    paginated: bool,
):
    """List curated datasets with current-version review state."""
    query = db.query(Dataset).filter(Dataset.kind == "curated")
    pipeline_id = ""
    if task_id:
        task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
        pipeline_id = task.pipeline_id if task else "__missing_task__"
    elif pipeline:
        selected_pipeline = (
            db.query(Pipeline)
            .filter(or_(Pipeline.id == pipeline, Pipeline.name == pipeline))
            .first()
        )
        pipeline_id = selected_pipeline.id if selected_pipeline else pipeline

    if pipeline_id:
        selected_pipeline = (
            db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        )
        legacy_target_ids = (
            selected_pipeline.target_curated_ids or []
            if selected_pipeline
            else []
        )
        producer_filter = Dataset.producer_pipeline_id == pipeline_id
        if legacy_target_ids:
            query = query.filter(
                or_(producer_filter, Dataset.id.in_(legacy_target_ids))
            )
        else:
            query = query.filter(producer_filter)

    ordered = query.order_by(Dataset.updated_at.desc(), Dataset.id.desc())
    # Review state is bound to the current version and cannot be represented by
    # Dataset alone.  Apply database pagination only when no state filter exists.
    if paginated and not status:
        total = query.count()
        rows = (
            ordered.offset((page - 1) * page_size).limit(page_size).all()
        )
    else:
        rows = ordered.all()
        total = len(rows)

    dataset_ids = [row.id for row in rows]
    all_reviews = (
        db.query(CuratedReview)
        .filter(CuratedReview.curated_dataset_id.in_(dataset_ids))
        .order_by(CuratedReview.created_at.desc())
        .all()
        if dataset_ids
        else []
    )
    reviews_by_dataset: dict[str, list] = {}
    for review in all_reviews:
        reviews_by_dataset.setdefault(
            review.curated_dataset_id,
            [],
        ).append(review)

    # 每个数据集的最新版本一次批量取回（按 dataset_id 分组 max(version_no)
    # 再 join 取行），替代逐数据集一次查询的 N+1；唯一约束保证无并列，
    # 与 order_by(version_no.desc()).first() 语义一致。
    latest_version_by_dataset: dict[str, DatasetVersion] = {}
    if dataset_ids:
        latest_version_sub = (
            db.query(
                DatasetVersion.dataset_id,
                func.max(DatasetVersion.version_no).label("mx"),
            )
            .filter(DatasetVersion.dataset_id.in_(dataset_ids))
            .group_by(DatasetVersion.dataset_id)
            .subquery()
        )
        latest_version_by_dataset = {
            version.dataset_id: version
            for version in db.query(DatasetVersion)
            .join(
                latest_version_sub,
                (DatasetVersion.dataset_id == latest_version_sub.c.dataset_id)
                & (DatasetVersion.version_no == latest_version_sub.c.mx),
            )
            .all()
        }

    result = []
    for dataset in rows:
        version = latest_version_by_dataset.get(dataset.id)
        schema = (
            dataset.schema_json
            if isinstance(dataset.schema_json, dict)
            else {}
        )
        review = next(
            (
                candidate
                for candidate in reviews_by_dataset.get(dataset.id, [])
                if review_matches_version(candidate, version)
            ),
            None,
        )
        result.append(
            CuratedDatasetResponse(
                id=dataset.id,
                name=dataset.name,
                status=review.status if review else "pending_review",
                primary_key=",".join(
                    split_pk(schema.get("primary_key"))
                ),
                row_count=version.rowcount if version else None,
                quality_score=schema.get("quality_score"),
                producer_pipeline_id=dataset.producer_pipeline_id,
                output_key=dataset.output_key,
                has_review_evidence=bool(
                    reviews_by_dataset.get(dataset.id)
                ),
                created_at=(
                    dataset.created_at.isoformat()
                    if dataset.created_at
                    else None
                ),
                updated_at=(
                    dataset.updated_at.isoformat()
                    if dataset.updated_at
                    else None
                ),
            )
        )

    if status:
        if status == "pending_review":
            result = [
                item
                for item in result
                if item.status in {"pending_review", "pending", "in_review"}
            ]
        elif status == "reviewed":
            # Both decisions mean that the current version no longer needs
            # manual processing; the decision remains in immutable evidence.
            result = [
                item
                for item in result
                if item.status in {"approved", "rejected"}
            ]
        else:
            result = [item for item in result if item.status == status]
        total = len(result)
        if paginated:
            start = (page - 1) * page_size
            result = result[start : start + page_size]

    if paginated:
        return {
            "items": result,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    return result


def get_curated(db: Session, dataset_id: str) -> CuratedDatasetResponse:
    """Return one canonical curated dataset, with legacy read compatibility."""
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.kind == "curated")
        .first()
    )
    if dataset:
        review = current_version_review(db, dataset_id)
        version = (
            db.query(DatasetVersion)
            .filter(DatasetVersion.dataset_id == dataset_id)
            .order_by(DatasetVersion.version_no.desc())
            .first()
        )
        schema = (
            dataset.schema_json
            if isinstance(dataset.schema_json, dict)
            else {}
        )
        return CuratedDatasetResponse(
            id=dataset.id,
            name=dataset.name,
            status=review.status if review else "pending_review",
            primary_key=",".join(split_pk(schema.get("primary_key"))),
            row_count=version.rowcount if version else 0,
            quality_score=schema.get("quality_score"),
            producer_pipeline_id=dataset.producer_pipeline_id,
            output_key=dataset.output_key,
            has_review_evidence=(
                db.query(CuratedReview)
                .filter(CuratedReview.curated_dataset_id == dataset.id)
                .first()
                is not None
            ),
        )

    legacy = (
        db.query(CuratedDataset)
        .filter(CuratedDataset.id == dataset_id)
        .first()
    )
    if legacy:
        legacy_schema = (
            legacy.schema_json
            if isinstance(legacy.schema_json, dict)
            else {}
        )
        return CuratedDatasetResponse(
            id=legacy.id,
            name=legacy.name,
            status=legacy.status,
            primary_key=",".join(
                split_pk(legacy_schema.get("primary_key"))
            ),
            row_count=legacy_schema.get("row_count"),
            quality_score=legacy.quality_score,
        )
    raise HTTPException(404, "Curated dataset not found")
