from datetime import datetime, timedelta, timezone

from app.data_channel.curated.router import list_curated
from app.data_channel.datasets.router import datasets_overview
from app.models.v2.dataset import Dataset


def _dataset(*, name, kind, created_at, updated_at, schema=None):
    return Dataset(
        name=name,
        kind=kind,
        schema_json=schema or {},
        created_at=created_at,
        updated_at=updated_at,
    )


def test_curated_page_orders_by_latest_update(db):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    db.add_all([
        _dataset(name="较早更新成品", kind="curated", created_at=base + timedelta(days=2), updated_at=base),
        _dataset(name="最近更新成品", kind="curated", created_at=base, updated_at=base + timedelta(days=3)),
        _dataset(name="中间更新成品", kind="curated", created_at=base + timedelta(days=1), updated_at=base + timedelta(days=1)),
    ])
    db.commit()

    first = list_curated(
        pipeline="", task_id="", status="", page=1, page_size=2,
        paginated=True, db=db,
    )
    assert first["total"] == 3
    assert [item.name for item in first["items"]] == ["最近更新成品", "中间更新成品"]

    second = list_curated(
        pipeline="", task_id="", status="", page=2, page_size=2,
        paginated=True, db=db,
    )
    assert [item.name for item in second["items"]] == ["较早更新成品"]

    legacy = list_curated(
        pipeline="", task_id="", status="", page=1, page_size=20,
        paginated=False, db=db,
    )
    assert isinstance(legacy, list)
    assert [item.name for item in legacy] == ["最近更新成品", "中间更新成品", "较早更新成品"]


def test_manual_dataset_page_orders_by_creation_and_excludes_sync(db):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    db.add_all([
        _dataset(
            name="最早创建人工数据集", kind="structured",
            created_at=base, updated_at=base + timedelta(days=8),
        ),
        _dataset(
            name="中间创建人工数据集", kind="structured",
            created_at=base + timedelta(days=1), updated_at=base + timedelta(days=6),
            schema={"origin": "manual"},
        ),
        _dataset(
            name="最新创建人工数据集", kind="structured",
            created_at=base + timedelta(days=2), updated_at=base + timedelta(days=2),
        ),
        _dataset(
            name="SYNC::不应出现", kind="structured",
            created_at=base + timedelta(days=3), updated_at=base + timedelta(days=3),
        ),
    ])
    db.commit()

    first = datasets_overview(
        source="manual", sort_by="created_at", page=1, page_size=2,
        paginated=True, db=db,
    )
    assert first["total"] == 3
    assert [item["name"] for item in first["items"]] == ["最新创建人工数据集", "中间创建人工数据集"]

    second = datasets_overview(
        source="manual", sort_by="created_at", page=2, page_size=2,
        paginated=True, db=db,
    )
    assert [item["name"] for item in second["items"]] == ["最早创建人工数据集"]
