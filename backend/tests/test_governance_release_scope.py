"""Governance page must be a fail-closed view of the current release."""
from __future__ import annotations

from app.models.ontology_formal import (
    ActionExecutionLog,
    ActionType,
    ObjectInstance,
    ObjectType,
    PropertyFact,
)
from app.models.ontology_version import OntologyVersion
from app.models.sentinel import (
    Notification,
    Sentinel,
    SentinelCdcOutbox,
    SentinelFiring,
)
from app.ontologies.formal_modeling.facts import record_property_facts


def _seed_release_and_drift(db, ontology: dict) -> str:
    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    release = db.query(OntologyVersion).filter_by(id=release_id).one()
    release.snapshot_formal = {
        "objectTypes": [{
            "id": "ot-published", "name": "PublishedOrder",
            "displayName": "发布订单", "primaryKey": "name",
            "properties": [{"id": "name", "name": "name", "type": "string"}],
        }],
        "linkTypes": [],
        "actions": [{
            "id": "action-published", "name": "published_action",
            "displayName": "发布动作", "objectTypeId": "ot-published",
            "parameters": [], "rules": [], "requiresApproval": True,
        }],
        "functions": [],
        "sentinels": [{
            "id": "sentinel-published", "name": "published_watcher",
            "displayName": "发布哨兵",
            "bindings": [{"alias": "o", "objectTypeId": "ot-published"}],
            "links": [], "condition": "o.active == True",
            "conditionRows": [], "conditionLogic": "and", "primaryAlias": "o",
            "actionIds": ["action-published"], "actionParameters": {},
            "onChange": True, "onSchedule": False,
            "scanIntervalSeconds": 300, "triggerMode": "on_enter",
            "muted": False, "enabled": True, "status": "draft",
        }],
        "mappings": [], "linkMappings": [],
    }

    # Mutable projection deliberately differs from the release.  Governance
    # must not leak these unpromoted names, flags, or extra definitions.
    object_type = ObjectType(
        id="ot-published", ontology_id=ontology_id,
        name="DraftOrder", display_name="草稿订单",
        primary_key="name", properties=[],
    )
    instance = ObjectInstance(
        id="order-1", ontology_id=ontology_id,
        object_type_id=object_type.id,
        properties={"name": "SO-1", "active": True},
        ontology_release_id=release_id,
    )
    released_action_projection = ActionType(
        id="action-published", ontology_id=ontology_id,
        name="draft_action_name", display_name="草稿动作名",
        object_type_id=object_type.id, parameters=[], rules=[],
        requires_approval=False,
    )
    draft_action = ActionType(
        id="action-draft-only", ontology_id=ontology_id,
        name="draft_only", display_name="未发布动作",
        object_type_id=object_type.id, parameters=[], rules=[],
        requires_approval=True,
    )
    released_sentinel_projection = Sentinel(
        id="sentinel-published", ontology_id=ontology_id,
        name="draft_watcher_name", display_name="草稿哨兵名",
        bindings=[], links=[], condition="False", action_ids=[],
        muted=True, enabled=False, status="draft",
    )
    draft_sentinel = Sentinel(
        id="sentinel-draft-only", ontology_id=ontology_id,
        name="draft_only", display_name="未发布哨兵",
        bindings=[], links=[], action_ids=[], enabled=True, status="draft",
    )
    db.add_all([
        object_type, instance, released_action_projection, draft_action,
        released_sentinel_projection, draft_sentinel,
        ActionExecutionLog(
            id="pending-current", ontology_id=ontology_id,
            action_id="action-published", action_name="草稿动作名",
            object_type_id=object_type.id, object_instance_id=instance.id,
            parameters={}, status="pending", dry_run=False,
            ontology_version="v0",
            ontology_release_id=release_id,
        ),
        ActionExecutionLog(
            id="pending-old", ontology_id=ontology_id,
            action_id="action-published", action_name="旧版动作",
            object_type_id=object_type.id, object_instance_id=instance.id,
            parameters={}, status="pending", dry_run=False,
            # Reused version label simulates rollback/re-release (ABA).  The
            # immutable release id must still keep this historical row out.
            ontology_version="v0",
            ontology_release_id="release-old-same-version",
        ),
        ActionExecutionLog(
            id="pending-draft-action", ontology_id=ontology_id,
            action_id="action-draft-only", action_name="未发布动作",
            object_type_id=object_type.id, object_instance_id=instance.id,
            parameters={}, status="pending", dry_run=False,
            ontology_version="v0",
            ontology_release_id=release_id,
        ),
        PropertyFact(
            id="fact-current", ontology_id=ontology_id,
            instance_id=instance.id, object_type_id=object_type.id,
            property_name="status", value={"v": "current"},
            kind="property", source="test", ontology_version="v0",
            ontology_release_id=release_id,
        ),
        PropertyFact(
            id="fact-old", ontology_id=ontology_id,
            instance_id=instance.id, object_type_id=object_type.id,
            property_name="status", value={"v": "old"},
            kind="property", source="test", ontology_version="v0",
            ontology_release_id="release-old-same-version",
        ),
        SentinelFiring(
            id="firing-current", ontology_id=ontology_id,
            sentinel_id="sentinel-published", sentinel_name="草稿哨兵名",
            trigger_source="manual", status="fired", ontology_version="v0",
            ontology_release_id=release_id,
        ),
        SentinelFiring(
            id="firing-old", ontology_id=ontology_id,
            sentinel_id="sentinel-published", sentinel_name="旧版哨兵",
            trigger_source="manual", status="fired", ontology_version="v0",
            ontology_release_id="release-old-same-version",
        ),
        SentinelFiring(
            id="firing-draft", ontology_id=ontology_id,
            sentinel_id="sentinel-draft-only", sentinel_name="未发布哨兵",
            trigger_source="manual", status="fired", ontology_version="v0",
            ontology_release_id=release_id,
        ),
    ])
    db.commit()
    return release_id


