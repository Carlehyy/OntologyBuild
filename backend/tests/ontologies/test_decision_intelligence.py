"""决策智能查询面 — 事实流因果链追溯（trace_causal_chain）。"""
from datetime import datetime, timezone

from app.models.ontology import OntologyProject
from app.models.ontology_formal import (
    ActionExecutionLog,
    ObjectInstance,
    ObjectType,
    PropertyFact,
)
from app.ontologies.agent_runtime.boundary import (
    AgentScope,
    get_or_create_profile,
)
from app.ontologies.agent_runtime.toolkit import ToolRunner
from app.ontologies.decision_intelligence.service import trace_causal_chain


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fact(
        db, *, ontology_id: str, fact_id: str, instance_id: str = "obj-1",
        property_name: str = "status", value="pending", kind: str = "property",
        source: str = "manual", caused_by: str | None = None,
        supersedes_id: str | None = None,
        derived_from: list[str] | None = None,
        release_id: str | None = None, seq: int = 1,
        actor_id: str | None = None) -> PropertyFact:
    fact = PropertyFact(
        id=fact_id,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        instance_id=instance_id,
        object_type_id="t-1",
        property_name=property_name,
        value={"v": value},
        kind=kind,
        source=source,
        actor_id=actor_id,
        caused_by=caused_by,
        supersedes_id=supersedes_id,
        derived_from=derived_from,
        seq=seq,
        recorded_at=_now(),
    )
    db.add(fact)
    return fact


def _log(
        db, *, ontology_id: str, log_id: str, status: str = "success",
        target_instance_id: str = "obj-1", decided_by: str | None = None,
        decision_reason: str | None = None,
        related_log_id: str | None = None) -> ActionExecutionLog:
    log = ActionExecutionLog(
        id=log_id,
        ontology_id=ontology_id,
        action_id="action-1",
        action_name="更新状态",
        object_instance_id=target_instance_id,
        status=status,
        decided_by=decided_by,
        decision_reason=decision_reason,
        related_log_id=related_log_id,
    )
    db.add(log)
    return log


def _decision(
        db, *, ontology_id: str, fact_id: str, log_id: str,
        decision: str = "APPROVED", reason: str | None = None) -> PropertyFact:
    return _fact(
        db, ontology_id=ontology_id, fact_id=fact_id, instance_id=log_id,
        property_name="decision", value={"decision": decision, "reason": reason},
        kind="decision", source="editor-save")


def _node_map(result: dict) -> dict[str, dict]:
    return {node["id"]: node for node in result["nodes"]}


def test_upstream_causal_chain_decision_action_fact(
        db, ontology):
    oid = ontology["id"]
    _fact(db, ontology_id=oid, fact_id="f-old", value="draft")
    _fact(
        db, ontology_id=oid, fact_id="f-new", value="paid",
        source="action://update-status", caused_by="log-1",
        supersedes_id="f-old", seq=2)
    _log(db, ontology_id=oid, log_id="log-1",
         decided_by="admin", decision_reason="客户已付款")
    _decision(db, ontology_id=oid, fact_id="d-1", log_id="log-1")
    db.commit()

    result = trace_causal_chain(
        db, ontology_id=oid, instance_id="obj-1",
        direction="upstream", max_depth=3)
    nodes = _node_map(result)
    edges = {(e["from"], e["to"], e["kind"]) for e in result["edges"]}

    assert nodes["f-new"]["value"] == "paid"
    assert nodes["f-old"]["value"] == "draft"
    assert nodes["log-1"]["kind"] == "action"
    assert nodes["log-1"]["decidedBy"] == "admin"
    assert "d-1" in nodes and nodes["d-1"]["kind"] == "decision"
    assert ("fact:f-new", "log:log-1", "caused_by") in edges
    assert ("fact:f-new", "fact:f-old", "supersedes") in edges
    assert ("log:log-1", "decision:d-1", "decided") in edges
    assert result["summary"]["decisionNodes"] == 1
    assert result["truncated"] is False


