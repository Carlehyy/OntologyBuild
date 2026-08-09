"""Lifecycle operations for curated datasets."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.data_channel.datasets.consumers import dataset_consumers
from app.data_channel.datasets.service import (
    drain_storage_deletion_outbox,
    enqueue_dataset_storage_deletions,
)
from app.data_channel.file_assets.models import PipelineFileAsset
from app.models.v2.curated import CuratedReview, CuratedRowEdit
from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem
from app.ontologies.mappings.consumers import dataset_mapping_bindings


def delete_curated(
    db: Session,
    dataset_id: str,
    *,
    force: bool,
) -> None:
    """Delete one unreferenced curated dataset and all owned evidence/data."""
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.kind == "curated")
        .first()
    )
    if not dataset:
        raise HTTPException(404, "Curated dataset not found")

    if force:
        raise HTTPException(
            400,
            "force 强制删除已禁用；请先解除流水线和本体映射依赖",
        )

    pipelines = dataset_consumers(db, dataset_id)
    mappings = dataset_mapping_bindings(db, dataset_id)
    if pipelines or mappings:
        raise HTTPException(
            409,
            detail={
                "code": "in_use",
                "message": (
                    f"该数据集被 {len(pipelines)} 条流水线、"
                    f"{len(mappings)} 个本体映射引用，请先解除依赖后再删除。"
                ),
                "pipelines": pipelines,
                "mappings": mappings,
            },
        )

    # Storage deletion intents are committed in the same transaction as the
    # database deletion.  Physical deletion then drains the durable outbox.
    enqueue_dataset_storage_deletions(db, dataset_id)
    review_ids = [
        review.id
        for review in (
            db.query(CuratedReview.id)
            .filter(CuratedReview.curated_dataset_id == dataset_id)
            .all()
        )
    ]
    if review_ids:
        (
            db.query(CuratedRowEdit)
            .filter(CuratedRowEdit.review_id.in_(review_ids))
            .delete(synchronize_session=False)
        )
        (
            db.query(CuratedReview)
            .filter(CuratedReview.curated_dataset_id == dataset_id)
            .delete(synchronize_session=False)
        )

    version_ids = [
        version.id
        for version in (
            db.query(DatasetVersion)
            .filter(DatasetVersion.dataset_id == dataset_id)
            .all()
        )
    ]
    if version_ids:
        # 摘除运行记录对版本的血缘指针，否则版本删除撞 FK（NO ACTION）
        from app.data_channel.datasets.service import detach_run_version_lineage
        detach_run_version_lineage(db, version_ids)
        (
            db.query(MediaItem)
            .filter(MediaItem.dataset_version_id.in_(version_ids))
            .delete(synchronize_session=False)
        )
        (
            db.query(PipelineFileAsset)
            .filter(PipelineFileAsset.dataset_version_id.in_(version_ids))
            .delete(synchronize_session=False)
        )
    (
        db.query(DatasetVersion)
        .filter(DatasetVersion.dataset_id == dataset_id)
        .delete(synchronize_session=False)
    )
    db.delete(dataset)
    db.commit()
    drain_storage_deletion_outbox(db)