def test_governance_reads_only_current_release_snapshot(
    client, auth_headers, ontology, db,
):
    ontology_id = ontology["id"]
    release_id = _seed_release_and_drift(db, ontology)
    release_query = f"release_id={release_id}"

    pending_response = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/pending-actions?{release_query}",
        headers=auth_headers,
    )
    assert pending_response.status_code == 200, pending_response.text
    pending = pending_response.json()["data"]
    assert [item["id"] for item in pending] == ["pending-current"]
    assert pending[0]["actionName"] == "发布动作"
    assert pending[0]["objectTypeName"] == "发布订单"
    assert pending[0]["objectInstanceLabel"] == "发布订单 · SO-1"
    assert pending[0]["ontologyVersion"] == "v0"
    assert pending[0]["ontologyReleaseId"] == release_id

    autonomy_response = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/autonomy?{release_query}",
        headers=auth_headers,
    )
    assert autonomy_response.status_code == 200, autonomy_response.text
    autonomy = autonomy_response.json()["data"]
    assert [item["actionId"] for item in autonomy] == ["action-published"]
    assert autonomy[0]["actionName"] == "发布动作"
    assert autonomy[0]["requiresApproval"] is True
    assert autonomy[0]["level"] == "L1"
    assert autonomy[0]["sentinels"] == [{
        "id": "sentinel-published", "name": "发布哨兵",
        "muted": False, "enabled": True,
    }]

    sentinel_response = client.get(
        f"/api/v1/ontologies/{ontology_id}/sentinels/?{release_query}",
        headers=auth_headers,
    )
    assert sentinel_response.status_code == 200, sentinel_response.text
    sentinels = sentinel_response.json()["data"]
    assert [item["id"] for item in sentinels] == ["sentinel-published"]
    assert sentinels[0]["displayName"] == "发布哨兵"
    assert sentinels[0]["condition"] == "o.active == True"
    assert sentinels[0]["muted"] is False
    assert sentinels[0]["enabled"] is True
    assert sentinels[0]["status"] == "published"

    firings_response = client.get(
        f"/api/v1/ontologies/{ontology_id}/sentinels/firings?{release_query}",
        headers=auth_headers,
    )
    assert firings_response.status_code == 200, firings_response.text
    firings = firings_response.json()["data"]
    assert [item["id"] for item in firings] == ["firing-current"]
    assert firings[0]["sentinelName"] == "发布哨兵"
    assert firings[0]["ontologyVersion"] == "v0"
    assert firings[0]["ontologyReleaseId"] == release_id

    facts_response = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/facts/recent?{release_query}",
        headers=auth_headers,
    )
    assert facts_response.status_code == 200, facts_response.text
    facts = facts_response.json()["data"]
    assert [item["id"] for item in facts] == ["fact-current"]
    assert facts[0]["subjectLabel"] == "发布订单·SO-1"
    assert facts[0]["ontologyVersion"] == "v0"
    assert facts[0]["ontologyReleaseId"] == release_id


def test_governance_rejects_changed_or_cross_release_context(
    client, auth_headers, ontology, db,
):
    ontology_id = ontology["id"]
    release_id = _seed_release_and_drift(db, ontology)

    stale_read = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/facts/recent?release_id=stale-release",
        headers=auth_headers,
    )
    assert stale_read.status_code == 409
    assert stale_read.json()["detail"]["code"] == "release_context_changed"

    cross_release_decision = client.post(
        f"/api/v2/formal/ontologies/{ontology_id}/action-logs/pending-old/decide",
        headers=auth_headers,
        json={"decision": "rejected", "releaseId": release_id},
    )
    assert cross_release_decision.status_code == 409
    assert "跨版本审批已拒绝" in cross_release_decision.json()["detail"]
    db.expire_all()
    assert db.query(ActionExecutionLog).filter_by(id="pending-old").one().status == "pending"


