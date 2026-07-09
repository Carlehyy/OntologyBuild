"""在线建表（人工数据集）：不上传文件，在线定义列/类型/主键后逐行维护。

产品定位：与上传创建的人工数据集完全同权——声明主键后可被本体映射灌入、
可作流水线数据源、可上传文件批量补数。区别仅在来源（source=manual）与
列类型语义（用户声明 types_source=declared，编辑时校验且不随数据重推断）。
"""
from __future__ import annotations

import io

import pytest

from app.main import app
from app.routers.v2 import datasets as datasets_module
from app.routers.v2 import mappings as mappings_module


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

    app.dependency_overrides[datasets_module.get_db] = _override
    app.dependency_overrides[mappings_module.get_db] = _override
    yield client
    app.dependency_overrides.pop(datasets_module.get_db, None)
    app.dependency_overrides.pop(mappings_module.get_db, None)


def _create_table(api, headers, name="设备台账", columns=None, primary_key="编号"):
    payload = {
        "name": name,
        "columns": columns if columns is not None else [
            {"name": "编号", "type": "string"},
            {"name": "名称", "type": "string"},
            {"name": "数量", "type": "integer"},
        ],
        "primary_key": primary_key,
    }
    return api.post("/api/v2/datasets/create-table", json=payload, headers=headers)


# ── 创建 ─────────────────────────────────────────────────────
def test_create_table_basic(api, auth_headers):
    r = _create_table(api, auth_headers)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["version_no"] == 1
    assert data["columns"] == ["编号", "名称", "数量"]
    assert data["primary_key"] == "编号"
    ds_id = data["id"]

    # 总览：来源=manual、主键已声明、v1 空表
    ov = api.get("/api/v2/datasets/overview", headers=auth_headers).json()
    item = next(i for i in ov["items"] if i["id"] == ds_id)
    assert item["source"] == "manual"
    assert item["primary_key"] == "编号"
    assert item["version_count"] == 1 and item["rowcount"] == 0

    # 预览：0 行但表头来自契约列（编辑器靠它渲染空表）
    pv = api.get(f"/api/v2/datasets/{ds_id}/preview", headers=auth_headers).json()
    assert pv["columns"] == ["编号", "名称", "数量"]
    assert pv["rows"] == [] and pv["total_rows"] == 0

    # schema：返回声明类型而非推断（空表推断只会得到空列表）
    sc = api.get(f"/api/v2/datasets/{ds_id}/schema", headers=auth_headers).json()
    assert {c["name"]: c["type"] for c in sc["columns"]} == {
        "编号": "string", "名称": "string", "数量": "integer"}


def test_create_table_validation(api, auth_headers):
    assert _create_table(api, auth_headers, name="  ").status_code == 400
    assert _create_table(api, auth_headers, columns=[]).status_code == 400
    assert _create_table(api, auth_headers, columns=[{"name": "  "}]).status_code == 400
    r = _create_table(api, auth_headers,
                      columns=[{"name": "编号"}, {"name": "编号"}])
    assert r.status_code == 400 and "重复" in str(r.json()["detail"])
    r = _create_table(api, auth_headers, primary_key="不存在的列")
    assert r.status_code == 400 and "不在列定义中" in str(r.json()["detail"])
    # 非法类型回落 string、空白列名被忽略
    r = _create_table(api, auth_headers, primary_key="",
                      columns=[{"name": "a", "type": "非法类型"}, {"name": " "}])
    assert r.status_code == 201
    sc = api.get(f"/api/v2/datasets/{r.json()['data']['id']}/schema",
                 headers=auth_headers).json()
    assert sc["columns"] == [{"name": "a", "type": "string", "sample_values": []}]


# ── 在线维护 ─────────────────────────────────────────────────
def test_online_table_insert_edit_flow(api, auth_headers):
    ds_id = _create_table(api, auth_headers).json()["data"]["id"]

    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 1,
        "inserts": [
            {"values": {"编号": "A1", "名称": "泵机", "数量": "10"}},
            {"values": {"编号": "A2", "名称": "阀门", "数量": "5"}},
        ],
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["version_no"] == 2 and r.json()["rowcount"] == 2

    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 2,
        "updates": [{"key": {"编号": "A1"}, "values": {"数量": "99"}}],
        "deletes": [{"key": {"编号": "A2"}}],
    }, headers=auth_headers)
    assert r.status_code == 200, r.text

    pv = api.get(f"/api/v2/datasets/{ds_id}/preview", headers=auth_headers).json()
    assert pv["total_rows"] == 1
    assert pv["rows"][0]["数量"] == "99"

    # 主键三校验照常生效：插入重复主键被拦
    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 3,
        "inserts": [{"values": {"编号": "A1", "名称": "重复", "数量": "1"}}],
    }, headers=auth_headers)
    assert r.status_code == 400 and "重复" in str(r.json()["detail"])