def test_downstream_effects_and_superseded_by(
        db, ontology):
    oid = ontology["id"]
    _fact(
        db, ontology_id=oid, fact_id="f-1", value="paid",
        source="action://update-status", caused_by="log-1")
    _fact(
        db, ontology_id=oid, fact_id="f-2", value="paid",
        source="action://update-status", caused_by="log-1",
        instance_id="obj-2")
    _fact(
        db, ontology_id=oid, fact_id="f-3", value="refunded",
        supersedes_id="f-1", seq=2)
    _log(db, ontology_id=oid, log_id="log-1")
    db.commit()

    result = trace_causal_chain(
        db, ontology_id=oid, instance_id="obj-1",
        direction="downstream", max_depth=2)
    edges = {(e["from"], e["to"], e["kind"]) for e in result["edges"]}

    assert ("log:log-1", "fact:f-1", "effect") in edges
    assert ("log:log-1", "fact:f-2", "effect") in edges
    assert ("fact:f-1", "fact:f-3", "superseded_by") in edges
    assert ("fact:f-1", "log:log-1", "caused_by") not in edges  # 上游方向未展开


def test_caused_by_pointing_to_another_fact(
        db, ontology):
    oid = ontology["id"]
    _fact(db, ontology_id=oid, fact_id="f-input", value="100")
    _fact(
        db, ontology_id=oid, fact_id="f-derived", value="200",
        kind="derived", caused_by="f-input")
    db.commit()

    result = trace_causal_chain(
        db, ontology_id=oid, instance_id="obj-1",
        direction="upstream", max_depth=2)
    edges = {(e["from"], e["to"], e["kind"]) for e in result["edges"]}
    assert ("fact:f-derived", "fact:f-input", "caused_by") in edges


def test_derived_from_inputs_are_traced(
        db, ontology):
    oid = ontology["id"]
    _fact(db, ontology_id=oid, fact_id="f-a", value="10", instance_id="obj-2")
    _fact(db, ontology_id=oid, fact_id="f-b", value="20", instance_id="obj-3")
    _fact(
        db, ontology_id=oid, fact_id="f-total", value="30", kind="derived",
        derived_from=["f-a", "f-b"])
    db.commit()

    result = trace_causal_chain(
        db, ontology_id=oid, instance_id="obj-1",
        direction="upstream", max_depth=2)
    edges = {(e["from"], e["to"], e["kind"]) for e in result["edges"]}
    assert ("fact:f-total", "fact:f-a", "derived_from") in edges
    assert ("fact:f-total", "fact:f-b", "derived_from") in edges


def test_depth_limit_stops_expansion(
        db, ontology):
    oid = ontology["id"]
    # 跨实例因果链：f1(obj-1) → log-1 → f2(obj-2) → f3(obj-2, supersedes f2)
    # → log-2 → f4(obj-3)。max_depth=2 到 f3 为止，f4 需 3 层才可达。
    _fact(db, ontology_id=oid, fact_id="f1", instance_id="obj-1",
          caused_by="log-1")
    _fact(db, ontology_id=oid, fact_id="f2", instance_id="obj-2",
          caused_by="log-1")
    _fact(db, ontology_id=oid, fact_id="f3", instance_id="obj-2",
          caused_by="log-2", supersedes_id="f2", seq=2)
    _fact(db, ontology_id=oid, fact_id="f4", instance_id="obj-3",
          caused_by="log-2")
    _log(db, ontology_id=oid, log_id="log-1")
    _log(db, ontology_id=oid, log_id="log-2")
    db.commit()

    shallow = trace_causal_chain(
        db, ontology_id=oid, instance_id="obj-1",
        direction="downstream", max_depth=2)
    shallow_nodes = _node_map(shallow)
    assert "f2" in shallow_nodes and "f3" in shallow_nodes
    assert "f4" not in shallow_nodes

    deep = trace_causal_chain(
        db, ontology_id=oid, instance_id="obj-1",
        direction="downstream", max_depth=3)
    assert "f4" in _node_map(deep)


def test_property_filter_only_traces_that_property(
        db, ontology):
    oid = ontology["id"]
    _fact(db, ontology_id=oid, fact_id="f-amount", property_name="amount",
          value=100)
    _fact(db, ontology_id=oid, fact_id="f-status", property_name="status",
          value="paid")
    db.commit()

    result = trace_causal_chain(
        db, ontology_id=oid, instance_id="obj-1",
        property_name="amount", direction="both", max_depth=2)
    nodes = _node_map(result)
    assert "f-amount" in nodes
    assert "f-status" not in nodes


def test_release_scope_excludes_out_of_authority_facts(
        db, ontology):
    oid = ontology["id"]
    _fact(db, ontology_id=oid, fact_id="f-in", release_id="rel-1")
    _fact(db, ontology_id=oid, fact_id="f-out", release_id="rel-2",
          supersedes_id=None)
    db.commit()

    result = trace_causal_chain(
        db, ontology_id=oid, instance_id="obj-1",
        authority_release_ids=["rel-1"], direction="both", max_depth=2)
    nodes = _node_map(result)
    assert "f-in" in nodes
    assert "f-out" not in nodes


