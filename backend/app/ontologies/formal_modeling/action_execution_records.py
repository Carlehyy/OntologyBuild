"""Durable identity, lineage, release-fence, and audit records for Actions.

The effect interpreter and orchestration facade import these helpers through
``action_runtime_support`` for compatibility.  This module stays independent
of value evaluation and write-contract enforcement so audit/idempotency rules
can be reviewed without entering the transactional rule interpreter.
"""
from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.ontology_formal import (
    ActionExecutionLog,
    ActionType,
    PropertyFact,
)
from app.ontologies.formal_modeling.action_execution_context import (
    ActionDefinitionResolution,
)
from app.ontologies.formal_modeling.facts import fact_order_clause
from app.shared.time_utils import utc_iso


def _now():
    return datetime.now(timezone.utc)


def _idempotency_key(body) -> str | None:
    value = getattr(body, "idempotency_key", None)
    if value is None:
        return None
    return value.strip() if isinstance(value, str) else ""


def _match_state_id(body) -> str | None:
    value = getattr(body, "sentinel_match_state_id", None)
    return value if isinstance(value, str) and value else None


def _sentinel_id_from_execution_lineage(
        db: Session | None, ontology_id: str, body) -> str | None:
    """Resolve the Sentinel that owns an action from durable match lineage.

    HITL approval rebuilds the action request in a fresh transaction.  The
    durable ``sentinel_match_state_id`` survives that boundary while the
    evaluator-only ``sentinel_id`` hint does not, so notification provenance
    must be recovered from the match state rather than depending on the
    transient request shape.
    """
    state_id = _match_state_id(body)
    if db is not None and state_id is not None:
        from app.models.sentinel import SentinelMatchState

        row = db.query(SentinelMatchState.sentinel_id).filter(
            SentinelMatchState.id == state_id,
            SentinelMatchState.ontology_id == ontology_id,
        ).first()
        if row is not None and row[0]:
            return str(row[0])
    value = getattr(body, "sentinel_id", None)
    return value if isinstance(value, str) and value else None


def _normalize_target_snapshot(
        body, action: ActionType) -> tuple[dict | None, list[str]]:
    raw = getattr(body, "target_snapshot", None)
    if raw is None:
        return None, []
    if not isinstance(raw, dict):
        return None, ["target_snapshot 必须是对象"]
    allowed = {
        "id", "objectTypeId", "object_type_id", "properties", "computed",
    }
    unknown = sorted(set(raw) - allowed)
    errors = (
        [f"target_snapshot 包含未知字段: {', '.join(unknown)}"]
        if unknown else []
    )
    snapshot_id = str(raw.get("id") or "").strip()
    object_type_id = str(
        raw.get("objectTypeId") or raw.get("object_type_id") or "").strip()
    properties = raw.get("properties")
    computed = raw.get("computed", {})
    if not snapshot_id:
        errors.append("target_snapshot 缺少 id")
    if not object_type_id:
        errors.append("target_snapshot 缺少 objectTypeId")
    if not isinstance(properties, dict):
        errors.append("target_snapshot.properties 必须是对象")
    if not isinstance(computed, dict):
        errors.append("target_snapshot.computed 必须是对象")
    target_id = getattr(body, "target_instance_id", None)
    if snapshot_id and snapshot_id != target_id:
        errors.append("target_snapshot.id 与 target_instance_id 不一致")
    if action.object_type_id and object_type_id != action.object_type_id:
        errors.append(
            "target_snapshot.objectTypeId 与动作绑定对象类型不一致")
    if errors:
        return None, errors
    normalized = {
        "id": snapshot_id,
        "objectTypeId": object_type_id,
        "properties": deepcopy(properties),
        "computed": deepcopy(computed),
    }
    try:
        encoded = json.dumps(
            normalized, ensure_ascii=False, default=str).encode("utf-8")
    except (TypeError, ValueError):
        return None, ["target_snapshot 无法序列化为 JSON"]
    if len(encoded) > 1_000_000:
        return None, ["target_snapshot 超过 1000000 bytes 限制"]
    return normalized, []


def _is_executing_sentinel_approval(
        db: Session, owner: ActionExecutionLog) -> bool:
    """Whether ``owner`` is the durable checkpoint of an approved HITL run.

    ``executing`` alone is not enough: it is a generic runtime status and may
    also represent corrupt/legacy state.  The proposal must still be linked to
    one Sentinel match and carry the committed APPROVED decision fact written
    by the decision endpoint before technical execution starts.
    """
    if (
        owner.status != "executing"
        or owner.dry_run
        or not owner.sentinel_match_state_id
        or owner.decided_at is None
        or owner.related_log_id is not None
    ):
        return False
    fact = (
        db.query(PropertyFact)
        .filter(
            PropertyFact.ontology_id == owner.ontology_id,
            PropertyFact.instance_id == owner.id,
            PropertyFact.property_name == "decision",
            PropertyFact.kind == "decision",
        )
        .order_by(*fact_order_clause())
        .first()
    )
    wrapped = (
        fact.value.get("v")
        if fact and isinstance(fact.value, dict)
        else None
    )
    decision = (
        wrapped.get("decision")
        if isinstance(wrapped, dict)
        else wrapped
    )
    return str(decision or "").upper() == "APPROVED"


