"""人工审核服务 — 行级编辑、版本合并、审核状态管理。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.data_channel.datasets.lake_gate import split_pk
from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit


class ReviewApprovalError(ValueError):
    """当前数据版本尚未审批，或审批已经因新版本产生而过期。"""


def latest_dataset_version(db: Session, dataset_id: str):
    """返回统一资产表的最新不可变版本。"""
    from app.models.v2.dataset import DatasetVersion

    return (db.query(DatasetVersion)
            .filter(DatasetVersion.dataset_id == dataset_id)
            .order_by(DatasetVersion.version_no.desc()).first())


def _as_aware(value):
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def review_matches_version(review: CuratedReview, version) -> bool:
    """判断审核是否属于指定版本，并安全兼容迁移前的空版本记录。

    新审核始终通过 ``dataset_version_id`` 精确绑定。历史空版本审核仅能背书
    审核创建时已存在的版本；版本创建时间晚于审核时，旧审核自动失效。
    """
    if version is None:
        return review.dataset_version_id is None
    if review.dataset_version_id:
        return review.dataset_version_id == version.id
    review_at = _as_aware(review.created_at)
    version_at = _as_aware(version.created_at)
    return bool(review_at and version_at and review_at >= version_at)


def current_version_review(db: Session, dataset_id: str, *, status: str | None = None):
    """返回当前 DatasetVersion 的最新有效审核；没有则返回 ``None``。"""
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
    """要求当前数据版本已经审批通过，否则硬失败。"""
    version = latest_dataset_version(db, dataset_id)
    if version is None:
        raise ReviewApprovalError(f"数据集 {dataset_id} 尚无可审批的数据版本")
    return require_version_approved(db, dataset_id, version)


def require_version_approved(db: Session, dataset_id: str, version) -> CuratedReview:
    """要求指定不可变版本已审批，避免检查和读数之间切换版本。"""
    reviews = (db.query(CuratedReview)
               .filter(CuratedReview.curated_dataset_id == dataset_id)
               .order_by(CuratedReview.created_at.desc()).all())
    review = next((item for item in reviews
                   if item.status == "approved" and review_matches_version(item, version)), None)
    if review is None:
        raise ReviewApprovalError(
            f"数据集 {dataset_id} 当前版本 v{version.version_no} 尚未审批通过；"
            f"旧版本审批不会自动继承到新版本")
    return review


def _dataset_schema(db: Session, dataset_id: str) -> dict:
    """读取权威 v2 dataset schema；legacy 表仅作只读兼容回退。"""
    from app.models.v2.dataset import Dataset

    dataset = db.query(Dataset).filter(
        Dataset.id == dataset_id, Dataset.kind == "curated").first()
    if dataset:
        return dict(dataset.schema_json or {})
    legacy = db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).first()
    return dict(legacy.schema_json or {}) if legacy else {}


def _field_contract(schema: dict, field_name: str) -> dict:
    return next((
        item for item in (schema.get("contract_definitions") or [])
        if str(item.get("field_key") or "") == field_name
    ), {})


def _field_type(schema: dict, field_name: str) -> str:
    contract = _field_contract(schema, field_name)
    if contract.get("field_type"):
        return str(contract["field_type"]).lower()
    typed = next((
        item for item in (schema.get("columns_typed") or [])
        if str(item.get("name") or "") == field_name
    ), {})
    return str(typed.get("type") or "string").lower()


def _coerce_review_value(schema: dict, field_name: str, value):
    """把审核表中的文本值恢复为发布契约声明的运行时类型。"""
    if value is None:
        return None
    expected = _field_type(schema, field_name)
    if expected == "string":
        return str(value)
    if expected == "json":
        return value if isinstance(value, (dict, list)) else json.loads(str(value))
    if expected == "integer":
        return int(str(value).strip().replace(",", ""))
    if expected == "float":
        return float(str(value).strip().replace(",", ""))
    if expected == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "yes", "1"}
    # timestamp 仍以 ISO 文本进入 Parquet/投影，与流水线原始时间列保持一致。
    return value


def dataset_pk_columns(db: Session, dataset_id: str) -> list[str]:
    """审核行身份只认 schema 中固化的真实主键，支持复合主键。"""
    return split_pk(_dataset_schema(db, dataset_id).get("primary_key"))


def encode_row_pk(row: dict, pk_cols: list[str], *, dataset_name: str = "") -> str:
    """把真实主键编码成审核 ``row_pk``。

    单主键沿用纯字符串；复合主键使用紧凑 JSON 数组，避免业务值中的分隔符
    造成碰撞。
    """
    if not pk_cols:
        raise ValueError(
            f"数据集「{dataset_name}」未声明主键，无法安全定位审核编辑行。"
            f"请先在流水线数据契约中声明主键并重新入湖。")
    values: list[str] = []
    for column in pk_cols:
        value = row.get(column)
        if value is None or str(value).strip() == "":
            raise ValueError(
                f"数据集「{dataset_name}」的审核行缺少主键列「{column}」或值为空，"
                f"无法应用行级编辑。")
        values.append(str(value).strip())
    if len(values) == 1:
        return values[0]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def normalize_row_pk(value, pk_cols: list[str], *, dataset_name: str = "") -> str:
    """规整 API 提交的行键；复合键接受 JSON 数组或按列名给出的对象。"""
    if not pk_cols:
        raise HTTPException(409, detail={
            "code": "review_primary_key_required",
            "message": f"数据集「{dataset_name}」未声明主键，无法安全编辑具体行。"
                       f"请先在流水线数据契约中声明主键并重新入湖。",
        })
    parsed = value
    if isinstance(value, str) and len(pk_cols) > 1:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            raise HTTPException(400, detail={
                "code": "invalid_composite_row_pk",
                "message": f"复合主键 {pk_cols} 的 row_pk 必须是 JSON 数组或对象。",
            }) from None
    if isinstance(parsed, dict):
        row = {column: parsed.get(column) for column in pk_cols}
    elif isinstance(parsed, (list, tuple)):
        if len(parsed) != len(pk_cols):
            raise HTTPException(400, detail={
                "code": "invalid_composite_row_pk",
                "message": f"row_pk 提供了 {len(parsed)} 个值，但主键需要 "
                           f"{len(pk_cols)} 列 {pk_cols}。",
            })
        row = dict(zip(pk_cols, parsed))
    elif len(pk_cols) == 1:
        row = {pk_cols[0]: parsed}
    else:
        raise HTTPException(400, "复合主键 row_pk 格式非法")
    try:
        return encode_row_pk(row, pk_cols, dataset_name=dataset_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def _version_by_id(db: Session, dataset_id: str, version_id: str | None):
    if not version_id:
        return latest_dataset_version(db, dataset_id)
    from app.models.v2.dataset import DatasetVersion

    return db.query(DatasetVersion).filter(
        DatasetVersion.id == version_id,
        DatasetVersion.dataset_id == dataset_id,
    ).first()


def apply_all_row_edits(
    db: Session,
    dataset_id: str,
    rows: list[dict],
    *,
    dataset_version_id: str | None = None,
    include_review_id: str | None = None,
) -> list[dict]:
    """把指定版本的审核编辑叠加到数据行。

    正式读取默认只应用已批准审核，避免 pending/rejected 修改泄漏到本体映射；
    审核详情通过 ``include_review_id`` 额外预览当前 pending 审核。历史空版本
    审核仍按创建时间与版本匹配，不能永久污染后续版本。
    """
    if not rows:
        return rows
    version = _version_by_id(db, dataset_id, dataset_version_id)
    if dataset_version_id and version is None:
        raise ValueError(f"数据集 {dataset_id} 的版本 {dataset_version_id} 不存在")
    reviews = (db.query(CuratedReview)
               .filter(CuratedReview.curated_dataset_id == dataset_id)
               .order_by(CuratedReview.created_at.asc()).all())
    reviews = [review for review in reviews
               if review_matches_version(review, version)
               and (review.status == "approved" or review.id == include_review_id)]
    if not reviews:
        return rows

    review_ids = [review.id for review in reviews]
    edits = (db.query(CuratedRowEdit)
             .filter(CuratedRowEdit.review_id.in_(review_ids))
             .order_by(CuratedRowEdit.edited_at.asc()).all())
    if not edits:
        return rows
    pk_cols = dataset_pk_columns(db, dataset_id)
    if not pk_cols:
        raise ValueError(
            f"数据集 {dataset_id} 存在行级审核编辑，但 schema 未声明主键；"
            f"系统拒绝猜测 id 或第一列，以免把修改应用到错误行。")

    edit_map: dict[str, dict[str, str | None]] = {}
    for edit in edits:
        edit_map.setdefault(edit.row_pk, {})[edit.field_name] = edit.new_value

    schema = _dataset_schema(db, dataset_id)
    output: list[dict] = []
    matched_keys: set[str] = set()
    for source_row in rows:
        row_pk = encode_row_pk(source_row, pk_cols, dataset_name=dataset_id)
        patch = edit_map.get(row_pk)
        row = source_row
        if patch:
            matched_keys.add(row_pk)
            row = dict(source_row)
            for field, value in patch.items():
                # NULL 是单元格值，不是“从这一行删除 schema 字段”。
                row[field] = _coerce_review_value(schema, field, value)
        output.append(row)

    unmatched = sorted(set(edit_map) - matched_keys)
    if unmatched:
        raise ValueError(
            f"数据集 {dataset_id} 的审核编辑包含无法在绑定版本中定位的行主键 "
            f"{unmatched[:5]}。请刷新审核页面后按 schema 主键重新提交；"
            f"系统不会把未命中的编辑静默忽略。")
    return output


def load_rows_with_edits(db: Session, dataset_id: str, limit: int = 10000) -> list[dict]:
    """严格读取最新版本并叠加属于该版本的已批准编辑。"""
    from app.services.v2.dataset_service import DatasetService

    version = latest_dataset_version(db, dataset_id)
    if version is None:
        return []
    rows = DatasetService(db).load_all_rows(dataset_id, version.version_no)[:limit]
    return apply_all_row_edits(
        db, dataset_id, rows, dataset_version_id=version.id)


def load_all_rows_with_edits(
    db: Session,
    dataset_id: str,
    *,
    require_approved: bool = False,
    version=None,
) -> list[dict]:
    """严格全量读取指定版本并叠加该版本的已批准审核编辑。

    Mapping/投影等生产消费方必须使用此入口；存储读取、校验或解析失败均抛错。
    """
    from app.services.v2.dataset_service import DatasetService

    version = version or latest_dataset_version(db, dataset_id)
    if version is None:
        if require_approved:
            raise ReviewApprovalError(f"数据集 {dataset_id} 尚无可审批的数据版本")
        return []
    if require_approved:
        require_version_approved(db, dataset_id, version)
    rows = DatasetService(db).load_all_rows(dataset_id, version.version_no)
    return apply_all_row_edits(
        db, dataset_id, rows, dataset_version_id=version.id)


class ReviewService:

    def __init__(self, db: Session):
        self._db = db

    def start_review(
        self,
        curated_dataset_id: str,
        reviewer_id: str | None = None,
    ) -> CuratedReview:
        """为当前不可变 DatasetVersion 新建或复用 pending 审核。"""
        self._get_dataset_or_raise(curated_dataset_id)
        version = latest_dataset_version(self._db, curated_dataset_id)
        if version is None:
            raise HTTPException(409, detail={
                "code": "dataset_has_no_version",
                "message": "该成品数据集尚无可审核版本，请先成功执行流水线入湖。",
            })

        existing = (self._db.query(CuratedReview).filter(
            CuratedReview.curated_dataset_id == curated_dataset_id,
            CuratedReview.status == "pending",
            CuratedReview.dataset_version_id == version.id,
        ).order_by(CuratedReview.created_at.desc()).first())
        if existing:
            return existing

        review = CuratedReview(
            curated_dataset_id=curated_dataset_id,
            dataset_version_id=version.id,
            reviewer_id=reviewer_id,
            status="pending",
        )
        self._db.add(review)
        self._set_dataset_status(curated_dataset_id, "in_review")
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
        """记录单行单字段修改，并验证版本、主键及目标行。"""
        review = self._get_review_or_raise(review_id)
        self._ensure_pending(review)
        self._assert_review_version_current(review)
        pk_cols = dataset_pk_columns(self._db, review.curated_dataset_id)
        row_pk = normalize_row_pk(
            row_pk, pk_cols, dataset_name=self._dataset_name(review.curated_dataset_id))
        if field_name in pk_cols:
            raise HTTPException(409, detail={
                "code": "primary_key_edit_forbidden",
                "message": f"列「{field_name}」属于主键 {pk_cols}，审核编辑不能改变行身份。"
                           f"如需变更主键，请修改流水线源数据并重新入湖。",
            })
        rows_by_pk = self._assert_row_keys_exist(review, [row_pk], pk_cols)
        self._validate_edit_contract(
            review, field_name, new_value,
            rows_by_pk.get(row_pk) if rows_by_pk else None,
        )

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
        """原子保存一批行级编辑。"""
        review = self._get_review_or_raise(review_id)
        self._ensure_pending(review)
        self._assert_review_version_current(review)
        pk_cols = dataset_pk_columns(self._db, review.curated_dataset_id)
        dataset_name = self._dataset_name(review.curated_dataset_id)
        prepared: list[tuple[dict, str, str]] = []
        for source in edits:
            field_name = str(source.get("field_name") or "").strip()
            if not field_name:
                raise HTTPException(400, "field_name 不能为空")
            if field_name in pk_cols:
                raise HTTPException(409, detail={
                    "code": "primary_key_edit_forbidden",
                    "message": f"列「{field_name}」属于主键 {pk_cols}，审核编辑不能改变行身份。",
                })
            prepared.append((
                source,
                normalize_row_pk(source.get("row_pk"), pk_cols, dataset_name=dataset_name),
                field_name,
            ))
        rows_by_pk = self._assert_row_keys_exist(
            review, [item[1] for item in prepared], pk_cols)
        for source, row_pk, field_name in prepared:
            self._validate_edit_contract(
                review, field_name, source.get("new_value"),
                rows_by_pk.get(row_pk) if rows_by_pk else None,
            )

        results: list[CuratedRowEdit] = []
        for source, row_pk, field_name in prepared:
            edit = CuratedRowEdit(
                review_id=review_id,
                row_pk=row_pk,
                field_name=field_name,
                old_value=source.get("old_value"),
                new_value=source.get("new_value"),
            )
            self._db.add(edit)
            results.append(edit)
        self._db.commit()
        return results

    def approve(self, review_id: str, notes: str = "") -> CuratedReview:
        """批准当前版本审核。"""
        review = self._get_review_or_raise(review_id)
        self._ensure_pending(review)
        self._assert_review_version_current(review)
        review.status = "approved"
        review.notes = notes
        review.decided_at = datetime.now(timezone.utc)
        self._set_dataset_status(review.curated_dataset_id, "approved")
        self._db.commit()
        self._db.refresh(review)
        return review

    def reject(self, review_id: str, notes: str = "") -> CuratedReview:
        """拒绝当前版本审核。"""
        review = self._get_review_or_raise(review_id)
        self._ensure_pending(review)
        self._assert_review_version_current(review)
        review.status = "rejected"
        review.notes = notes
        review.decided_at = datetime.now(timezone.utc)
        self._set_dataset_status(review.curated_dataset_id, "rejected")
        self._db.commit()
        self._db.refresh(review)
        return review

    def get_edits(self, review_id: str) -> list[CuratedRowEdit]:
        return self._db.query(CuratedRowEdit).filter(
            CuratedRowEdit.review_id == review_id).all()

    def apply_edits_to_snapshot(
        self,
        review_id: str,
        original_data: list[dict],
    ) -> list[dict]:
        """将审核编辑应用到内存快照，不修改底层版本文件。"""
        review = self._get_review_or_raise(review_id)
        edits = self.get_edits(review_id)
        if not edits:
            return original_data
        pk_cols = dataset_pk_columns(self._db, review.curated_dataset_id)
        if not pk_cols:
            raise HTTPException(409, detail={
                "code": "review_primary_key_required",
                "message": "数据集未声明主键，无法安全应用行级编辑。",
            })

        edit_map: dict[str, dict[str, str | None]] = {}
        for edit in edits:
            edit_map.setdefault(edit.row_pk, {})[edit.field_name] = edit.new_value

        schema = _dataset_schema(self._db, review.curated_dataset_id)
        result: list[dict] = []
        matched_keys: set[str] = set()
        for source_row in original_data:
            try:
                row_pk = encode_row_pk(
                    source_row, pk_cols,
                    dataset_name=self._dataset_name(review.curated_dataset_id))
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            row = source_row
            if row_pk in edit_map:
                matched_keys.add(row_pk)
                row = dict(source_row)
                for field, value in edit_map[row_pk].items():
                    row[field] = _coerce_review_value(schema, field, value)
            result.append(row)

        unmatched = sorted(set(edit_map) - matched_keys)
        if unmatched:
            raise HTTPException(409, detail={
                "code": "review_row_not_found",
                "message": f"审核编辑中的行主键 {unmatched[:5]} 不存在于绑定版本。"
                           f"请刷新审核页面后重新选择行。",
            })
        return result

    def _dataset_name(self, dataset_id: str) -> str:
        dataset = self._get_dataset_or_raise(dataset_id)
        return str(getattr(dataset, "name", dataset_id) or dataset_id)

    @staticmethod
    def _ensure_pending(review: CuratedReview) -> None:
        if review.status != "pending":
            raise HTTPException(409, detail={
                "code": "review_already_decided",
                "message": f"审核已处于 {review.status} 状态，不能继续编辑或重复决定。",
            })

    def _assert_review_version_current(self, review: CuratedReview) -> None:
        """阻止用旧版本审核结果背书或修改当前资产。"""
        latest = latest_dataset_version(self._db, review.curated_dataset_id)
        if latest is not None and review_matches_version(review, latest):
            return
        raise HTTPException(409, detail={
            "code": "review_version_stale",
            "message": "审核发起后数据集已产生新版本，本次审核仅对应旧版本，"
                       "不能继续保存、批准或拒绝。请为最新版本重新发起审核。",
            "review_dataset_version_id": review.dataset_version_id,
            "latest_dataset_version_id": latest.id if latest else None,
            "latest_version_no": latest.version_no if latest else None,
        })

    def _assert_row_keys_exist(
        self,
        review: CuratedReview,
        row_pks: list[str],
        pk_cols: list[str],
    ) -> dict[str, dict]:
        """验证编辑目标行真实存在于审核绑定版本。"""
        if not row_pks or not review.dataset_version_id:
            return {}
        from app.models.v2.dataset import DatasetVersion
        from app.services.v2.dataset_service import DatasetReadError, DatasetService

        version = self._db.query(DatasetVersion).filter(
            DatasetVersion.id == review.dataset_version_id,
            DatasetVersion.dataset_id == review.curated_dataset_id,
        ).first()
        if not version:
            raise HTTPException(409, detail={
                "code": "review_version_unavailable",
                "message": "审核绑定的数据版本已不存在，不能继续提交编辑。",
            })
        try:
            rows = DatasetService(self._db).load_all_rows(
                review.curated_dataset_id, version.version_no)
        except DatasetReadError as exc:
            raise HTTPException(422, f"审核版本数据读取失败：{exc}") from None
        existing = {
            encode_row_pk(row, pk_cols, dataset_name=review.curated_dataset_id): row
            for row in rows
        }
        missing = sorted(set(row_pks) - set(existing))
        if missing:
            raise HTTPException(409, detail={
                "code": "review_row_not_found",
                "message": f"行主键 {missing[:5]} 不存在于审核绑定的数据版本。"
                           f"请刷新审核页面后重新选择行。",
            })
        return existing

    def _validate_edit_contract(
        self,
        review: CuratedReview,
        field_name: str,
        new_value,
        source_row: dict | None,
    ) -> None:
        """审核修改只能改真实列，且新值服从资产湖固化的逻辑类型。"""
        schema = _dataset_schema(self._db, review.curated_dataset_id)
        known = {str(c) for c in (schema.get("columns") or [])}
        if source_row:
            known.update(str(c) for c in source_row.keys())
        if known and field_name not in known:
            raise HTTPException(400, detail={
                "code": "review_unknown_field",
                "message": f"字段「{field_name}」不在审核绑定的数据版本中，不能通过 API 新增任意列。",
            })
        contract = _field_contract(schema, field_name)
        if contract and contract.get("nullable") is False and (
            new_value is None
            or (isinstance(new_value, str) and not new_value.strip())
        ):
            raise HTTPException(400, detail={
                "code": "review_null_forbidden",
                "message": f"字段「{field_name}」的发布契约不允许为空，审核修改不能清空该值。",
            })
        from app.data_channel.datasets.lake_gate import (
            LakeGateError, validate_declared_types)
        try:
            validate_declared_types(
                [{field_name: new_value}], schema.get("columns_typed"),
                dataset_name=self._dataset_name(review.curated_dataset_id),
            )
        except LakeGateError as exc:
            raise HTTPException(400, detail={
                "code": "review_type_mismatch",
                "message": str(exc),
            }) from None

    def _get_dataset_or_raise(self, dataset_id: str):
        """统一资产表是审核唯一权威源；legacy 表仅只读兼容。"""
        from app.models.v2.dataset import Dataset

        dataset = self._db.query(Dataset).filter(
            Dataset.id == dataset_id, Dataset.kind == "curated").first()
        if dataset:
            return dataset
        legacy = self._db.query(CuratedDataset).filter(
            CuratedDataset.id == dataset_id).first()
        if legacy:
            raise HTTPException(409, detail={
                "code": "legacy_curated_not_migrated",
                "message": "该成品数据集仍属于旧资产表，尚未迁移到统一数据资产湖，"
                           "不能发起新审核。",
            })
        raise HTTPException(404, f"Curated dataset {dataset_id} not found")

    def _set_dataset_status(self, dataset_id: str, status: str) -> None:
        """审核状态写入统一资产元数据；legacy 表只做兼容同步。"""
        from app.models.v2.dataset import Dataset

        dataset = self._db.query(Dataset).filter(
            Dataset.id == dataset_id, Dataset.kind == "curated").first()
        if dataset:
            schema = dict(dataset.schema_json or {})
            schema["review_status"] = status
            dataset.schema_json = schema
        legacy = self._db.query(CuratedDataset).filter(
            CuratedDataset.id == dataset_id).first()
        if legacy:
            legacy.status = status

    def _get_review_or_raise(self, review_id: str) -> CuratedReview:
        review = self._db.query(CuratedReview).filter(
            CuratedReview.id == review_id).first()
        if not review:
            raise HTTPException(404, f"Review {review_id} not found")
        return review
