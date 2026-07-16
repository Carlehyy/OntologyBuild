from datetime import datetime, timedelta, timezone

from app.models.ontology_formal import ActionExecutionLog
from app.models.sentinel import SentinelFiring


def test_overview_returns_daily_runtime_buckets(client, auth_headers, ontology, db):
    ontology_id = ontology["id"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    db.add_all([
        SentinelFiring(
            ontology_id=ontology_id,
            sentinel_id="sentinel-fired",
            trigger_source="change",
            status="fired",
            created_at=now - timedelta(days=6),
        ),
        SentinelFiring(
            ontology_id=ontology_id,
            sentinel_id="sentinel-error",
            trigger_source="schedule",
            status="error",
            created_at=now - timedelta(days=2),
        ),
        SentinelFiring(
            ontology_id=ontology_id,
            sentinel_id="sentinel-old",
            trigger_source="change",
            status="fired",
            created_at=now - timedelta(days=8),
        ),
        ActionExecutionLog(
            ontology_id=ontology_id,
            action_id="action-success",
            status="success",
            dry_run=False,
            executed_at=now - timedelta(days=5),
        ),
        ActionExecutionLog(
            ontology_id=ontology_id,
            action_id="action-failed",
            status="failed",
            dry_run=False,
            executed_at=now - timedelta(days=1),
        ),
        ActionExecutionLog(
            ontology_id=ontology_id,
            action_id="action-old",
            status="failed",
            dry_run=False,
            executed_at=now - timedelta(days=9),
        ),
        ActionExecutionLog(
            ontology_id=ontology_id,
            action_id="action-dry-run",
            status="success",
            dry_run=True,
            executed_at=now,
        ),
    ])
    db.commit()

    response = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/overview",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    runtime = response.json()["data"]["runtime"]
    daily = runtime["daily7d"]

    assert len(daily) == 7
    assert [item["date"] for item in daily] == sorted(item["date"] for item in daily)
    assert sum(item["firings"]["fired"] for item in daily) == 1
    assert sum(item["firings"]["error"] for item in daily) == 1
    assert sum(item["actionRuns"]["success"] for item in daily) == 1
    assert sum(item["actionRuns"]["failed"] for item in daily) == 1
    assert runtime["firings7d"] == {"total": 2, "fired": 1, "error": 1}
    assert runtime["actionRuns7d"] == {"total": 2, "success": 1, "failed": 1}
