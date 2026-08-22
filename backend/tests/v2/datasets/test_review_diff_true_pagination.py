"""审核三视图真分页（MYW-41 方案 A）。

覆盖：最新湖表版本 SQL 真分页、历史版本窗口化回放（不整版物化）、
分页回放与全量回放的对拍等价、stale 审核钉住旧版本的回放分页、
pending 行编辑在分页当前视图上的叠加。
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.data_channel.curated.review_service import ReviewService
from app.data_channel.datasets import lake_store
from app.data_channel.datasets.lake_gate import split_pk
from app.data_channel.datasets.models import Dataset
from app.main import app
from app.routers.v2 import curated as curated_module
from app.services.v2.dataset_service import DatasetService


@pytest.fixture(autouse=True)
def _drop_lake_tables(db):
    """物理湖表不在 Base.metadata 中（运行时 DDL），逐测试清理避免串库。"""
    yield
    conn = db.connection()
    for name in sa.inspect(conn).get_table_names():
        if name.startswith("lake_ds_"):
            conn.execute(sa.text(f'DROP TABLE IF EXISTS "{name}"'))
    db.commit()


@pytest.fixture
def api(client, db):
    def _override():
        yield db
    app.dependency_overrides[curated_module.get_db] = _override
    yield client
    app.dependency_overrides.pop(curated_module.get_db, None)


def _make_lake_dataset(db, runs, pk="id", columns=("id", "name")):
    ds = Dataset(
        id=str(uuid.uuid4()), name=f"真分页-{uuid.uuid4().hex[:6]}",
        kind="curated",
        schema_json={"primary_key": pk, "columns": list(columns)})
    db.add(ds)
    db.commit()
    versions = []
    for mode, rows in runs:
        version, _ = lake_store.upsert_run(db, ds, rows, mode, split_pk(pk))
        versions.append(version)
    db.refresh(ds)
    return ds, versions


def _review_diff(api, auth_headers, ds_id, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    path = f"/api/v2/curated/{ds_id}/review-diff"
    if query:
        path += f"?{query}"
    r = api.get(path, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json().get("data", r.json())


# ── 当前视图：最新湖表版本走物理表 SQL 真分页 ──────────────────
def test_review_diff_current_latest_lake_true_paging(api, auth_headers, db):
    ds, _ = _make_lake_dataset(db, [
        ("overwrite", [{"id": str(i), "name": f"n{i}"} for i in range(5)]),
    ])

    middle = _review_diff(api, auth_headers, ds.id, limit=2, offset=2)
    assert middle["current"]["version_no"] == 1
    assert middle["current"]["total"] == 5
    assert [r["id"] for r in middle["current"]["rows"]] == ["2", "3"]
    assert middle["current"]["has_more"] is True
    # 首个版本没有上一版可对照
    assert middle["previous"]["total"] == 0
    assert middle["previous"]["version_no"] is None
    # delta 来自变更集：首版全为新增
    assert middle["delta"]["added_count"] == 5

    tail = _review_diff(api, auth_headers, ds.id, limit=2, offset=4)
    assert [r["id"] for r in tail["current"]["rows"]] == ["4"]
    assert tail["current"]["has_more"] is False


# ── 历史视图：窗口化回放与全量回放对拍 ────────────────────────
def test_previous_view_paged_replay_matches_full_replay(api, auth_headers, db):
    ds, (_v1, _v2, _v3) = _make_lake_dataset(db, [
        ("overwrite", [{"id": str(i), "name": f"n{i}"} for i in range(8)]),
        ("upsert", [{"id": "3", "name": "改3"}, {"id": "9", "name": "增9"}]),
        ("overwrite", [{"id": "0", "name": "零"},
                       {"id": "5", "name": "改5"},
                       {"id": "11", "name": "增11"}]),
    ])
    # 无 pending 审核时 current 钉在最新版（v3），上一版即 v2（其后有 2 个变更集）
    oracle_version = DatasetService(db).list_versions(ds.id)[-2]
    oracle = lake_store.rows_at_version(db, ds, oracle_version.version_no)
    oracle_pairs = sorted((r["id"], r["name"]) for r in oracle)

    collected: list[tuple[str, str]] = []
    offset, limit = 0, 3
    while True:
        body = _review_diff(
            api, auth_headers, ds.id, limit=limit, offset=offset)
        previous = body["previous"]
        assert previous["version_no"] == oracle_version.version_no
        assert previous["total"] == len(oracle)
        assert previous["has_more"] == (offset + limit < len(oracle))
        collected.extend(
            (r["id"], r["name"]) for r in previous["rows"])
        if not previous["has_more"]:
            break
        offset += limit

    assert sorted(collected) == oracle_pairs
    # 同一请求重复发起，窗口内容逐行一致（跨请求顺序稳定）
    again = _review_diff(api, auth_headers, ds.id, limit=3, offset=3)
    assert [(r["id"], r["name"]) for r in again["previous"]["rows"]] == \
        collected[3:6]


def test_page_rows_at_version_windows_equal_full_replay(db):
    ds, versions = _make_lake_dataset(db, [
        ("overwrite", [{"id": str(i), "name": f"n{i}"} for i in range(6)]),
        ("upsert", [{"id": "2", "name": "改2"}, {"id": "7", "name": "增7"}]),
        ("overwrite", [{"id": "1", "name": "壹"},
                       {"id": "4", "name": "改4"},
                       {"id": "8", "name": "增8"}]),
    ])
    for version in versions[:-1]:
        oracle = lake_store.rows_at_version(db, ds, version.version_no)
        oracle_pairs = sorted((r["id"], r["name"]) for r in oracle)

        windows: list[dict] = []
        offset, limit = 0, 4
        while True:
            page, total = lake_store.page_rows_at_version(
                db, ds, version.version_no, offset=offset, limit=limit)
            assert total == len(oracle)
            windows.extend(page)
            if offset + limit >= total or not page:
                break
            offset += limit
        assert sorted((r["id"], r["name"]) for r in windows) == oracle_pairs

        replay, total = lake_store.page_rows_at_version(
            db, ds, version.version_no, offset=0, limit=999)
        assert total == len(oracle)
        assert [(r["id"], r["name"]) for r in replay] == \
            [(r["id"], r["name"]) for r in replay]


# ── stale 审核：钉住旧湖表版本的回放分页 ──────────────────────
def test_stale_pending_pins_old_lake_version_and_pages(api, auth_headers, db):
    ds, (v1,) = _make_lake_dataset(db, [
        ("overwrite", [{"id": "a", "name": "甲"}, {"id": "b", "name": "乙"},
                       {"id": "c", "name": "丙"}]),
    ])
    review = ReviewService(db).start_review(ds.id)
    assert review.dataset_version_id == v1.id

    # 审核期间流水线又入湖了新版本：pending 被钉在 v1，读取走窗口化回放
    lake_store.upsert_run(db, ds, [{"id": "a", "name": "新甲"}],
                          "overwrite", split_pk("id"))
    db.refresh(ds)

    body = _review_diff(api, auth_headers, ds.id)
    assert body["review"]["id"] == review.id
    assert body["review"]["stale"] is True
    assert body["current"]["version_no"] == 1
    assert body["current"]["total"] == 3

    paged = _review_diff(api, auth_headers, ds.id, limit=2, offset=1)
    assert paged["current"]["total"] == 3
    assert paged["current"]["has_more"] is False
    assert [r["id"] for r in paged["current"]["rows"]] == ["b", "c"]
    # 上一版视图同样可分页（v1 没有更早版本 → 空）
    assert paged["previous"]["version_no"] is None


# ── 行级编辑叠加到分页后的当前视图 ────────────────────────────
def test_pending_edit_overlays_paged_current_view(api, auth_headers, db):
    ds, _ = _make_lake_dataset(db, [
        ("overwrite", [{"id": "0", "name": "n0"}, {"id": "1", "name": "n1"},
                       {"id": "2", "name": "n2"}]),
    ])
    service = ReviewService(db)
    review = service.start_review(ds.id)
    service.batch_edit_rows(review.id, [{
        "row_pk": "1", "field_name": "name",
        "old_value": "n1", "new_value": "已修正",
    }])

    body = _review_diff(api, auth_headers, ds.id, limit=2, offset=0)
    assert body["current"]["total"] == 3
    assert [r["name"] for r in body["current"]["rows"]] == ["n0", "已修正"]
    assert body["current_row_pks"] == ["0", "1"]

    second_page = _review_diff(api, auth_headers, ds.id, limit=2, offset=2)
    assert [r["name"] for r in second_page["current"]["rows"]] == ["n2"]
