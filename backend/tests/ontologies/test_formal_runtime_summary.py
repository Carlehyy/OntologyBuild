from datetime import datetime, timedelta, timezone

from app.models.ontology_version import OntologyVersion
from app.models.sentinel import SentinelFiring


def _seed_release_lineage(db, ontology, published_days_ago: int):
    """发布快照带 3 哨兵 + 4 动作，运行记录散布在窗口内外。"""
    ontology_id = ontology["id"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    release = db.query(OntologyVersion).filter_by(
        id=ontology["current_release_id"]).one()
    release.published_at = now - timedelta(days=published_days_ago)
    release.snapshot_formal = {
        "objectTypes": [], "linkTypes": [], "functions": [], "mappings": [],
        "linkMappings": [],
        "actions": [
            {"id": "action-success", "name": "success"},
            {"id": "action-failed", "name": "failed"},
            {"id": "action-early", "name": "early"},
            {"id": "action-dry-run", "name": "dry-run"},
        ],
        "sentinels": [
            {"id": "sentinel-fired", "name": "fired", "enabled": True},
            {"id": "sentinel-error", "name": "error", "enabled": True},
            {"id": "sentinel-early", "name": "early", "enabled": True},
        ],
    }
    db.add_all([
        # 窗口内的哨兵评估：一次命中、一次出错
        SentinelFiring(
            ontology_id=ontology_id,
            sentinel_id="sentinel-fired",
            trigger_source="change",
            status="fired",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            created_at=now - timedelta(days=2),
        ),
        SentinelFiring(
            ontology_id=ontology_id,
            sentinel_id="sentinel-error",
            trigger_source="schedule",
            status="error",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            created_at=now - timedelta(days=1),
        ),
        # 发布之前的评估：不得计入任何桶
        SentinelFiring(
            ontology_id=ontology_id,
            sentinel_id="sentinel-early",
            trigger_source="change",
            status="fired",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            created_at=now - timedelta(days=published_days_ago + 3),
        ),
    ])
    db.commit()
    return release, now


def test_runtime_summary_returns_requested_day_buckets(client, auth_headers, ontology, db):
    from app.models.ontology_formal import ActionExecutionLog

    ontology_id = ontology["id"]
    release, now = _seed_release_lineage(db, ontology, published_days_ago=30)
    db.add_all([
        ActionExecutionLog(
            ontology_id=ontology_id,
            action_id="action-success",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            status="success",
            dry_run=False,
            executed_at=now - timedelta(days=2),
        ),
        ActionExecutionLog(
            ontology_id=ontology_id,
            action_id="action-failed",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            status="failed",
            dry_run=False,
            executed_at=now - timedelta(days=1),
        ),
        # 发布之前执行的动作：不得计入
        ActionExecutionLog(
            ontology_id=ontology_id,
            action_id="action-early",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            status="failed",
            dry_run=False,
            executed_at=now - timedelta(days=40),
        ),
        # dry-run 与未出结果的执行：与 overview 口径一致，不计入
        ActionExecutionLog(
            ontology_id=ontology_id,
            action_id="action-dry-run",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            status="success",
            dry_run=True,
            executed_at=now,
        ),
        ActionExecutionLog(
            ontology_id=ontology_id,
            action_id="action-success",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            status="pending",
            dry_run=False,
            executed_at=now,
        ),
    ])
    db.commit()

    start = (now - timedelta(days=29)).date().isoformat()
    end = now.date().isoformat()
    response = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/runtime-summary"
        f"?start={start}&end={end}",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]

    assert body["start"] == start
    assert body["end"] == end
    assert len(body["days"]) == 30
    assert [item["date"] for item in body["days"]] == sorted(
        item["date"] for item in body["days"])
    assert sum(item["firings"]["fired"] for item in body["days"]) == 1
    assert sum(item["firings"]["error"] for item in body["days"]) == 1
    assert sum(item["actionRuns"]["success"] for item in body["days"]) == 1
    assert sum(item["actionRuns"]["failed"] for item in body["days"]) == 1
    # 发布时间早于窗口起点时，窗口桶全部有效；发布前的记录不得回流
    assert body["days"][0]["firings"] == {"fired": 0, "error": 0}
    assert body["days"][0]["actionRuns"] == {"success": 0, "failed": 0}


def test_runtime_summary_buckets_before_release_stay_zero(client, auth_headers, ontology, db):
    """窗口早于发布时间的桶如实为 0——当前发布血缘口径，不跨版本凑数。"""
    ontology_id = ontology["id"]
    _, now = _seed_release_lineage(db, ontology, published_days_ago=2)

    start = (now - timedelta(days=9)).date().isoformat()
    end = now.date().isoformat()
    response = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/runtime-summary"
        f"?start={start}&end={end}",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    days = response.json()["data"]["days"]
    assert len(days) == 10
    before_release = [item for item in days if item["date"] < (now - timedelta(days=2)).date().isoformat()]
    assert before_release, "fixture 应包含发布前的桶"
    assert all(
        item["firings"] == {"fired": 0, "error": 0}
        and item["actionRuns"] == {"success": 0, "failed": 0}
        for item in before_release
    )
    assert sum(item["firings"]["fired"] for item in days) == 1
    assert sum(item["firings"]["error"] for item in days) == 1


def test_runtime_summary_rejects_bad_ranges(client, auth_headers, ontology):
    ontology_id = ontology["id"]
    base = f"/api/v2/formal/ontologies/{ontology_id}/runtime-summary"

    bad_format = client.get(
        f"{base}?start=2026-13-40&end=2026-08-27", headers=auth_headers)
    assert bad_format.status_code == 422

    missing = client.get(f"{base}?start=2026-08-01", headers=auth_headers)
    assert missing.status_code == 422

    reversed_range = client.get(
        f"{base}?start=2026-08-27&end=2026-08-01", headers=auth_headers)
    assert reversed_range.status_code == 422

    # 跨度超过 92 天拒绝（2026-01-01 → 2026-04-30 = 119 天）
    too_long = client.get(
        f"{base}?start=2026-01-01&end=2026-04-30", headers=auth_headers)
    assert too_long.status_code == 422

    # 恰好 92 天允许（2026-01-01 → 2026-04-02）
    ok = client.get(f"{base}?start=2026-01-01&end=2026-04-02", headers=auth_headers)
    assert ok.status_code == 200, ok.text
    assert len(ok.json()["data"]["days"]) == 92
