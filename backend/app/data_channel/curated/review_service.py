"""人工审核服务 — 行级编辑、版本合并、审核状态管理"""
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit


class ReviewApprovalError(ValueError):
    """当前数据版本尚未审批，或审批已经因新版本产生而过期。"""


def latest_dataset_version(db: Session, dataset_id: str):
    from app.models.v2.dataset import DatasetVersion
    return (db.query(DatasetVersion)
            .filter(DatasetVersion.dataset_id == dataset_id)
            .order_by(DatasetVersion.version_no.desc()).first())


def _as_aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def review_matches_version(review: CuratedReview, version) -> bool:
    """审批是否属于指定版本；兼容迁移前 dataset_version_id=NULL 的记录。

    历史空版本审批仅能背书“审批发生时已经存在的当前版本”。一旦产生时间更晚
    的新版本，该审批自动失效，避免 v1 的批准状态泄漏给 v2/v3。
    """
    if version is None:
        return review.dataset_version_id is None
    if review.dataset_version_id:
        return review.dataset_version_id == version.id
    review_at = _as_aware(review.created_at)
    version_at = _as_aware(version.created_at)
    return bool(review_at and version_at and review_at >= version_at)


def current_version_review(db: Session, dataset_id: str, *, status: str | None = None):
    """返回当前 DatasetVersion 的最新审批；没有则 None。"""
    version = latest_dataset_version(db, dataset_id)
    reviews = (db.query(CuratedReview)
               .filter(CuratedReview.curated_dataset_id == dataset_id)
               .order_by(CuratedReview.created_at.desc()).all())
    for review in reviews:
        if status is not None and review.status != status:
            continue
        if review_matches_version(review, version):
            return review
    return None


def require_current_version_approved(db: Session, dataset_id: str) -> CuratedReview:
    """要求当前数据版本已有 approved 审批，否则硬失败。"""
    version = latest_dataset_version(db, dataset_id)
    if version is None:
        raise ReviewApprovalError(f"数据集 {dataset_id} 尚无可审批的数据版本")
    return require_version_approved(db, dataset_id, version)


def require_version_approved(db: Session, dataset_id: str, version) -> CuratedReview:
    """要求指定的不可变版本已审批，避免审批检查与读数之间切换到新版本。"""
    reviews = (db.query(CuratedReview)
               .filter(CuratedReview.curated_dataset_id == dataset_id)
               .order_by(CuratedReview.created_at.desc()).all())
    review = next((r for r in reviews
                   if r.status == "approved" and review_matches_version(r, version)), None)
    if review is None:
        raise ReviewApprovalError(
            f"数据集 {dataset_id} 当前版本 v{version.version_no} 尚未审批通过；"
            f"旧版本审批不会自动继承到新版本")
    return review


def apply_all_row_edits(db: Session, dataset_id: str, rows: list[dict], *,
                        version=None, statuses: set[str] | None = None) -> list[dict]:
    """把该数据集全部审核的行级编辑按时间序叠加到行数据上（后写覆盖前写）。

    行匹配键与录入时一致：str(row['id'] or row['__pk__'])。
    不修改底层存储——编辑是审核层的"修正事实"，出口时叠加。
    """
    if not rows:
        return rows
    version = version or latest_dataset_version(db, dataset_id)
    reviews = (db.query(CuratedReview)
               .filter(CuratedReview.curated_dataset_id == dataset_id)
               .order_by(CuratedReview.created_at.asc())
               .all())
    reviews = [r for r in reviews
               if review_matches_version(r, version)
               and (statuses is None or r.status in statuses)]
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
    version = latest_dataset_version(db, dataset_id)
    rows = DatasetService(db).preview(dataset_id, None, limit=limit)
    return apply_all_row_edits(db, dataset_id, rows, version=version)


def load_all_rows_with_edits(db: Session, dataset_id: str, *,
                             require_approved: bool = False,
                             version=None) -> list[dict]:
    """严格全量读取当前版本并叠加属于该版本的审核编辑。

    Mapping/投影等生产消费方必须使用此入口：无行数上限，存储读取、解析或
    checksum 失败均抛错。要求审批时只叠加 approved 审批中的编辑。
    """
    from app.services.v2.dataset_service import DatasetService

    version = version or latest_dataset_version(db, dataset_id)
    if require_approved:
        if version is None:
            raise ReviewApprovalError(f"数据集 {dataset_id} 尚无可审批的数据版本")
        require_version_approved(db, dataset_id, version)
    rows = DatasetService(db).load_all_rows(
        dataset_id, version.version_no if version is not None else None)
    statuses = {"approved"} if require_approved else None
    return apply_all_row_edits(
        db, dataset_id, rows, version=version, statuses=statuses)


class ReviewService:

    def __init__(self, db: Session):
        self._db = db

    def start_review(self, curated_dataset_id: str, reviewer_id: str | None = None) -> CuratedReview:
        """新建审核记录，状态设为 pending"""
        self._get_dataset_or_raise(curated_dataset_id)
        version = latest_dataset_version(self._db, curated_dataset_id)
        if version is None:
            from fastapi import HTTPException
            raise HTTPException(409, "数据集尚无不可变版本，无法发起审批")

        review = CuratedReview(
            curated_dataset_id=curated_dataset_id,
            dataset_version_id=version.id if version else None,
            reviewer_id=reviewer_id,
            status="pending",
        )
        self._db.add(review)
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
        self._ensure_review_is_current(review)
        review.status = "approved"
        review.notes = notes
        review.decided_at = datetime.now(timezone.utc)

        self._db.commit()
        self._db.refresh(review)
        return review

    def reject(self, review_id: str, notes: str = "") -> CuratedReview:
        """审核拒绝"""
        review = self._get_review_or_raise(review_id)
        self._ensure_review_is_current(review)
        review.status = "rejected"
        review.notes = notes
        review.decided_at = datetime.now(timezone.utc)

        self._db.commit()
        self._db.refresh(review)
        return review

    def _ensure_review_is_current(self, review: CuratedReview) -> None:
        """禁止对旧版本审批记录作出会影响当前资产状态的决定。"""
        version = latest_dataset_version(self._db, review.curated_dataset_id)
        if version is not None and not review_matches_version(review, version):
            from fastapi import HTTPException
            raise HTTPException(
                409,
                f"该审核属于旧数据版本，当前已更新到 v{version.version_no}。"
                f"请为最新版本重新发起审核")

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
        """Resolve the canonical v2 Dataset; legacy table is read-only fallback."""
        from app.models.v2.dataset import Dataset
        ds_v2 = self._db.query(Dataset).filter(
            Dataset.id == dataset_id, Dataset.kind == "curated"
        ).first()
        if ds_v2:
            return ds_v2
        ds = self._db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).first()
        if ds:
            return ds
        from fastapi import HTTPException
        raise HTTPException(404, f"Curated dataset {dataset_id} not found")

    def _get_review_or_raise(self, review_id: str) -> CuratedReview:
        review = self._db.query(CuratedReview).filter(CuratedReview.id == review_id).first()
        if not review:
            from fastapi import HTTPException
            raise HTTPException(404, f"Review {review_id} not found")
        return review
