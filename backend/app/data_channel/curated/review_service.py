"""人工审核服务 — 行级编辑、版本合并、审核状态管理"""
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit


def apply_all_row_edits(db: Session, dataset_id: str, rows: list[dict]) -> list[dict]:
    """把该数据集全部审核的行级编辑按时间序叠加到行数据上（后写覆盖前写）。

    行匹配键与录入时一致：str(row['id'] or row['__pk__'])。
    不修改底层存储——编辑是审核层的"修正事实"，出口时叠加。
    """
    if not rows:
        return rows
    reviews = (db.query(CuratedReview)
               .filter(CuratedReview.curated_dataset_id == dataset_id)
               .order_by(CuratedReview.created_at.asc())
               .all())
    if not reviews:
        return rows
    review_ids = [r.id for r in reviews]
    edits = (db.query(CuratedRowEdit)
             .filter(CuratedRowEdit.review_id.in_(review_ids))
             .order_by(CuratedRowEdit.edited_at.asc())
             .all())
    if not edits:
        return rows
    # row_pk → {field: new_value}；时间序遍历天然后写覆盖前写
    edit_map: dict[str, dict[str, str | None]] = {}
    for e in edits:
        edit_map.setdefault(e.row_pk, {})[e.field_name] = e.new_value
    out: list[dict] = []
    for row in rows:
        pk = str(row.get("id", row.get("__pk__", "")))
        patch = edit_map.get(pk)
        if patch:
            row = dict(row)
            for field, val in patch.items():
                if val is None:
                    row.pop(field, None)
                else:
                    row[field] = val
        out.append(row)
    return out


def load_rows_with_edits(db: Session, dataset_id: str, limit: int = 10000) -> list[dict]:
    """curated 数据的统一出口读数：最新版本行 + 行级审核编辑叠加。

    预览 / 质量报告 / 映射灌入本体 都应经过这里——否则人工修正的
    v2_curated_row_edits 永远不会体现在下游数据里（行级审核变成摆设）。
    """
    from app.services.v2.dataset_service import DatasetService
    rows = DatasetService(db).preview(dataset_id, None, limit=limit)
    return apply_all_row_edits(db, dataset_id, rows)


