"""ReviewService 单元测试"""
from unittest.mock import MagicMock
from app.services.v2.curated.review_service import ReviewService
from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit
from app.models.v2.dataset import Dataset, DatasetVersion


def make_db_with_dataset():
    db = MagicMock()
    ds = Dataset(
        id="ds-1", name="test_dataset", kind="curated", schema_json={})
    version = DatasetVersion(
        id="ver-1", dataset_id="ds-1", version_no=1, rowcount=1)
    review = CuratedReview(
        id="rev-1", curated_dataset_id="ds-1",
        dataset_version_id=version.id, status="pending"
    )

    def query_side_effect(model):
        mock_q = MagicMock()
        if model == Dataset:
            mock_q.filter.return_value.first.return_value = ds
        elif model == CuratedDataset:
            mock_q.filter.return_value.first.return_value = None
        elif model == CuratedReview:
            mock_q.filter.return_value.first.return_value = review
        elif model == CuratedRowEdit:
            mock_q.filter.return_value.all.return_value = []
        elif model == DatasetVersion:
            mock_q.filter.return_value.order_by.return_value.first.return_value = version
        return mock_q

    db.query.side_effect = query_side_effect
    db.add = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()
    return db, ds, review


def test_start_review_creates_record():
    db, ds, _ = make_db_with_dataset()
    svc = ReviewService(db)

    # refresh 时设置 id
    def refresh_side_effect(obj):
        if isinstance(obj, CuratedReview) and not obj.id:
            obj.id = "new-rev-1"
    db.refresh.side_effect = refresh_side_effect

    review = svc.start_review("ds-1")
    db.add.assert_called_once()
    db.commit.assert_called()
    assert review.status == "pending"
    assert review.dataset_version_id == "ver-1"


def test_approve_updates_status():
    db, _, review = make_db_with_dataset()
    svc = ReviewService(db)
    result = svc.approve("rev-1", notes="数据看起来不错")
    assert review.status == "approved"
    assert review.notes == "数据看起来不错"
    assert review.decided_at is not None


def test_reject_updates_status():
    db, _, review = make_db_with_dataset()
    svc = ReviewService(db)
    result = svc.reject("rev-1", notes="存在数据质量问题")
    assert review.status == "rejected"


def test_edit_row_creates_edit_record():
    db, _, review = make_db_with_dataset()
    svc = ReviewService(db)
    edit = svc.edit_row("rev-1", "row-1", "name", "旧名称", "新名称")
    db.add.assert_called()
    db.commit.assert_called()


def test_batch_edit_rows():
    db, _, review = make_db_with_dataset()
    svc = ReviewService(db)
    edits = [
        {"row_pk": "1", "field_name": "name", "old_value": "A", "new_value": "B"},
        {"row_pk": "2", "field_name": "age", "old_value": "25", "new_value": "26"},
    ]
    results = svc.batch_edit_rows("rev-1", edits)
    assert len(results) == 2
    assert db.add.call_count == 2


def test_apply_edits_to_snapshot():
    db = MagicMock()
    review = CuratedReview(id="rev-1", curated_dataset_id="ds-1", status="pending")

    edits = [
        CuratedRowEdit(review_id="rev-1", row_pk="1", field_name="name", old_value="Alice", new_value="Alice Smith"),
        CuratedRowEdit(review_id="rev-1", row_pk="2", field_name="age", old_value="25", new_value=None),
    ]
    db.query.return_value.filter.return_value.all.return_value = edits

    svc = ReviewService(db)
    original = [
        {"id": "1", "name": "Alice", "age": "30"},
        {"id": "2", "name": "Bob",   "age": "25"},
    ]
    result = svc.apply_edits_to_snapshot("rev-1", original)

    # row 1: name 修改
    assert result[0]["name"] == "Alice Smith"
    # row 2: age 被删除（new_value=None）
    assert "age" not in result[1]


def test_get_edits_empty():
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []
    svc = ReviewService(db)
    result = svc.get_edits("rev-1")
    assert result == []


def test_apply_edits_no_edits_returns_original():
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []
    svc = ReviewService(db)
    original = [{"id": "1", "name": "Alice"}]
    result = svc.apply_edits_to_snapshot("rev-1", original)
    assert result == original
