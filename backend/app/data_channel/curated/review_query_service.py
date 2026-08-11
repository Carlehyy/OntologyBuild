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
from app.data_channel.datasets import lake_store
from app.data_channel.datasets.lake_gate import split_pk
from app.data_channel.datasets.models import DatasetChangeset, DatasetChangesetRow
from app.data_channel.pipeline_tasks.merge import _slim_row, compute_lake_impact
from app.models.v2.curated import CuratedReview
from app.models.v2.dataset import Dataset
from app.services.v2.dataset_service import DatasetReadError, DatasetService


def _delta_from_changeset(
    db: Session,
    version,
    primary_key_columns: list[str],
    *,
    sample_limit: int = 200,
) -> dict | None:
    """湖表版本的 delta 直接取其变更集（计数 + 逐行样本）。

    形状对齐 merge.compute_lake_impact；样本按 row_pk 排序（确定性），slim
    截断沿用 merge._slim_row。变更集缺失（异常状态）返回 None 由调用方回退
    内存计算。
    """
    changeset = db.query(DatasetChangeset).filter(
        DatasetChangeset.version_id == version.id).first()
    if changeset is None:
        return None

    def _sample(change_type: str) -> list:
        return (db.query(DatasetChangesetRow)
                .filter(DatasetChangesetRow.changeset_id == changeset.id,
                        DatasetChangesetRow.change_type == change_type)
                .order_by(DatasetChangesetRow.row_pk)
                .limit(sample_limit)
                .all())

    added = _sample("added")
    updated = _sample("updated")
    deleted = _sample("deleted")
    total_after = int(version.rowcount or 0)
    return {
        "keyed_by": list(primary_key_columns) if primary_key_columns else None,
        "total_before": total_after - changeset.added_count + changeset.deleted_count,
        "total_after": total_after,
        "added_count": changeset.added_count,
        "updated_count": changeset.updated_count,
        "deleted_count": changeset.deleted_count,
        "unchanged_count": max(
            0, total_after - changeset.added_count - changeset.updated_count),
        "added_sample": [_slim_row(r.new_row or {}) for r in added],
        "updated_sample": [{"before": _slim_row(r.old_row or {}),
                            "after": _slim_row(r.new_row or {})}
                           for r in updated],
        "deleted_sample": [_slim_row(r.old_row or {}) for r in deleted],
        "sample_truncated": (changeset.added_count > sample_limit
                             or changeset.updated_count > sample_limit
                             or changeset.deleted_count > sample_limit),
    }


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

    def _load_rows_for_version(version) -> list[dict]:
        """湖表版本经物理表 + 变更集回放；blob 版本走遗留解析（严格读）。"""
        if lake_store.version_uses_lake(dataset, version):
            return lake_store.rows_at_version(db, dataset, version.version_no)
        return dataset_service.load_all_rows(dataset_id, version.version_no)

    try:
        current_rows = apply_all_row_edits(
            db,
            dataset_id,
            _load_rows_for_version(current_version),
            dataset_version_id=current_version.id,
            include_review_id=selected_review.id if selected_review else None,
        )
        previous_rows = (
            apply_all_row_edits(
                db,
                dataset_id,
                _load_rows_for_version(previous_version),
                dataset_version_id=previous_version.id,
            )
            if previous_version
            else []
        )
    except DatasetReadError as exc:
        raise HTTPException(422, f"版本数据读取失败：{exc}") from exc
    except lake_store.LakeStoreError as exc:
        raise HTTPException(422, f"版本数据读取失败：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(
            409,
            detail={
                "code": "review_edit_identity_error",
                "message": str(exc),
            },
        ) from exc

    # 湖表版本的 delta 直接取该版本的变更集（版本间真实差异）；blob 版本保持
    # 内存计算（叠加编辑后的三视图 diff，与历史行为逐字段一致）
    delta = None
    if lake_store.version_uses_lake(dataset, current_version):
        delta = _delta_from_changeset(
            db, current_version, primary_key_columns)
    if delta is None:
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
