"""人工数据集功能包：主键契约 / 在线行编辑 / 上传契约校验 / 映射准入。

对应产品定位：人工数据集是资产湖一等资产——上传后可声明主键契约、
在线维护（改单元格/增删行=新版本）、并被本体映射直接灌入。
"""
from __future__ import annotations

import io

import pytest

from app.main import app
from app.routers.v2 import datasets as datasets_module
from app.routers.v2 import mappings as mappings_module

CSV = "编号,名称,数量\nA1,苹果,10\nA2,香蕉,5\n"
CSV_DUP_PK = "编号,名称,数量\nA1,苹果,10\nA1,香蕉,5\n"


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
    """datasets / mappings 路由都用模块内 get_db，需覆盖到测试库"""
    def _override():
        yield db

    app.dependency_overrides[datasets_module.get_db] = _override
    app.dependency_overrides[mappings_module.get_db] = _override
    yield client
    app.dependency_overrides.pop(datasets_module.get_db, None)
    app.dependency_overrides.pop(mappings_module.get_db, None)


def _upload(api, headers, name="库存表.csv", content=CSV) -> str:
    r = api.post("/api/v2/datasets/upload",
                 files={"file": (name, io.BytesIO(content.encode("utf-8")), "text/csv")},
                 headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _declare(api, headers, ds_id: str, pk: str):
    return api.put(f"/api/v2/datasets/{ds_id}/contract",
                   json={"primary_key": pk}, headers=headers)


# ── 主键契约 ─────────────────────────────────────────────────
def test_declare_contract_validates_and_persists(api, auth_headers):
    ds_id = _upload(api, auth_headers)
    r = _declare(api, auth_headers, ds_id, "编号")
    assert r.status_code == 200, r.text
    assert r.json()["rows_validated"] == 2

    ov = api.get("/api/v2/datasets/overview", headers=auth_headers).json()
    item = next(i for i in ov["items"] if i["id"] == ds_id)
    assert item["primary_key"] == "编号"


def test_declare_contract_rejects_duplicate_values(api, auth_headers):
    ds_id = _upload(api, auth_headers, content=CSV_DUP_PK)
    r = _declare(api, auth_headers, ds_id, "编号")
    assert r.status_code == 400
    assert "重复" in str(r.json()["detail"])


def test_declare_contract_rejects_curated(api, auth_headers, db):
    from app.models.v2.dataset import Dataset
    ds = Dataset(name="流水线产物 curated", kind="curated")
    db.add(ds)
    db.commit()
    r = _declare(api, auth_headers, ds.id, "编号")
    assert r.status_code == 400
    assert "入湖闸门" in str(r.json()["detail"])


def test_contract_locked_after_mapping_bound(api, auth_headers, db):
    ds_id = _upload(api, auth_headers)
    assert _declare(api, auth_headers, ds_id, "编号").status_code == 200

    from app.models.v2.mapping import OntologyMapping
    db.add(OntologyMapping(ontology_id="ont-1", curated_dataset_id=ds_id,
                           entity_class="Item", field_mapping={}))
    db.commit()

    # 改主键 → 锁定拒绝；同值重declare → 幂等允许
    assert _declare(api, auth_headers, ds_id, "名称").status_code == 400
    assert _declare(api, auth_headers, ds_id, "编号").status_code == 200


# ── 上传新版本的契约校验 ──────────────────────────────────────
def test_upload_version_enforces_contract(api, auth_headers):
    ds_id = _upload(api, auth_headers)
    assert _declare(api, auth_headers, ds_id, "编号").status_code == 200

    bad = api.post(f"/api/v2/datasets/{ds_id}/upload",
                   files={"file": ("v2.csv", io.BytesIO(CSV_DUP_PK.encode("utf-8")), "text/csv")},
                   headers=auth_headers)
    assert bad.status_code == 400
    versions = api.get(f"/api/v2/datasets/{ds_id}/versions", headers=auth_headers).json()
    assert len(versions) == 1  # 坏文件没有产生新版本

    ok = api.post(f"/api/v2/datasets/{ds_id}/upload",
                  files={"file": ("v2.csv", io.BytesIO(CSV.encode("utf-8")), "text/csv")},
                  headers=auth_headers)
    assert ok.status_code == 201
    assert ok.json()["version_no"] == 2


# ── 在线行编辑 ───────────────────────────────────────────────
def test_edit_rows_update_insert_delete(api, auth_headers):
    ds_id = _upload(api, auth_headers)
    assert _declare(api, auth_headers, ds_id, "编号").status_code == 200

    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 1,
        "updates": [{"key": {"编号": "A1"}, "values": {"数量": "99"}}],
        "inserts": [{"values": {"编号": "A3", "名称": "橙子", "数量": "7"}}],
        "deletes": [{"key": {"编号": "A2"}}],
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version_no"] == 2 and body["rowcount"] == 2

    pv = api.get(f"/api/v2/datasets/{ds_id}/preview", headers=auth_headers).json()
    rows = {row["编号"]: row for row in pv["rows"]}
    assert rows["A1"]["数量"] == "99"
    assert rows["A3"]["名称"] == "橙子"
    assert "A2" not in rows


def test_edit_rows_version_conflict_409(api, auth_headers):
    ds_id = _upload(api, auth_headers)
    assert _declare(api, auth_headers, ds_id, "编号").status_code == 200
    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 0,
        "updates": [{"key": {"编号": "A1"}, "values": {"数量": "1"}}],
    }, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["current_version_no"] == 1


