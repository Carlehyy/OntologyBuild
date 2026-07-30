"""Read-model assembly for curated dataset review comparisons.

The HTTP router owns request validation and dependency injection.  This module
owns the version selection, row-edit overlay, delta calculation, and canonical
row-identity rules that form the review-diff business workflow.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.data_channel.curated.approved_version_reader import (
    apply_all_row_edits,
    encode_row_pk,
    review_matches_version,
)
from app.data_channel.datasets.lake_gate import split_pk
from app.data_channel.pipeline_tasks.merge import compute_lake_impact
from app.models.v2.curated import CuratedReview
from app.models.v2.dataset import Dataset
from app.services.v2.dataset_service import DatasetReadError, DatasetService


def build_review_diff(
    db: Session,
    dataset_id: str,
    *,
    limit: int,
    offset: int,
    review_id: str | None,
) -> dict:
    """Build the current/previous/delta review views for one curated dataset."""
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.kind == "curated")
        .first()
    )
    if not dataset:
        raise HTTPException(404, "Curated dataset not found")

    schema = dataset.schema_json if isinstance(dataset.schema_json, dict) else {}
    primary_key_columns = split_pk(schema.get("primary_key"))

    dataset_service = DatasetService(db)
    versions = dataset_service.list_versions(dataset_id)
    empty_page = {
        "version_no": None,
        "total": 0,
        "rows": [],
        "offset": offset,
        "limit": limit,
        "has_more": False,
    }
    if not versions:
        return {
            "pk": primary_key_columns,
            "row_pk_encoding": (
                "plain-string" if len(primary_key_columns) == 1 else "json-array"
            ),
            "current_row_pks": [],
            "current": empty_page,
            "previous": empty_page,
            "delta": None,
        }

    selected_review = None
    if review_id:
        selected_review = (
            db.query(CuratedReview)
            .filter(
                CuratedReview.id == review_id,
                CuratedReview.curated_dataset_id == dataset_id,
            )
            .first()
        )
        if not selected_review:
            raise HTTPException(404, "Review not found for this dataset")
    else:
        # Prefer the version bound to the pending review.  Silently switching to
        # a newer snapshot would make an approver review a different data set.
        selected_review = (
            db.query(CuratedReview)
            .filter(
                CuratedReview.curated_dataset_id == dataset_id,
                CuratedReview.status == "pending",
            )
            .order_by(CuratedReview.created_at.desc())
            .first()
        )

    latest_version = versions[-1]
    current_version = latest_version
    if selected_review and selected_review.dataset_version_id:
        current_version = next(
            (
                version
                for version in versions
                if version.id == selected_review.dataset_version_id
            ),
            None,
        )
        if current_version is None:
            raise HTTPException(
                409,
                detail={
                    "code": "review_version_unavailable",
                    "message": "该审核绑定的数据版本已不存在或不可用，不能继续审核。",
                    "dataset_version_id": selected_review.dataset_version_id,
                },
            )
    elif selected_review:
        # Reviews created before dataset_version_id existed are pinned by their
        # creation time rather than being rebound to today's latest version.
        historical_versions = [
            version
            for version in versions
            if review_matches_version(selected_review, version)
        ]
        if historical_versions:
            current_version = historical_versions[-1]

    current_index = versions.index(current_version)
    previous_version = versions[current_index - 1] if current_index > 0 else None

    try:
        current_rows = apply_all_row_edits(
            db,
            dataset_id,
            dataset_service.load_all_rows(dataset_id, current_version.version_no),
            dataset_version_id=current_version.id,
            include_review_id=selected_review.id if selected_review else None,
        )
        previous_rows = (
            apply_all_row_edits(
                db,
                dataset_id,
                dataset_service.load_all_rows(
                    dataset_id,
                    previous_version.version_no,
                ),
                dataset_version_id=previous_version.id,
            )
            if previous_version
            else []
        )
    except DatasetReadError as exc:
        raise HTTPException(422, f"版本数据读取失败：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(
            409,
            detail={
                "code": "review_edit_identity_error",
                "message": str(exc),
            },
        ) from exc

    delta = compute_lake_impact(
        previous_rows,
        current_rows,
        primary_key_columns,
        sample_limit=200,
    )
    current_page = current_rows[offset : offset + limit]
    current_row_primary_keys: list[str | None] = []
    if primary_key_columns:
        for row in current_page:
            try:
                # Return the same canonical encoding consumed by review writes;
                # browsers must not reimplement Python value stringification.
                current_row_primary_keys.append(
                    encode_row_pk(
                        row,
                        primary_key_columns,
                        dataset_name=dataset.name,
                    )
                )
            except ValueError:
                # Keep the version visible while disabling edits only for rows
                # whose identity is incomplete.
                current_row_primary_keys.append(None)

    return {
        "pk": primary_key_columns,
        "row_pk_encoding": (
            "plain-string" if len(primary_key_columns) == 1 else "json-array"
        ),
        "current_row_pks": current_row_primary_keys,
        "current": {
            "version_no": current_version.version_no,
            "dataset_version_id": current_version.id,
            "total": len(current_rows),
            "rows": current_page,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < len(current_rows),
        },
        "previous": {
            "version_no": (
                previous_version.version_no if previous_version else None
            ),
            "total": len(previous_rows),
            "rows": previous_rows[offset : offset + limit],
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < len(previous_rows),
        },
        "delta": delta,
        "review": (
            {
                "id": selected_review.id,
                "dataset_version_id": selected_review.dataset_version_id,
                "status": selected_review.status,
                "stale": not review_matches_version(
                    selected_review,
                    latest_version,
                ),
                "latest_dataset_version_id": latest_version.id,
                "latest_version_no": latest_version.version_no,
            }
            if selected_review
            else None
        ),
    }
