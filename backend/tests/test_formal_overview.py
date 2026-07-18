from datetime import datetime, timedelta, timezone

from app.models.ontology_formal import (
    ActionExecutionLog, ObjectInstance, ObjectType, PropertyFact,
)
from app.models.ontology_version import OntologyVersion
from app.models.sentinel import SentinelFiring
from app.models.v2.mapping import OntologyMapping


def test_overview_returns_daily_runtime_buckets(client, auth_headers, ontology, db):
    ontology_id = ontology["id"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    release = db.query(OntologyVersion).filter_by(
        id=ontology["current_release_id"]).one()
    release.published_at = now - timedelta(days=7)
    release.snapshot_formal = {
        "objectTypes": [], "linkTypes": [], "functions": [], "mappings": [],
        "linkMappings": [],
        "actions": [
            {"id": "action-success", "name": "success"},
            {"id": "action-failed", "name": "failed"},
            {"id": "action-old", "name": "old"},
            {"id": "action-dry-run", "name": "dry-run"},
        ],
        "sentinels": [
            {"id": "sentinel-fired", "name": "fired", "enabled": True},
            {"id": "sentinel-error", "name": "error", "enabled": True},
            {"id": "sentinel-old", "name": "old", "enabled": True},
        ],
    }

    db.add_all([
        SentinelFiring(
            ontology_id=ontology_id,
            sentinel_id="sentinel-fired",
            trigger_source="change",
            status="fired",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            created_at=now - timedelta(days=6),
        ),
        SentinelFiring(
            ontology_id=ontology_id,
            sentinel_id="sentinel-error",
            trigger_source="schedule",
            status="error",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            created_at=now - timedelta(days=2),
        ),
        SentinelFiring(
            ontology_id=ontology_id,
            sentinel_id="sentinel-old",
            trigger_source="change",
            status="fired",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            created_at=now - timedelta(days=8),
        ),
        ActionExecutionLog(
            ontology_id=ontology_id,
            action_id="action-success",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            status="success",
            dry_run=False,
            executed_at=now - timedelta(days=5),
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
        ActionExecutionLog(
            ontology_id=ontology_id,
            action_id="action-old",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
            status="failed",
            dry_run=False,
            executed_at=now - timedelta(days=9),
        ),
        ActionExecutionLog(
            ontology_id=ontology_id,
            action_id="action-dry-run",
            ontology_version=release.version_number,
            ontology_release_id=release.id,
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


def test_overview_is_scoped_to_current_release_snapshot(
        client, auth_headers, ontology, db):
    ontology_id = ontology["id"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    release = db.query(OntologyVersion).filter_by(
        id=ontology["current_release_id"]).one()
    release.published_at = now - timedelta(days=1)
    release.snapshot_formal = {
        "objectTypes": [{
            "id": "ot-release", "name": "ReleasedOrder",
            "displayName": "发布订单", "primaryKey": "id", "properties": [],
        }],
        "linkTypes": [],
        "actions": [{
            "id": "action-release", "name": "review_order",
            "displayName": "审核订单", "requiresApproval": True,
        }],
        "functions": [{"id": "fn-release", "name": "order_name"}],
        "sentinels": [],
        "mappings": [{
            "id": "mapping-release", "entityClass": "ReleasedOrder",
            "targetObjectTypeId": "ot-release", "fieldMapping": {"id": "id"},
        }],
        "linkMappings": [],
    }

    # Mutable runtime rows deliberately drift from the immutable release.  The
    # overview must not count schema/configuration or instances outside v0.
    db.add_all([
        ObjectType(
            id="ot-rogue", ontology_id=ontology_id, name="Rogue",
            display_name="旁路类型", properties=[]),
        ObjectInstance(
            id="instance-release", ontology_id=ontology_id,
            object_type_id="ot-release", properties={"id": "R-1"},
            source="pipeline"),
        ObjectInstance(
            id="instance-rogue", ontology_id=ontology_id,
            object_type_id="ot-rogue", properties={"id": "X-1"},
            source="manual"),
        OntologyMapping(
            id="mapping-rogue", ontology_id=ontology_id,
            entity_class="Rogue", field_mapping={}, status="draft"),
        ActionExecutionLog(
            id="pending-current", ontology_id=ontology_id,
            action_id="action-release", ontology_version=release.version_number,
            ontology_release_id=release.id,
            status="pending", dry_run=False, executed_at=now),
        ActionExecutionLog(
            id="pending-old", ontology_id=ontology_id,
            action_id="action-release", ontology_version=release.version_number,
            ontology_release_id="release-old-same-version",
            status="pending", dry_run=False, executed_at=now),
        ActionExecutionLog(
            id="run-current", ontology_id=ontology_id,
            action_id="action-release", ontology_version=release.version_number,
            ontology_release_id=release.id,
            status="success", dry_run=False, executed_at=now),
        ActionExecutionLog(
            id="run-rogue", ontology_id=ontology_id,
            action_id="action-rogue", ontology_version=release.version_number,
            ontology_release_id=release.id,
            status="success", dry_run=False, executed_at=now),
        PropertyFact(
            id="fact-current", ontology_id=ontology_id,
            instance_id="instance-release", object_type_id="ot-release",
            property_name="status", value={"v": "ready"}, kind="property",
            source="pipeline", ontology_version=release.version_number,
            ontology_release_id=release.id, recorded_at=now),
        PropertyFact(
            id="fact-before-release", ontology_id=ontology_id,
            instance_id="instance-release", object_type_id="ot-release",
            property_name="status", value={"v": "old"}, kind="property",
            source="pipeline", ontology_version=release.version_number,
            ontology_release_id="release-old-same-version",
            recorded_at=now - timedelta(days=2)),
        PropertyFact(
            id="fact-rogue", ontology_id=ontology_id,
            instance_id="instance-rogue", object_type_id="ot-rogue",
            property_name="status", value={"v": "rogue"}, kind="property",
            source="manual", ontology_version=release.version_number,
            ontology_release_id=release.id, recorded_at=now),
    ])
    db.commit()

    response = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/overview",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["release"] == {
        "id": release.id,
        "version": release.version_number,
        "publishedAt": release.published_at.isoformat(),
    }
    assert body["model"] == {
        "objectTypes": 1,
        "linkTypes": 0,
        "actions": 1,
        "actionsRequiringApproval": 1,
        "functions": 1,
        "sentinels": {"total": 0, "enabled": 0, "muted": 0},
    }
    assert body["data"]["instances"] == 1
    assert body["data"]["instancesBySource"] == {"pipeline": 1}
    assert body["data"]["mappings"] == {
        "total": 1, "bound": 1, "nameMatch": 0,
        "autoCreate": 0, "autoApply": 0,
    }
    assert body["runtime"]["pendingApprovals"] == 1
    assert body["runtime"]["actionRuns7d"] == {
        "total": 1, "success": 1, "failed": 0,
    }
    assert body["facts"] == {"total": 1, "byKind": {"property": 1}}

    pending = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/pending-actions"
        "?current_release_only=true",
        headers=auth_headers,
    )
    assert pending.status_code == 200, pending.text
    assert [item["id"] for item in pending.json()["data"]] == ["pending-current"]

    facts = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/facts/recent"
        "?current_release_only=true",
        headers=auth_headers,
    )
    assert facts.status_code == 200, facts.text
    assert [item["id"] for item in facts.json()["data"]] == ["fact-current"]