def _idempotent_replay(
        db: Session, ontology_id: str, key: str | None, *,
        same_request_is_sentinel_approval: bool = False,
) -> dict[str, Any] | None:
    """Return the durable owner or one truthful unresolved replay outcome.

    An approved HITL row owns the key while the actual execution is kept as a
    related audit log.  It is reusable only when that related execution really
    succeeded; an explicitly verified ``executing`` proposal remains pending,
    because approval by itself is not proof that downstream effects ran.
    """
    if not key:
        return None
    owner = db.query(ActionExecutionLog).filter(
        ActionExecutionLog.ontology_id == ontology_id,
        ActionExecutionLog.idempotency_key == key,
    ).first()
    if owner is None:
        return None
    if owner.status in ("success", "pending"):
        result = _log_to_dict(owner)
        result["idempotentReplay"] = True
        if owner.status == "pending":
            result["pendingApproval"] = True
        return result
    if (
        same_request_is_sentinel_approval
        and _is_executing_sentinel_approval(db, owner)
    ):
        # The human decision is durable, but the separately committed technical
        # execution/finalization is not complete yet.  A change caused by that
        # execution can race back through Sentinel CDC in this window.  Report
        # the original proposal as unresolved so the edge remains recoverable;
        # never present approval-in-progress as successful execution.
        result = _log_to_dict(owner)
        result["status"] = "pending"
        result["pendingApproval"] = True
        result["approvalExecuting"] = True
        result["approvalLogStatus"] = "executing"
        result["idempotentReplay"] = True
        return result
    if owner.status == "approved" and owner.related_log_id:
        related = db.query(ActionExecutionLog).filter(
            ActionExecutionLog.id == owner.related_log_id,
            ActionExecutionLog.ontology_id == ontology_id,
        ).first()
        if related is not None and related.status == "success":
            result = _log_to_dict(related)
            result["idempotentReplay"] = True
            result["approvalLogId"] = owner.id
            return result
    return {
        "status": "failed",
        "errorMessage": f"幂等键已被不可复用状态占用: {owner.status}",
        "actionId": owner.action_id,
        "parameters": owner.parameters or {},
        "effects": [],
        "validationErrors": ["idempotency_key_conflict"],
        "dryRun": bool(owner.dry_run),
        "executedAt": _now().isoformat(),
        "durationMs": 0,
        "idempotentReplay": True,
    }


def _same_idempotent_request(log: ActionExecutionLog, action: ActionType,
                             body, params: dict, ontology_version: str | None,
                             ontology_release_id: str | None,
                             target_snapshot: dict | None) -> bool:
    return (
        log.action_id == action.id
        and log.object_instance_id == body.target_instance_id
        and (log.parameters or {}) == params
        and bool(log.dry_run) == bool(body.dry_run)
        and log.sentinel_match_state_id == _match_state_id(body)
        and log.ontology_version == ontology_version
        and log.ontology_release_id == ontology_release_id
        and (log.target_snapshot or None) == target_snapshot
    )


def _idempotency_owner(db: Session, ontology_id: str,
                       key: str | None) -> ActionExecutionLog | None:
    if not key:
        return None
    return db.query(ActionExecutionLog).filter(
        ActionExecutionLog.ontology_id == ontology_id,
        ActionExecutionLog.idempotency_key == key,
    ).first()