def test_online_table_rejects_type_violation(api, auth_headers):
    ds_id = _create_table(api, auth_headers).json()["data"]["id"]

    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 1,
        "inserts": [{"values": {"编号": "A1", "名称": "泵机", "数量": "十个"}}],
    }, headers=auth_headers)
    assert r.status_code == 400
    detail = str(r.json()["detail"])
    assert "数量" in detail and "integer" in detail
    versions = api.get(f"/api/v2/datasets/{ds_id}/versions", headers=auth_headers).json()
    assert len(versions) == 1  # 坏值没有落盘

    # "1"/"0" 在整数列合法（推断器会判 boolean，但整数录入必须放行）；空值放行
    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 1,
        "inserts": [{"values": {"编号": "A1", "名称": "", "数量": "1"}}],
    }, headers=auth_headers)
    assert r.status_code == 200, r.text


def test_online_table_declared_types_survive_edits(api, auth_headers):
    """声明类型是契约：录入整数样值后，float 列不得被重推断成 integer。"""
    r = _create_table(api, auth_headers, columns=[
        {"name": "编号", "type": "string"},
        {"name": "单价", "type": "float"},
    ])
    ds_id = r.json()["data"]["id"]

    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 1,
        "inserts": [{"values": {"编号": "A1", "单价": "5"}}],  # 看起来像 integer 的合法 float
    }, headers=auth_headers)
    assert r.status_code == 200, r.text

    sc = api.get(f"/api/v2/datasets/{ds_id}/schema", headers=auth_headers).json()
    types = {c["name"]: c["type"] for c in sc["columns"]}
    assert types == {"编号": "string", "单价": "float"}
    # 编号列声明 string：纯数字编号不得翻成 integer
    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 2,
        "inserts": [{"values": {"编号": "1001", "单价": "3.5"}}],
    }, headers=auth_headers)
    assert r.status_code == 200
    sc = api.get(f"/api/v2/datasets/{ds_id}/schema", headers=auth_headers).json()
    assert {c["name"]: c["type"] for c in sc["columns"]}["编号"] == "string"


def test_online_table_edit_rejects_unknown_column(api, auth_headers):
    ds_id = _create_table(api, auth_headers).json()["data"]["id"]
    r = api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 1,
        "inserts": [{"values": {"编号": "A1", "野列": "x"}}],
    }, headers=auth_headers)
    assert r.status_code == 400 and "不存在的列" in str(r.json()["detail"])


# ── 文件补数与列结构刷新 ──────────────────────────────────────
def test_upload_version_refreshes_declared_schema(api, auth_headers):
    ds_id = _create_table(api, auth_headers).json()["data"]["id"]

    csv = "编号,数量,备注\nB1,3,新列来自文件\n"
    r = api.post(f"/api/v2/datasets/{ds_id}/upload",
                 files={"file": ("补数.csv", io.BytesIO(csv.encode("utf-8")), "text/csv")},
                 headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["columns_added"] == ["备注"]
    assert body["columns_removed"] == ["名称"]

    # 列结构随新文件刷新；声明过的列保留声明类型，新列按数据推断
    sc = api.get(f"/api/v2/datasets/{ds_id}/schema", headers=auth_headers).json()
    types = {c["name"]: c["type"] for c in sc["columns"]}
    assert types["数量"] == "integer" and types["编号"] == "string"
    assert "备注" in types and "名称" not in types
    pv = api.get(f"/api/v2/datasets/{ds_id}/preview", headers=auth_headers).json()
    assert pv["columns"] == ["编号", "数量", "备注"]

    # 主键契约在上传路径照常拦截：重复主键的文件不落盘
    bad = "编号,数量,备注\nB1,1,x\nB1,2,y\n"
    r = api.post(f"/api/v2/datasets/{ds_id}/upload",
                 files={"file": ("坏.csv", io.BytesIO(bad.encode("utf-8")), "text/csv")},
                 headers=auth_headers)
    assert r.status_code == 400 and "重复" in str(r.json()["detail"])


# ── 本体映射准入（第一性原理：与上传数据集同权）──────────────────
def test_online_table_bindable_to_ontology_mapping(api, auth_headers, ontology):
    ds_id = _create_table(api, auth_headers).json()["data"]["id"]
    api.post(f"/api/v2/datasets/{ds_id}/rows/edit", json={
        "base_version_no": 1,
        "inserts": [{"values": {"编号": "A1", "名称": "泵机", "数量": "10"}}],
    }, headers=auth_headers)

    r = api.post(f"/api/v2/ontologies/{ontology['id']}/mappings", json={
        "curated_dataset_id": ds_id,
        "entity_class": "Item",
        "field_mapping": {"编号": "code"},
    }, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["mapping_id"]