def test_edit_rows_requires_pk_for_update_but_allows_insert(api, auth_headers):
    ds_id = _upload(api, auth_headers)  # 未声明主键
    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 1,
        "updates": [{"key": {"编号": "A1"}, "values": {"数量": "1"}}],
    }, headers=auth_headers)
    assert r.status_code == 400
    assert "主键契约" in str(r.json()["detail"])

    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 1,
        "inserts": [{"values": {"编号": "A3", "名称": "橙子", "数量": "7"}}],
    }, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["rowcount"] == 3


def test_edit_rows_rejects_pk_violation(api, auth_headers):
    ds_id = _upload(api, auth_headers)
    assert _declare(api, auth_headers, ds_id, "编号").status_code == 200
    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 1,
        "updates": [{"key": {"编号": "A1"}, "values": {"编号": "A2"}}],  # 改成与 A2 重复
    }, headers=auth_headers)
    assert r.status_code == 400
    assert "重复" in str(r.json()["detail"])
    versions = api.get(f"/api/v2/datasets/{ds_id}/versions", headers=auth_headers).json()
    assert len(versions) == 1  # 坏编辑没有落盘


def test_edit_rows_rejects_sync_dataset(api, auth_headers, db):
    from app.models.v2.dataset import Dataset
    ds = Dataset(name="SYNC::外部库存", kind="structured")
    db.add(ds)
    db.commit()
    r = api.post(f"/api/v2/datasets/{ds.id}/rows/edit", json={
        "base_version_no": 0,
        "inserts": [{"values": {"a": "1"}}],
    }, headers=auth_headers)
    assert r.status_code == 400
    assert "同步任务" in str(r.json()["detail"])


# ── 本体映射准入 ─────────────────────────────────────────────
def test_mapping_requires_declared_pk_for_manual_dataset(api, auth_headers, ontology):
    ds_id = _upload(api, auth_headers)
    oid = ontology["id"]

    r = api.post(f"/api/v2/ontologies/{oid}/mappings", json={
        "curated_dataset_id": ds_id,
        "entity_class": "Item",
        "field_mapping": {"编号": "code"},
    }, headers=auth_headers)
    assert r.status_code == 400
    assert "主键契约" in str(r.json()["detail"])

    assert _declare(api, auth_headers, ds_id, "编号").status_code == 200
    r = api.post(f"/api/v2/ontologies/{oid}/mappings", json={
        "curated_dataset_id": ds_id,
        "entity_class": "Item",
        "field_mapping": {"编号": "code"},
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["mapping_id"]