def test_decision_facts_expose_decision_reason_and_count(
        db, ontology):
    oid = ontology["id"]
    # 决策事实直接挂在实例上（E2E 数据形态）：kind='decision'，值为字典
    _fact(
        db, ontology_id=oid, fact_id="d-seed", value={
            "decision": "APPROVED", "reason": "客户已付款"},
        kind="decision", source="user://admin",
        actor_id="actor-1")
    db.commit()

    result = trace_causal_chain(
        db, ontology_id=oid, instance_id="obj-1",
        direction="upstream", max_depth=2)
    nodes = _node_map(result)
    assert nodes["d-seed"]["factKind"] == "decision"
    assert nodes["d-seed"]["decision"] == "APPROVED"
    assert nodes["d-seed"]["reason"] == "客户已付款"
    assert result["summary"]["decisionNodes"] == 1
    assert result["summary"]["decisionOutcomes"][0]["decision"] == "APPROVED"


def test_fact_nodes_carry_causal_pointers(db, ontology):
    oid = ontology["id"]
    _fact(
        db, ontology_id=oid, fact_id="f-1", caused_by="log-1",
        supersedes_id="f-0", derived_from=["f-in"])
    _fact(db, ontology_id=oid, fact_id="f-0")
    db.commit()

    result = trace_causal_chain(
        db, ontology_id=oid, instance_id="obj-1",
        direction="upstream", max_depth=2)
    node = _node_map(result)["f-1"]
    assert node["causedBy"] == "log-1"
    assert node["supersedesId"] == "f-0"
    assert node["derivedFrom"] == ["f-in"]


def test_invalid_direction_raises(db, ontology):
    oid = ontology["id"]
    try:
        trace_causal_chain(
            db, ontology_id=oid, instance_id="obj-1", direction="sideways")
    except ValueError as exc:
        assert "direction" in str(exc)
    else:
        raise AssertionError("应当拒绝非法 direction")


def test_related_log_link_is_followed(db, ontology):
    oid = ontology["id"]
    # HITL：审批日志 log-pending 批准后产生执行日志 log-exec，两者互链
    _fact(db, ontology_id=oid, fact_id="f-1", caused_by="log-pending")
    _log(db, ontology_id=oid, log_id="log-pending", status="pending",
         decided_by="admin", related_log_id="log-exec")
    _log(db, ontology_id=oid, log_id="log-exec", status="success")
    db.commit()

    result = trace_causal_chain(
        db, ontology_id=oid, instance_id="obj-1",
        direction="upstream", max_depth=3)
    nodes = _node_map(result)
    edges = {(e["from"], e["to"], e["kind"]) for e in result["edges"]}
    assert ("fact:f-1", "log:log-pending", "caused_by") in edges
    assert ("log:log-pending", "log:log-exec", "related") in edges
    assert "log-exec" in nodes


def test_tool_runner_trace_causal_chain(db, ontology):
    oid = ontology["id"]
    project = db.query(OntologyProject).filter_by(id=oid).one()
    db.add(ObjectType(
        id="t-1", ontology_id=oid, name="Order",
        display_name="订单", primary_key="id",
        properties=[{"id": "id", "name": "id", "displayName": "编号",
                     "type": "string", "required": True}],
    ))
    db.add(ObjectInstance(
        id="obj-1", ontology_id=oid, object_type_id="t-1",
        properties={"id": "O-1", "status": "paid"}, computed={},
        source="pipeline",
    ))
    _fact(db, ontology_id=oid, fact_id="f-old", value="draft")
    _fact(
        db, ontology_id=oid, fact_id="f-new", value="paid",
        source="action://update-status", caused_by="log-1",
        supersedes_id="f-old", seq=2)
    _log(db, ontology_id=oid, log_id="log-1", decided_by="admin")
    _decision(db, ontology_id=oid, fact_id="d-1", log_id="log-1")
    profile = get_or_create_profile(db, oid)
    db.commit()

    scope = AgentScope(db, project, profile)
    runner = ToolRunner(db, scope)
    result = runner.run(
        "trace_causal_chain", {"instance_id": "obj-1"})

    assert result["instance"]  # 助手叙述用的实例标签
    assert result["summary"]["factNodes"] == 2
    assert result["summary"]["actionNodes"] == 1
    assert result["summary"]["decisionNodes"] == 1
    assert any(c["instanceId"] == "obj-1" for c in runner.citations)