def test_direct_action_uses_release_snapshot_instead_of_live_draft(
    client, auth_headers, ontology, db,
):
    ontology_id = ontology["id"]
    release_id = _seed_release_and_drift(db, ontology)

    response = client.post(
        f"/api/v2/formal/ontologies/{ontology_id}/run-action",
        headers=auth_headers,
        json={
            "actionId": "action-published",
            "targetInstanceId": "order-1",
            "releaseId": release_id,
        },
    )

    assert response.status_code == 200, response.text
    log = response.json()["data"]
    # The immutable action requires approval. Its live draft projection does
    # not; observing pending is proof the runtime resolved the release snapshot.
    assert log["status"] == "pending"
    assert log["pendingApproval"] is True
    assert log["actionName"] == "发布动作"
    assert log["ontologyReleaseId"] == release_id

    stale = client.post(
        f"/api/v2/formal/ontologies/{ontology_id}/run-action",
        headers=auth_headers,
        json={
            "actionId": "action-published",
            "targetInstanceId": "order-1",
            "releaseId": "stale-release",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "release_context_changed"


def test_current_release_history_includes_dynamic_overlay_but_not_draft_or_old_rows(
    client, auth_headers, ontology, db,
):
    ontology_id = ontology["id"]
    release_id = _seed_release_and_drift(db, ontology)
    db.add_all([
        Sentinel(
            id="sentinel-dynamic-current",
            ontology_id=ontology_id,
            name="dynamic_current",
            display_name="当前动态哨兵",
            bindings=[],
            links=[],
            action_ids=[],
            enabled=True,
            status="published",
            origin="assistant_dynamic",
            bound_release_id=release_id,
        ),
        SentinelFiring(
            id="firing-dynamic-current",
            ontology_id=ontology_id,
            sentinel_id="sentinel-dynamic-current",
            sentinel_name="当前动态哨兵",
            trigger_source="manual",
            status="fired",
            ontology_version="v0",
            ontology_release_id=release_id,
        ),
    ])
    db.commit()

    response = client.get(
        f"/api/v1/ontologies/{ontology_id}/sentinels/firings",
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()["data"]} == {
        "firing-current",
        "firing-dynamic-current",
    }


def test_notifications_default_to_current_release_with_explicit_history_escape_hatch(
    client, auth_headers, ontology, db,
):
    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    db.add_all([
        Notification(
            id="notification-current",
            ontology_id=ontology_id,
            channel="internal",
            status="delivered",
            ontology_release_id=release_id,
            sentinel_id="sentinel-current",
            action_log_id="action-log-current",
        ),
        Notification(
            id="notification-old",
            ontology_id=ontology_id,
            channel="internal",
            status="delivered",
            ontology_release_id="release-old",
            sentinel_id="sentinel-old",
            action_log_id="action-log-old",
        ),
    ])
    db.commit()

    current = client.get(
        f"/api/v1/ontologies/{ontology_id}/sentinels/notifications",
        headers=auth_headers,
    )
    history = client.get(
        f"/api/v1/ontologies/{ontology_id}/sentinels/notifications"
        "?include_history=true",
        headers=auth_headers,
    )

    assert [item["id"] for item in current.json()["data"]] == [
        "notification-current"]
    assert current.json()["data"][0]["ontologyReleaseId"] == release_id
    assert current.json()["data"][0]["sentinelId"] == "sentinel-current"
    assert current.json()["data"][0]["actionLogId"] == "action-log-current"
    assert {item["id"] for item in history.json()["data"]} == {
        "notification-current", "notification-old",
    }


def test_authenticated_cdc_status_defaults_current_and_can_explicitly_show_history(
    client, auth_headers, ontology, db, monkeypatch,
):
    from app.ontologies.sentinels import cdc

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    monkeypatch.setattr(cdc, "AUTO_DISPATCH", False)
    db.add_all([
        SentinelCdcOutbox(
            id="cdc-current",
            chain_id="chain-current",
            ontology_id=ontology_id,
            ontology_release_id=release_id,
            status="completed",
        ),
        SentinelCdcOutbox(
            id="cdc-old-dead",
            chain_id="chain-old",
            ontology_id=ontology_id,
            ontology_release_id="release-old",
            status="dead",
            attempts=4,
            last_error="historical detail",
        ),
    ])
    db.commit()

    current = client.get(
        f"/api/v1/ontologies/{ontology_id}/sentinels/cdc-status",
        headers=auth_headers,
    ).json()["data"]
    history = client.get(
        f"/api/v1/ontologies/{ontology_id}/sentinels/cdc-status"
        "?include_history=true",
        headers=auth_headers,
    ).json()["data"]

    assert current["scope"] == "current_release"
    assert current["ontology_release_id"] == release_id
    assert current["healthy"] is True
    assert current["durable"]["completed"] == 1
    assert current["durable"]["dead"] == 0
    assert history["scope"] == "history"
    assert history["healthy"] is False
    assert history["durable"]["dead"] == 1
    assert {item["eventId"] for item in history["recent_events"]} == {
        "cdc-current", "cdc-old-dead",
    }


def test_new_runtime_facts_inherit_current_release(ontology, db):
    facts = record_property_facts(
        db,
        ontology_id=ontology["id"],
        instance_id="runtime-subject",
        object_type_id="runtime-type",
        old_props=None,
        new_props={"status": "ready"},
        source="test",
    )
    db.commit()

    assert len(facts) == 1
    assert facts[0].ontology_version == "v0"
    assert facts[0].ontology_release_id == ontology["current_release_id"]
