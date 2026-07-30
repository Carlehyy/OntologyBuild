"""Curated 审批权限：只有管理员可以决定审核。"""
import uuid

import pytest

from app.models.v2.curated import CuratedReview
from app.models.v2.dataset import Dataset, DatasetVersion


@pytest.fixture
def curated_client(client, db):
    """v2 curated 路由使用模块内 get_db，需单独覆盖到测试库。"""
    from app.main import app
    from app.routers.v2 import curated

    def _override():
        yield db

    app.dependency_overrides[curated.get_db] = _override
    yield client
    app.dependency_overrides.pop(curated.get_db, None)


def _make_curated(db) -> str:
    dataset = Dataset(
        name=f"authz-{uuid.uuid4().hex[:6]}",
        kind="curated",
        schema_json={
            "primary_key": "id",
            "review_status": "pending_review",
            "quality_score": 0.8,
        },
    )
    db.add(dataset)
    db.flush()
    db.add(DatasetVersion(
        dataset_id=dataset.id,
        version_no=1,
        rowcount=1,
        storage_uri=f"s3://test/{dataset.id}/v1.parquet",
    ))
    db.commit()
    db.refresh(dataset)
    return dataset.id


def _login(client, username, password):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_editor_cannot_approve_curated(curated_client, db, editor_user):
    dataset_id = _make_curated(db)
    headers = _login(curated_client, "editor", "editor123")

    response = curated_client.post(
        f"/api/v2/curated/{dataset_id}/review",
        params={"action": "approve"},
        headers=headers,
    )

    assert response.status_code == 403


def test_admin_can_approve_curated(curated_client, db, admin_user):
    dataset_id = _make_curated(db)
    headers = _login(curated_client, "admin", "admin123")

    response = curated_client.post(
        f"/api/v2/curated/{dataset_id}/review",
        params={"action": "approve"},
        headers=headers,
    )

    assert response.status_code == 200
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).one()
    review = db.query(CuratedReview).filter(
        CuratedReview.curated_dataset_id == dataset_id).one()
    db.refresh(dataset)
    assert review.status == "approved"
    assert dataset.schema_json["review_status"] == "approved"