def _rule_identity(rule: dict, ordinal: int) -> str:
    """Stable, non-secret identity used to scope one webhook delivery."""
    configured = str(rule.get("id") or "").strip()
    if configured:
        material = configured
    else:
        material = json.dumps(
            {
                "ordinal": ordinal,
                "type": rule.get("type"),
                "name": rule.get("name"),
                "order": rule.get("order", 0),
                "config": rule.get("config") or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _failed_effects(effects: list, *, dry_run: bool) -> list:
    """Make failed audit rows explicit about transactional rollback.

    Local effects are only durable after the surrounding commit.  Retaining a
    successful-looking "delivered/created" description after rollback is a
    dangerous false positive for operators.  A webhook response is different:
    the remote side cannot be rolled back, so expose that uncertainty instead
    of claiming either outcome.
    """
    if dry_run:
        return deepcopy(effects)
    normalized: list = []
    for raw in effects:
        item = deepcopy(raw)
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        if (
            item.get("type") == "webhook"
            and (
                item.get("statusCode") is not None
                or item.get("externalDeliveryMayHaveOccurred") is True
            )
        ):
            item["localTransactionCommitted"] = False
            item["externalDeliveryMayHaveOccurred"] = True
            item["description"] = (
                "Webhook 请求已发出且可能到达远端，但本地事务随后回滚；"
                "必须按 idempotencyKey 对账"
            )
        else:
            item["committed"] = False
            item["rolledBack"] = True
            item.pop("inputFactIds", None)
            derived_count = item.pop("derivedFactCount", None)
            if derived_count:
                item["rolledBackDerivedFactCount"] = derived_count
            if item.get("type") == "notification":
                item["status"] = "rolled_back"
                item["description"] = "站内通知已回滚（未投递）"
            else:
                item["description"] = (
                    f"{item.get('description') or item.get('type') or '副作用'}"
                    "（已回滚）"
                )
        normalized.append(item)
    return normalized


def _fail_log(db: Session, ontology_id: str, action: Optional[ActionType], body,
              start: float, message: str, effects: Optional[list] = None,
              validation_errors: Optional[list] = None,
              actor_id: Optional[str] = None,
              parameters: Optional[dict] = None,
              ontology_version: str | None = None,
              ontology_release_id: str | None = None,
              target_snapshot: dict | None = None,
              suppress_log: bool = False) -> dict:
    normalized_parameters = (
        (body.parameters or {}) if parameters is None else parameters)
    normalized_effects = _failed_effects(
        effects or [], dry_run=bool(body.dry_run))
    if suppress_log:
        return {
            "id": None,
            "actionId": body.action_id,
            "actionName": action.display_name if action else None,
            "objectTypeId": action.object_type_id if action else None,
            "objectInstanceId": body.target_instance_id,
            "parameters": normalized_parameters,
            "status": "failed",
            "validationErrors": validation_errors or [],
            "effects": normalized_effects,
            "errorMessage": message,
            "durationMs": int((time.time() - start) * 1000),
            "dryRun": bool(body.dry_run),
            "executedAt": _now().isoformat(),
            "actorId": actor_id,
            "targetSnapshot": target_snapshot,
            "idempotencyKey": None,
            "sentinelMatchStateId": _match_state_id(body),
            "ontologyVersion": ontology_version,
            "ontologyReleaseId": ontology_release_id,
            "previewOnly": True,
            "sideEffects": "none",
        }
    log = ActionExecutionLog(
        ontology_id=ontology_id,
        action_id=body.action_id,
        action_name=action.display_name if action else None,
        object_type_id=action.object_type_id if action else None,
        object_instance_id=body.target_instance_id,
        parameters=normalized_parameters, status="failed",
        validation_errors=validation_errors or [],
        effects=normalized_effects,
        error_message=message, duration_ms=int((time.time() - start) * 1000),
        dry_run=body.dry_run, actor_id=actor_id,
        # Failed attempts never own the key: retrying the same deterministic
        # sentinel step must be possible after the transaction was rolled back.
        idempotency_key=None,
        sentinel_match_state_id=_match_state_id(body),
        ontology_version=ontology_version,
        ontology_release_id=ontology_release_id,
        target_snapshot=target_snapshot,
    )
    db.add(log); db.commit(); db.refresh(log)
    return _log_to_dict(log)


def _current_release_error(
        db: Session,
        resolution: ActionDefinitionResolution,
        preview_context: Optional[dict],
) -> str | None:
    expected_release_id = resolution.expected_release_id
    project = resolution.project
    if expected_release_id is None or preview_context is not None:
        return None
    # Real executions hold the project row lock.  Read-only previews cannot
    # lock, so the second read below detects a promotion that raced the
    # preview and rejects its mixed observation.
    db.refresh(project, attribute_names=["current_release_id"])
    if str(project.current_release_id or "") != str(expected_release_id):
        return "动作执行期间当前发布节点发生变化"
    return None


def _log_to_dict(log: ActionExecutionLog) -> dict[str, Any]:
    return {
        "id": log.id, "actionId": log.action_id, "actionName": log.action_name,
        "objectTypeId": log.object_type_id, "objectInstanceId": log.object_instance_id,
        "parameters": log.parameters or {}, "status": log.status,
        "validationErrors": log.validation_errors or [], "effects": log.effects or [],
        "errorMessage": log.error_message, "durationMs": log.duration_ms,
        "dryRun": log.dry_run, "executedAt": utc_iso(log.executed_at),
        "actorId": log.actor_id,
        "decidedBy": log.decided_by,
        "decidedAt": utc_iso(log.decided_at),
        "decisionReason": log.decision_reason,
        "relatedLogId": log.related_log_id,
        "targetSnapshot": log.target_snapshot,
        "idempotencyKey": log.idempotency_key,
        "sentinelMatchStateId": log.sentinel_match_state_id,
        "ontologyVersion": log.ontology_version,
        "ontologyReleaseId": log.ontology_release_id,
    }
