from datetime import datetime, timedelta, timezone

from app.data_channel.pipelines import management_service
from app.data_channel.pipelines.models import Pipeline, PipelineRun
from app.data_channel.pipelines.router import get_db as get_pipeline_db
from app.main import app


def _pipeline(*, name, created_by, created_at, updated_at, engine, enabled=False):
    return Pipeline(
        name=name,
        created_by=created_by,
        created_at=created_at,
        updated_at=updated_at,
        definition={"engine": engine},
        status="draft",
        enabled=enabled,
    )


def _use_test_db(db):
    def override_pipeline_db():
        yield db

    app.dependency_overrides[get_pipeline_db] = override_pipeline_db


def test_paginated_list_orders_by_created_at_and_keeps_legacy_shape(
    client, auth_headers, admin_user, db,
):
    _use_test_db(db)
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    db.add_all([
        _pipeline(
            name="旧流水线（最近编辑）",
            created_by=admin_user.id,
            created_at=base,
            updated_at=base + timedelta(days=10),
            engine="python",
        ),
        _pipeline(
            name="中间创建",
            created_by=admin_user.id,
            created_at=base + timedelta(days=1),
            updated_at=base + timedelta(days=1),
            engine="python",
        ),
        _pipeline(
            name="最新创建",
            created_by=admin_user.id,
            created_at=base + timedelta(days=2),
            updated_at=base + timedelta(days=2),
            engine="n8n",
            enabled=True,
        ),
    ])
    db.commit()

    first_page = client.get(
        "/api/v2/pipelines",
        params={"paginated": True, "page": 1, "page_size": 2},
        headers=auth_headers,
    )
    assert first_page.status_code == 200
    payload = first_page.json()
    assert payload["total"] == 3
    assert payload["page"] == 1
    assert payload["page_size"] == 2
    overview = payload["overview"]
    trend_7d = overview.pop("trend_7d")
    assert overview == {
        "total": 3,
        "published": 0,
        "enabled": 1,
        "latest_failed": 0,
    }
    assert len(trend_7d) == 7
    assert all(item["runs"] == 0 and item["errors"] == 0 for item in trend_7d)
    assert [item["name"] for item in payload["items"]] == ["最新创建", "中间创建"]

    second_page = client.get(
        "/api/v2/pipelines",
        params={"paginated": True, "page": 2, "page_size": 2},
        headers=auth_headers,
    )
    assert second_page.status_code == 200
    assert [item["name"] for item in second_page.json()["items"]] == ["旧流水线（最近编辑）"]

    legacy = client.get("/api/v2/pipelines", headers=auth_headers)
    assert legacy.status_code == 200
    assert isinstance(legacy.json(), list)
    assert [item["name"] for item in legacy.json()] == ["最新创建", "中间创建", "旧流水线（最近编辑）"]


def test_paginated_list_applies_source_and_enabled_filters(
    client, auth_headers, admin_user, db,
):
    _use_test_db(db)
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    db.add_all([
        _pipeline(
            name="n8n 已启用",
            created_by=admin_user.id,
            created_at=base,
            updated_at=base,
            engine="n8n",
            enabled=True,
        ),
        _pipeline(
            name="脚本未启用",
            created_by=admin_user.id,
            created_at=base + timedelta(days=1),
            updated_at=base + timedelta(days=1),
            engine="python",
        ),
    ])
    db.commit()

    response = client.get(
        "/api/v2/pipelines",
        params={
            "paginated": True,
            "engine": "n8n",
            "enabled": True,
            "page": 1,
            "page_size": 10,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["name"] for item in payload["items"]] == ["n8n 已启用"]

    python_response = client.get(
        "/api/v2/pipelines",
        params={"paginated": True, "engine": "python"},
        headers=auth_headers,
    )
    assert python_response.status_code == 200
    assert [item["name"] for item in python_response.json()["items"]] == ["脚本未启用"]


def test_paginated_overview_is_global_and_counts_only_latest_failures(
    client, auth_headers, admin_user, db,
):
    _use_test_db(db)
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    python_pipeline = _pipeline(
        name="Python 采集",
        created_by=admin_user.id,
        created_at=base,
        updated_at=base,
        engine="python",
    )
    n8n_pipeline = _pipeline(
        name="n8n 采集",
        created_by=admin_user.id,
        created_at=base + timedelta(days=1),
        updated_at=base + timedelta(days=1),
        engine="n8n",
        enabled=True,
    )
    n8n_pipeline.status = "published"
    archived = _pipeline(
        name="已归档",
        created_by=admin_user.id,
        created_at=base + timedelta(days=2),
        updated_at=base + timedelta(days=2),
        engine="python",
    )
    archived.status = "archived"
    db.add_all([python_pipeline, n8n_pipeline, archived])
    db.flush()
    db.add_all([
        PipelineRun(pipeline_id=python_pipeline.id, status="failed", created_at=base),
        PipelineRun(pipeline_id=python_pipeline.id, status="success", created_at=base + timedelta(hours=1)),
        PipelineRun(pipeline_id=n8n_pipeline.id, status="failed", created_at=base + timedelta(hours=2)),
        PipelineRun(pipeline_id=archived.id, status="failed", created_at=base + timedelta(hours=3)),
    ])
    db.commit()

    response = client.get(
        "/api/v2/pipelines",
        params={"paginated": True, "engine": "n8n"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["name"] for item in payload["items"]] == ["n8n 采集"]
    overview = payload["overview"]
    overview.pop("trend_7d")
    assert overview == {
        "total": 2,
        "published": 1,
        "enabled": 1,
        "latest_failed": 1,
    }


def test_paginated_overview_trend_7d_counts_real_runs(
    client, auth_headers, admin_user, db,
):
    _use_test_db(db)
    now = datetime.now(timezone.utc)
    active_pipeline = _pipeline(
        name="活跃流水线",
        created_by=admin_user.id,
        created_at=now,
        updated_at=now,
        engine="python",
    )
    archived = _pipeline(
        name="已归档",
        created_by=admin_user.id,
        created_at=now,
        updated_at=now,
        engine="python",
    )
    archived.status = "archived"
    db.add_all([active_pipeline, archived])
    db.flush()
    three_days_ago = now - timedelta(days=3)
    db.add_all([
        PipelineRun(pipeline_id=active_pipeline.id, status="success", created_at=now - timedelta(hours=1)),
        PipelineRun(pipeline_id=active_pipeline.id, status="failed", created_at=now - timedelta(hours=2)),
        PipelineRun(pipeline_id=active_pipeline.id, status="success", created_at=three_days_ago),
        PipelineRun(pipeline_id=active_pipeline.id, status="failed", created_at=now - timedelta(days=8)),
        PipelineRun(pipeline_id=archived.id, status="failed", created_at=now - timedelta(minutes=30)),
    ])
    db.commit()

    response = client.get(
        "/api/v2/pipelines",
        params={"paginated": True},
        headers=auth_headers,
    )

    assert response.status_code == 200
    trend = response.json()["overview"]["trend_7d"]
    assert len(trend) == 7
    # 8 天前的运行与已归档流水线的运行不计入趋势
    assert sum(item["runs"] for item in trend) == 3
    assert sum(item["errors"] for item in trend) == 1
    three_day_key = management_service._shanghai_date(three_days_ago).isoformat()
    three_day_entry = next(item for item in trend if item["date"] == three_day_key)
    assert three_day_entry == {"date": three_day_key, "runs": 1, "errors": 0}
    # 日期连续且按上海自然日升序
    dates = [item["date"] for item in trend]
    assert dates == sorted(dates)
    assert dates[-1] == management_service._shanghai_date(now).isoformat()
