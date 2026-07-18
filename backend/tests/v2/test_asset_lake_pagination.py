from datetime import datetime, timedelta, timezone

from app.data_channel.curated.router import list_curated
from app.data_channel.datasets.router import datasets_overview
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.curated import CuratedReview


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


def test_curated_reviewed_filter_includes_approved_and_rejected_decisions(db):
    """界面审核状态只表达“当前版本是否仍需人工处理”。"""
    base = datetime(2026, 7, 2, tzinfo=timezone.utc)
    approved = _dataset(name="已通过版本", kind="curated", created_at=base, updated_at=base)
    rejected = _dataset(name="已拒绝版本", kind="curated", created_at=base, updated_at=base)
    pending = _dataset(name="待审核版本", kind="curated", created_at=base, updated_at=base)
    db.add_all([approved, rejected, pending])
    db.flush()

    versions = [
        DatasetVersion(dataset_id=approved.id, version_no=1, rowcount=3),
        DatasetVersion(dataset_id=rejected.id, version_no=1, rowcount=2),
        DatasetVersion(dataset_id=pending.id, version_no=1, rowcount=1),
    ]
    db.add_all(versions)
    db.flush()
    db.add_all([
        CuratedReview(
            curated_dataset_id=approved.id,
            dataset_version_id=versions[0].id,
            status="approved",
        ),
        CuratedReview(
            curated_dataset_id=rejected.id,
            dataset_version_id=versions[1].id,
            status="rejected",
        ),
    ])
    db.commit()

    reviewed = list_curated(
        pipeline="", task_id="", status="reviewed", page=1, page_size=20,
        paginated=True, db=db,
    )
    assert reviewed["total"] == 2
    assert {item.name for item in reviewed["items"]} == {"已通过版本", "已拒绝版本"}
    assert {item.status for item in reviewed["items"]} == {"approved", "rejected"}

    waiting = list_curated(
        pipeline="", task_id="", status="pending_review", page=1, page_size=20,
        paginated=True, db=db,
    )
    assert waiting["total"] == 1
    assert waiting["items"][0].name == "待审核版本"


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

    filtered = datasets_overview(
        db, source="manual", search="中间创建", sort_by="created_at",
        page=1, page_size=10, paginated=True,
    )
    assert filtered["total"] == 1
    assert [item["name"] for item in filtered["items"]] == ["中间创建人工数据集"]

    # 保留旧测试和内部调用的首个位置参数约定：datasets_overview(db)。
    legacy = datasets_overview(db)
    assert legacy["total"] == 4
