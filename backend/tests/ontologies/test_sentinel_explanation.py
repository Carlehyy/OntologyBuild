"""哨兵触发解释（可解释推理）— explain_sentinel_firing。"""
from datetime import datetime, timezone

from app.models.ontology import OntologyProject
from app.models.ontology_formal import ObjectInstance, ObjectType
from app.models.ontology_version import OntologyVersion
from app.models.sentinel import Sentinel, SentinelFiring, SentinelMatchState
from app.ontologies.agent_runtime.boundary import (
    AgentScope,
    get_or_create_profile,
)
from app.ontologies.agent_runtime.toolkit import ToolRunner
from app.ontologies.sentinels.explanation_service import (
    explain_sentinel_firing,
    resolve_sentinel_definition,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dynamic_sentinel(ontology_id: str) -> Sentinel:
    return Sentinel(
        id="sentinel-dyn",
        ontology_id=ontology_id,
        name="overdue_alert",
        display_name="逾期告警",
        origin="assistant_dynamic",
        bindings=[
            {"alias": "a", "objectTypeId": "t-1",
             "filter": "a.amount > 0"},
            {"alias": "b", "objectTypeId": "t-2",
             "filter": "b.status == 'open'"},
        ],
        links=[{"from": "a", "linkTypeId": "lt-1", "to": "b"}],
        condition="a.amount > 100 and b.status == 'open'",
        condition_logic="and",
        primary_alias="a",
        action_ids=["act-1"],
        action_parameters={},
        trigger_mode="on_enter",
        enabled=True,
        muted=False,
        status="published",
    )


def _firing(db, ontology_id: str, *, status: str = "fired",
            matches: list | None = None,
            action_results: list | None = None,
            entered: list | None = None,
            left: list | None = None,
            error: str | None = None) -> SentinelFiring:
    if matches is None:
        matches = [{"a": "inst-1", "b": "inst-2"}]
    firing = SentinelFiring(
        ontology_id=ontology_id,
        sentinel_id="sentinel-dyn",
        sentinel_name="逾期告警",
        trigger_source="change",
        matches=matches,
        match_count=len(matches),
        entered=entered if entered is not None else ["inst-1"],
        left=left if left is not None else [],
        action_results=action_results or [],
        status=status,
        error=error,
        created_at=_now(),
    )
    db.add(firing)
    return firing


def _match_state(db, ontology_id: str, *, match_key: str,
                 detail: dict) -> SentinelMatchState:
    state = SentinelMatchState(
        ontology_id=ontology_id,
        sentinel_id="sentinel-dyn",
        match_key=match_key,
        match_detail=detail,
        runtime_status="completed",
        first_seen_at=_now(),
        last_seen_at=_now(),
    )
    db.add(state)
    return state


def _snapshot_detail(values: dict[str, dict], edge: str = "enter") -> dict:
    snapshots = {
        alias: {
            "id": spec["id"],
            "objectTypeId": spec.get("objectTypeId"),
            "properties": spec.get("properties") or {},
            "computed": spec.get("computed") or {},
            "externalId": spec.get("externalId"),
        }
        for alias, spec in values.items()
    }
    return {
        "a": "inst-1", "b": "inst-2",
        "__snapshots__": snapshots,
        "__event__": {"edge": edge, "matchKey": "a=inst-1|b=inst-2",
                      "occurredAt": _now().isoformat()},
    }


def test_resolve_dynamic_sentinel_definition(db, ontology):
    oid = ontology["id"]
    db.add(_dynamic_sentinel(oid))
    firing = _firing(db, oid)
    db.commit()

    definition = resolve_sentinel_definition(
        db, ontology_id=oid, sentinel_id="sentinel-dyn", firing=firing)
    assert definition is not None
    assert definition["origin"] == "assistant_dynamic"
    assert definition["condition"] == "a.amount > 100 and b.status == 'open'"
    assert len(definition["bindings"]) == 2


def test_resolve_builtin_definition_from_release_snapshot(db, ontology):
    oid = ontology["id"]
    release = OntologyVersion(
        id="rel-1", ontology_id=oid, version_number="v1",
        parent_version_id=None, base_release_id="rel-1",
        promoted_from_id=None, node_kind="release",
        lifecycle_status="released", revision=0,
        created_by=db.query(OntologyProject).filter_by(id=oid).one().created_by,
        snapshot_formal={
            "sentinels": [{
                "id": "sentinel-builtin",
                "name": "builtin_alert",
                "displayName": "内置告警",
                "bindings": [{"alias": "a", "objectTypeId": "t-1"}],
                "links": [],
                "condition": "a.amount > 10",
                "conditionLogic": "and",
                "primaryAlias": "a",
                "actionIds": [],
                "actionParameters": {},
                "triggerMode": "on_enter",
            }],
        },
    )
    db.add(release)
    firing = SentinelFiring(
        ontology_id=oid, sentinel_id="sentinel-builtin",
        sentinel_name="内置告警", trigger_source="change",
        matches=[{"a": "inst-1"}], match_count=1, entered=["inst-1"],
        left=[], action_results=[], status="fired",
        ontology_release_id="rel-1", created_at=_now(),
    )
    db.add(firing)
    db.commit()

    definition = resolve_sentinel_definition(
        db, ontology_id=oid, sentinel_id="sentinel-builtin", firing=firing)
    assert definition is not None
    assert definition["origin"] == "release_builtin"
    assert definition["condition"] == "a.amount > 10"


def test_explanation_with_condition_evidence(db, ontology):
    oid = ontology["id"]
    db.add(_dynamic_sentinel(oid))
    _firing(db, oid, matches=[{"a": "inst-1", "b": "inst-2"}],
            action_results=[{
                "actionId": "act-1", "targetInstanceId": "inst-1",
                "edge": "enter", "status": "success",
            }])
    _match_state(db, oid, match_key="a=inst-1|b=inst-2", detail=_snapshot_detail({
        "a": {"id": "inst-1", "objectTypeId": "t-1",
              "properties": {"amount": 200}, "externalId": "A-1"},
        "b": {"id": "inst-2", "objectTypeId": "t-2",
              "properties": {"status": "open"}, "externalId": "B-2"},
    }))
    db.commit()
    firing = db.query(SentinelFiring).one()
    definition = resolve_sentinel_definition(
        db, ontology_id=oid, sentinel_id="sentinel-dyn", firing=firing)

    result = explain_sentinel_firing(
        db, ontology_id=oid, definition=definition, firing=firing)
    assert result["sentinel"]["displayName"] == "逾期告警"
    assert result["statusMeaning"].startswith("条件命中")
    assert len(result["matchedTuples"]) == 1
    item = result["matchedTuples"][0]
    assert item["edge"] == "enter"
    assert item["condition"]["result"] is True
    reads = {
        (r["alias"], r["property"], r["value"])
        for r in item["condition"]["reads"]
    }
    assert ("a", "amount", 200) in reads
    assert ("b", "status", "open") in reads
    assert {c["alias"] for c in item["bindingFilters"]} == {"a", "b"}
    assert all(c["result"] is True for c in item["bindingFilters"])
    assert item["actions"][0]["status"] == "success"
    assert result["explanationLimits"]["snapshotMissingTuples"] == 0


def test_leave_edge_reports_condition_false(db, ontology):
    oid = ontology["id"]
    db.add(_dynamic_sentinel(oid))
    _firing(db, oid, matches=[{"a": "inst-1", "b": "inst-2"}],
            entered=[], left=["inst-1"])
    _match_state(db, oid, match_key="a=inst-1|b=inst-2",
                 detail=_snapshot_detail({
                     "a": {"id": "inst-1", "objectTypeId": "t-1",
                           "properties": {"amount": 50}, "externalId": "A-1"},
                     "b": {"id": "inst-2", "objectTypeId": "t-2",
                           "properties": {"status": "closed"},
                           "externalId": "B-2"},
                 }, edge="leave"))
    db.commit()
    firing = db.query(SentinelFiring).one()
    definition = resolve_sentinel_definition(
        db, ontology_id=oid, sentinel_id="sentinel-dyn", firing=firing)

    result = explain_sentinel_firing(
        db, ontology_id=oid, definition=definition, firing=firing)
    item = result["matchedTuples"][0]
    assert item["edge"] == "leave"
    assert item["condition"]["result"] is False


def test_missing_snapshot_is_honest(db, ontology):
    oid = ontology["id"]
    db.add(_dynamic_sentinel(oid))
    _firing(db, oid, matches=[{"a": "inst-1", "b": "inst-2"}])
    db.commit()
    firing = db.query(SentinelFiring).one()
    definition = resolve_sentinel_definition(
        db, ontology_id=oid, sentinel_id="sentinel-dyn", firing=firing)

    result = explain_sentinel_firing(
        db, ontology_id=oid, definition=definition, firing=firing)
    item = result["matchedTuples"][0]
    assert item["snapshotMissing"] is True
    assert item["condition"] is None
    assert result["explanationLimits"]["snapshotMissingTuples"] == 1


def test_no_match_firing_has_status_meaning(db, ontology):
    oid = ontology["id"]
    db.add(_dynamic_sentinel(oid))
    _firing(db, oid, matches=[], status="no_match")
    db.commit()
    firing = db.query(SentinelFiring).one()
    definition = resolve_sentinel_definition(
        db, ontology_id=oid, sentinel_id="sentinel-dyn", firing=firing)

    result = explain_sentinel_firing(
        db, ontology_id=oid, definition=definition, firing=firing)
    assert result["matchedTuples"] == []
    assert "没有任何对象满足" in result["statusMeaning"]
    assert result["explanationLimits"]["totalMatches"] == 0


def test_tool_runner_explain_sentinel_firing(db, ontology):
    oid = ontology["id"]
    project = db.query(OntologyProject).filter_by(id=oid).one()
    db.add(ObjectType(
        id="t-1", ontology_id=oid, name="Order",
        display_name="订单", primary_key="id",
        properties=[{"id": "id", "name": "id", "displayName": "编号",
                     "type": "string", "required": True}],
    ))
    db.add(ObjectInstance(
        id="inst-1", ontology_id=oid, object_type_id="t-1",
        properties={"id": "A-1", "amount": 200}, computed={},
        source="pipeline",
    ))
    db.add(Sentinel(
        id="sentinel-single", ontology_id=oid, name="amount_alert",
        display_name="金额告警", origin="assistant_dynamic",
        bindings=[{"alias": "a", "objectTypeId": "t-1",
                   "filter": "a.amount > 0"}],
        links=[], condition="a.amount > 100", condition_logic="and",
        primary_alias="a", action_ids=[], action_parameters={},
        trigger_mode="on_enter", enabled=True, muted=False,
        status="published",
    ))
    db.add(SentinelFiring(
        ontology_id=oid, sentinel_id="sentinel-single",
        sentinel_name="金额告警", trigger_source="change",
        matches=[{"a": "inst-1"}], match_count=1, entered=["inst-1"],
        left=[], action_results=[], status="fired", created_at=_now(),
    ))
    db.add(SentinelMatchState(
        ontology_id=oid, sentinel_id="sentinel-single",
        match_key="inst-1", runtime_status="completed",
        first_seen_at=_now(), last_seen_at=_now(),
        match_detail={
            "a": "inst-1",
            "__snapshots__": {
                "a": {"id": "inst-1", "objectTypeId": "t-1",
                      "properties": {"amount": 200},
                      "computed": {}, "externalId": "A-1"},
            },
            "__event__": {"edge": "enter", "matchKey": "inst-1",
                          "occurredAt": _now().isoformat()},
        },
    ))
    profile = get_or_create_profile(db, oid)
    db.commit()

    scope = AgentScope(db, project, profile)
    runner = ToolRunner(db, scope)
    result = runner.run(
        "explain_sentinel_firing",
        {"sentinel_id": "amount_alert"})

    assert result["sentinel"]["displayName"] == "金额告警"
    assert result["matchedTuples"][0]["condition"]["result"] is True
    assert any(c["instanceId"] == "inst-1" for c in runner.citations)