class ReviewService:

    def __init__(self, db: Session):
        self._db = db

    def start_review(self, curated_dataset_id: str, reviewer_id: str | None = None) -> CuratedReview:
        """新建审核记录，状态设为 pending"""
        ds = self._get_dataset_or_raise(curated_dataset_id)
        self._ensure_curated_dataset_record(curated_dataset_id)

        review = CuratedReview(
            curated_dataset_id=curated_dataset_id,
            reviewer_id=reviewer_id,
            status="pending",
        )
        self._db.add(review)
        if ds is not None:
            ds.status = "in_review"
        self._db.commit()
        self._db.refresh(review)
        return review

    def edit_row(
        self,
        review_id: str,
        row_pk: str,
        field_name: str,
        old_value: str | None,
        new_value: str | None,
    ) -> CuratedRowEdit:
        """记录单行单字段的修改"""
        review = self._get_review_or_raise(review_id)

        edit = CuratedRowEdit(
            review_id=review_id,
            row_pk=row_pk,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
        )
        self._db.add(edit)
        self._db.commit()
        self._db.refresh(edit)
        return edit

    def batch_edit_rows(self, review_id: str, edits: list[dict]) -> list[CuratedRowEdit]:
        """批量提交行编辑
        edits 格式：[{"row_pk": "...", "field_name": "...", "old_value": "...", "new_value": "..."}]
        """
        self._get_review_or_raise(review_id)
        results = []
        for e in edits:
            edit = CuratedRowEdit(
                review_id=review_id,
                row_pk=e["row_pk"],
                field_name=e["field_name"],
                old_value=e.get("old_value"),
                new_value=e.get("new_value"),
            )
            self._db.add(edit)
            results.append(edit)
        self._db.commit()
        return results

    def approve(self, review_id: str, notes: str = "") -> CuratedReview:
        """审核通过 — 将数据集状态改为 approved"""
        review = self._get_review_or_raise(review_id)
        review.status = "approved"
        review.notes = notes
        review.decided_at = datetime.now(timezone.utc)

        self._set_dataset_status(review.curated_dataset_id, "approved")

        self._db.commit()
        self._db.refresh(review)
        return review

    def reject(self, review_id: str, notes: str = "") -> CuratedReview:
        """审核拒绝"""
        review = self._get_review_or_raise(review_id)
        review.status = "rejected"
        review.notes = notes
        review.decided_at = datetime.now(timezone.utc)

        self._set_dataset_status(review.curated_dataset_id, "rejected")

        self._db.commit()
        self._db.refresh(review)
        return review

    def get_edits(self, review_id: str) -> list[CuratedRowEdit]:
        """获取审核下的所有行编辑记录"""
        return self._db.query(CuratedRowEdit).filter(
            CuratedRowEdit.review_id == review_id
        ).all()

    def apply_edits_to_snapshot(self, review_id: str, original_data: list[dict]) -> list[dict]:
        """将行编辑应用到数据快照，返回修改后的数据（不修改数据库原始存储）"""
        edits = self.get_edits(review_id)
        if not edits:
            return original_data

        # 按 row_pk 分组编辑
        edit_map: dict[str, dict[str, str | None]] = {}
        for edit in edits:
            if edit.row_pk not in edit_map:
                edit_map[edit.row_pk] = {}
            edit_map[edit.row_pk][edit.field_name] = edit.new_value

        result = []
        for row in original_data:
            row_pk = str(row.get("id", row.get("__pk__", "")))
            if row_pk in edit_map:
                row = dict(row)
                for field, new_val in edit_map[row_pk].items():
                    if new_val is None:
                        row.pop(field, None)  # 删除字段
                    else:
                        row[field] = new_val
            result.append(row)

        return result

    # ── 私有辅助 ──────────────────────────────────────────────────

    def _get_dataset_or_raise(self, dataset_id: str):
        """优先查 legacy CuratedDataset；找不到时回退到 pipeline 产出的 Dataset(kind='curated')。

        返回值可能为 None（当只存在 Dataset(kind='curated') 时），调用方需做 None 兼容。
        """
        ds = self._db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).first()
        if ds:
            return ds
        from app.models.v2.dataset import Dataset
        ds_v2 = self._db.query(Dataset).filter(
            Dataset.id == dataset_id, Dataset.kind == "curated"
        ).first()
        if ds_v2:
            return ds_v2
        from fastapi import HTTPException
        raise HTTPException(404, f"Curated dataset {dataset_id} not found")

    def _ensure_curated_dataset_record(self, dataset_id: str) -> None:
        """确保 v2_curated_datasets 表中存在对应记录，以便 CuratedReview 的 FK 能解析。"""
        from app.models.v2.dataset import Dataset
        existing = self._db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).first()
        if existing:
            return
        ds_v2 = self._db.query(Dataset).filter(
            Dataset.id == dataset_id, Dataset.kind == "curated"
        ).first()
        if not ds_v2:
            return  # 无法补，由调用方后续的 FK 错误自然报出
        rec = CuratedDataset(
            id=dataset_id,
            name=ds_v2.name,
            schema_json=ds_v2.schema_json,
            quality_score=ds_v2.schema_json.get("quality_score") if isinstance(ds_v2.schema_json, dict) else None,
            status="pending_review",
        )
        self._db.add(rec)
        self._db.flush()

    def _set_dataset_status(self, dataset_id: str, status: str) -> None:
        """同时兼容 legacy CuratedDataset 与 pipeline 产出的 Dataset(kind='curated')。"""
        ds = self._db.query(CuratedDataset).filter(
            CuratedDataset.id == dataset_id
        ).first()
        if ds:
            ds.status = status
            return
        from app.models.v2.dataset import Dataset
        ds_v2 = self._db.query(Dataset).filter(
            Dataset.id == dataset_id, Dataset.kind == "curated"
        ).first()
        if ds_v2 and hasattr(ds_v2, "status"):
            ds_v2.status = status

    def _get_review_or_raise(self, review_id: str) -> CuratedReview:
        review = self._db.query(CuratedReview).filter(CuratedReview.id == review_id).first()
        if not review:
            from fastapi import HTTPException
            raise HTTPException(404, f"Review {review_id} not found")
        return review
