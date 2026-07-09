"""审批三视角 + 删除安全加固。

对应本轮需求：
1. 已审批通过的成品数据集不可删除（审批即背书）
2. 审批可看三视角：变化量 / 上一版全量 / 本次全量
3. 删除权限收敛：人工删除限管理员；成品/人工删除被本体映射引用时拦截
"""
from __future__ import annotations

import io
import uuid

import pytest

from app.main import app
from app.routers.v2 import curated as curated_module
from app.routers.v2 import datasets as datasets_module
from app.routers.v2 import mappings as mappings_module
from app.models.v2.dataset import Dataset
from app.data_channel.datasets.service import DatasetService, rows_to_parquet_bytes


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, bucket, key, data, content_type=""):
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data
        return uri

    def get_object(self, uri):
        if uri not in self.objects:
            raise FileNotFoundError(uri)
        return self.objects[uri]

    def delete_object(self, uri):
        self.objects.pop(uri, None)


@pytest.fixture
def fake_storage(monkeypatch):
    from app.data_channel.datasets import service as ds_service
    fs = FakeStorage()
    monkeypatch.setattr(ds_service, "get_storage_service", lambda: fs)
    return fs


@pytest.fixture
def api(client, db, fake_storage):
    def _override():
        yield db
    for mod in (datasets_module, mappings_module, curated_module):
        app.dependency_overrides[mod.get_db] = _override
    yield client
    for mod in (datasets_module, mappings_module, curated_module):
        app.dependency_overrides.pop(mod.get_db, None)


def _login(client, username, password):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def _make_curated_with_versions(db, versions: list[list[dict]], pk: str = "id") -> str:
    """建一个 v2 curated 数据集并按序写入多个全量版本，返回 dataset id。"""
    svc = DatasetService(db)
    ds = Dataset(name=f"diff-{uuid.uuid4().hex[:6]}", kind="curated",
                 schema_json={"primary_key": pk})
    db.add(ds)
    db.commit()
    db.refresh(ds)
    for rows in versions:
        svc.create_version(ds.id, rows_to_parquet_bytes(rows), rowcount=len(rows))
    return ds.id


# ── 三视角 ─────────────────────────────────────────────────────
def test_review_diff_three_views(api, auth_headers, db):
    ds_id = _make_curated_with_versions(db, [
        [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}],   # v1
        [{"id": "1", "name": "A"}, {"id": "3", "name": "c"}],   # v2: 1 改, 2 删, 3 增
    ])
    r = api.get(f"/api/v2/curated/{ds_id}/review-diff", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json().get("data", r.json())

    assert body["current"]["version_no"] == 2
    assert body["previous"]["version_no"] == 1
    assert len(body["current"]["rows"]) == 2
    assert len(body["previous"]["rows"]) == 2

    delta = body["delta"]
    assert delta["added_count"] == 1
    assert delta["updated_count"] == 1
    assert delta["deleted_count"] == 1
    # 变化量样本可追溯
    assert delta["added_sample"][0]["id"] == "3"
    assert delta["deleted_sample"][0]["id"] == "2"
    assert delta["updated_sample"][0]["before"]["name"] == "a"
    assert delta["updated_sample"][0]["after"]["name"] == "A"


def test_review_diff_first_version_all_added(api, auth_headers, db):
    ds_id = _make_curated_with_versions(db, [
        [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}],   # 仅 v1
    ])
    r = api.get(f"/api/v2/curated/{ds_id}/review-diff", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json().get("data", r.json())
    assert body["previous"]["version_no"] is None
    assert body["delta"]["added_count"] == 2
    assert body["delta"]["updated_count"] == 0
    assert body["delta"]["deleted_count"] == 0


# ── 禁删已审批 ─────────────────────────────────────────────────
def test_approved_curated_cannot_be_deleted(api, auth_headers, db, admin_user):
    ds_id = _make_curated_with_versions(db, [[{"id": "1", "name": "a"}]])
    # 审批通过
    r = api.post(f"/api/v2/curated/{ds_id}/review", params={"action": "approve"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    # 删除应被硬拦截
    r = api.delete(f"/api/v2/curated/{ds_id}", headers=auth_headers)
    assert r.status_code == 409, r.text
    assert "approved_locked" in r.text
    # force 也不能绕
    r = api.delete(f"/api/v2/curated/{ds_id}?force=true", headers=auth_headers)
    assert r.status_code == 409, r.text


def test_pending_curated_can_be_deleted(api, auth_headers, db):
    ds_id = _make_curated_with_versions(db, [[{"id": "1", "name": "a"}]])
    r = api.delete(f"/api/v2/curated/{ds_id}", headers=auth_headers)
    assert r.status_code == 204, r.text


# ── 依赖检查：被本体映射引用时拦截 ─────────────────────────────
def test_curated_delete_blocked_by_mapping(api, auth_headers, db):
    from app.ontologies.mappings.models import OntologyMapping
    ds_id = _make_curated_with_versions(db, [[{"id": "1", "name": "a"}]])
    db.add(OntologyMapping(ontology_id="ont-x", curated_dataset_id=ds_id,
                           entity_class="产品", field_mapping={}))
    db.commit()
    r = api.delete(f"/api/v2/curated/{ds_id}", headers=auth_headers)
    assert r.status_code == 409, r.text
    assert "in_use" in r.text
    # force 可强删
    r = api.delete(f"/api/v2/curated/{ds_id}?force=true", headers=auth_headers)
    assert r.status_code == 204, r.text


# ── 人工数据集删除：限管理员 ───────────────────────────────────
def _upload_manual(api, headers) -> str:
    csv = "id,name\n1,苹果\n2,香蕉\n"
    r = api.post("/api/v2/datasets/upload",
                 files={"file": ("库存.csv", io.BytesIO(csv.encode("utf-8")), "text/csv")},
                 headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def test_manual_delete_requires_admin(api, auth_headers, db, editor_user):
    ds_id = _upload_manual(api, auth_headers)  # 管理员上传
    editor_headers = _login(api, "editor", "editor123")
    r = api.delete(f"/api/v2/datasets/{ds_id}", headers=editor_headers)
    assert r.status_code == 403, r.text
    # 管理员可删
    r = api.delete(f"/api/v2/datasets/{ds_id}", headers=auth_headers)
    assert r.status_code == 200, r.text


def test_manual_delete_blocked_by_mapping(api, auth_headers, db):
    from app.ontologies.mappings.models import OntologyMapping
    ds_id = _upload_manual(api, auth_headers)
    db.add(OntologyMapping(ontology_id="ont-y", curated_dataset_id=ds_id,
                           entity_class="库存", field_mapping={}))
    db.commit()
    r = api.delete(f"/api/v2/datasets/{ds_id}", headers=auth_headers)
    assert r.status_code == 409, r.text
    assert "本体映射" in r.text
