"""本体版本化路由 — 版本历史 / diff / 回滚"""
from __future__ import annotations

import ast
import json
import hashlib
import math
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, tuple_
from sqlalchemy.exc import IntegrityError
import uuid
from app.deps import get_db, get_current_user, require_admin
from app.config import settings
from app.models.ontology_version import (
    OntologyVersion, OntologyChangeLog, OntologyTrialRun,
    OntologyTrialObject, OntologyTrialLink,
)
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.models.relation import Relation
from app.models.logic import LogicRule
from app.models.action import Action
from app.models.inference import AuditLog
from app.models.ontology_formal import (
    ObjectType as FoObjectType, LinkType as FoLinkType,
    ActionType as FoActionType, OntologyFunction as FoFunction,
    ObjectInstance as FoObjectInstance, LinkInstance as FoLinkInstance,
    PropertyFact,
)
from app.models.sentinel import Sentinel, SentinelMatchState
from app.models.v2.mapping import OntologyMapping, OntologyLinkMapping
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.curated import CuratedReview
from app.data_channel.datasets.service import version_has_content
from app.ontologies.formal_modeling import schemas as FS
from app.ontologies.formal_modeling.validation import validate_model
from app.ontologies.sentinels.evaluator import (
    RESERVED_SENTINEL_ALIASES as _RESERVED_SENTINEL_ALIASES,
)
from app.ontologies.access import ontology_access_guard
from app.ontologies.versions.evolution_service import (
    complete_snapshot, impact_report, materialize_trial, next_draft_number,
    next_release_number, snapshot_hash, snapshot_models, validate_snapshot,
    validate_builtin_sentinel_contract,
    validate_expression_function_contract,
    validate_manual_mapping_trial_contract,
    validate_release_mapping_contract, validate_trial_mapping_contract,
    workspace_snapshot,
)

router = APIRouter(dependencies=[Depends(ontology_access_guard)])


def _version_payload(version: OntologyVersion, latest_trial: OntologyTrialRun | None = None) -> dict:
    return {
        "id": version.id,
        "version_number": version.version_number,
        "version_label": version.version_label,
        "description": version.description,
        "parent_version_id": version.parent_version_id,
        "base_release_id": version.base_release_id,
        "promoted_from_id": version.promoted_from_id,
        "node_kind": version.node_kind or "release",
        "lifecycle_status": version.lifecycle_status or (
            "released" if (version.node_kind or "release") == "release" else "editing"),
        "revision": version.revision or 0,
        "snapshot_hash": version.snapshot_hash,
        "change_summary": version.change_summary or {},
        "created_by": version.created_by,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "published_at": version.published_at.isoformat() if version.published_at else None,
        "latest_trial": _trial_payload(latest_trial) if latest_trial else None,
    }


def _trial_payload(run: OntologyTrialRun) -> dict:
    return {
        "id": run.id, "version_id": run.version_id, "revision": run.revision,
        "snapshot_hash": run.snapshot_hash, "status": run.status,
        "base_release_id": run.base_release_id,
        "dataset_versions": run.dataset_versions or [],
        "result": run.result_json or {}, "impact_hash": run.impact_hash,
        "created_by": run.created_by,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "lease_expires_at": (
            run.lease_expires_at.isoformat()
            if run.status == "running" and run.lease_expires_at else None
        ),
    }


def _gate_error(code: str, kind: str, message: str, *, item_id: str = "",
                name: str = "", field: str = "") -> dict:
    error = {
        "code": code, "kind": kind, "id": item_id,
        "name": name, "message": message,
    }
    if field:
        error["field"] = field
    return error


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive DateTime values for lease comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _trial_lease_deadline(now: datetime | None = None) -> datetime:
    seconds = max(int(settings.ontology_trial_lease_seconds), 1)
    return _as_utc(now or datetime.now(timezone.utc)) + timedelta(seconds=seconds)


def _terminal_trial_result(
        run: OntologyTrialRun, code: str, message: str, **context: Any) -> dict:
    error = _gate_error(
        code, "trialRun", message, item_id=run.id)
    error.update({
        key: value for key, value in context.items() if value is not None
    })
    return {
        "counts": {"objects": 0, "links": 0, "facts": 0, "datasets": 0},
        "errors": [error],
        "warnings": [],
        "samples": {"objects": [], "links": []},
        "actionsExecuted": 0,
        "sideEffects": "blocked",
    }


def _terminalize_running_trial(
        run: OntologyTrialRun, *, code: str, message: str,
        now: datetime | None = None, terminal_status: str = "stale",
        **context: Any,
) -> bool:
    """Release one active slot without allowing its old worker to write back."""
    if run.status != "running":
        return False
    completed_at = _as_utc(now or datetime.now(timezone.utc))
    run.status = terminal_status
    run.completed_at = completed_at
    run.claim_token = None
    run.lease_expires_at = None
    run.result_json = _terminal_trial_result(
        run, code, message, **context)
    return True


def _recover_expired_trial_runs(
        db: Session, ontology_id: str, version_id: str,
        *, now: datetime | None = None,
) -> list[OntologyTrialRun]:
    """Terminalize abandoned claims so retry and branch deletion cannot wedge."""
    checked_at = _as_utc(now or datetime.now(timezone.utc))
    runs = db.query(OntologyTrialRun).filter(
        OntologyTrialRun.ontology_id == ontology_id,
        OntologyTrialRun.version_id == version_id,
        OntologyTrialRun.status == "running",
    ).with_for_update().all()
    recovered: list[OntologyTrialRun] = []
    for run in runs:
        if not run.claim_token or run.lease_expires_at is None:
            if _terminalize_running_trial(
                    run,
                    code="trial_claim_invalid",
                    message="试跑运行记录缺少有效执行凭据，已安全终结；请重新试跑",
                    now=checked_at):
                recovered.append(run)
            continue
        expires_at = run.lease_expires_at
        if _as_utc(expires_at) <= checked_at and _terminalize_running_trial(
                run,
                code="trial_run_timeout",
                message="试跑执行租约已超时，原运行已安全终结；可以重新试跑",
                now=checked_at,
                expiredAt=_as_utc(expires_at).isoformat()):
            recovered.append(run)
    if recovered:
        db.flush()
    return recovered


def _active_trial_run(
        db: Session, ontology_id: str, version_id: str,
) -> OntologyTrialRun | None:
    return db.query(OntologyTrialRun).filter(
        OntologyTrialRun.ontology_id == ontology_id,
        OntologyTrialRun.version_id == version_id,
        OntologyTrialRun.status == "running",
    ).order_by(desc(OntologyTrialRun.created_at)).first()


def _raise_trial_already_running(run: OntologyTrialRun) -> None:
    raise HTTPException(409, detail={
        "code": "trial_already_running",
        "message": "该草稿已有权威试跑正在执行，请等待完成或租约回收",
        "trialRunId": run.id,
        "leaseExpiresAt": (
            run.lease_expires_at.isoformat()
            if run.lease_expires_at else None
        ),
    })


def _raise_publish_errors(errors: list[dict], message: str = "本体发布门禁未通过") -> None:
    if errors:
        raise HTTPException(422, detail={
            "code": "publish_validation_failed",
            "message": f"{message}（{len(errors)} 个错误）",
            "errors": errors,
        })


def _dynamic_sentinel_id_conflict_errors(
        db: Session, ontology_id: str, sentinels: Any) -> list[dict]:
    """Protect the global Sentinel PK without mixing the two management schemas."""
    if not isinstance(sentinels, list):
        return []
    builtin_by_id = {
        str(item.get("id")).strip(): item
        for item in sentinels
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and str(item.get("id")).strip()
    }
    if not builtin_by_id:
        return []
    conflicts = {
        str(item[0])
        for item in db.query(Sentinel.id).filter(
            Sentinel.ontology_id == ontology_id,
            Sentinel.origin == "assistant_dynamic",
            Sentinel.id.in_(set(builtin_by_id)),
        ).all()
    }
    return [
        _gate_error(
            "sentinel_id_conflicts_dynamic",
            "sentinel",
            (
                f"建模内置哨兵 ID「{sentinel_id}」已被本体助手动态哨兵占用；"
                "两类哨兵必须使用不同 ID"
            ),
            item_id=sentinel_id,
            name=str(
                builtin_by_id[sentinel_id].get("displayName")
                or builtin_by_id[sentinel_id].get("name")
                or sentinel_id
            ),
            field="id",
        )
        for sentinel_id in sorted(conflicts)
    ]


def _json_safe(value: Any) -> Any:
    """快照只保留 JSON 值；不把 ORM/时间对象渗入 JSON 列。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _with_canvas_layout(snapshot: dict | None, layout: dict | None) -> dict:
    """把独立画布布局投影到工作区 DTO，不改动被哈希的模型快照。"""
    out = complete_snapshot(snapshot)
    positions = layout if isinstance(layout, dict) else {}
    for object_type in out["objectTypes"]:
        position = positions.get(str(object_type.get("id")))
        if not isinstance(position, dict):
            continue
        if "x" in position and "y" in position:
            object_type["positionX"] = position["x"]
            object_type["positionY"] = position["y"]
    return out


def _canvas_node_ids(snapshot: dict | None) -> set[str]:
    """Return every stable node id accepted by the read-only structure canvas.

    Object type ids intentionally keep their historical, unprefixed form so the
    full-screen editor and the management detail page share the same positions.
    L2-only nodes use namespaced ids to avoid collisions across object
    properties and actions.  Functions and sentinels are analysis overlays, not
    persistent canvas nodes.
    """
    snap = complete_snapshot(snapshot)
    valid_ids: set[str] = set()
    for object_type in snap["objectTypes"]:
        object_type_id = str(object_type.get("id") or "")
        if not object_type_id:
            continue
        valid_ids.update({object_type_id, f"l1:{object_type_id}", f"l2:{object_type_id}"})
        for prop in object_type.get("properties") or []:
            if not isinstance(prop, dict):
                continue
            property_id = str(prop.get("id") or prop.get("name") or "")
            if property_id:
                node_id = f"property:{object_type_id}:{property_id}"
                valid_ids.update({node_id, f"l2:{node_id}"})
    for action in snap["actions"]:
        action_id = str(action.get("id") or "")
        if action_id:
            node_id = f"action:{action_id}"
            valid_ids.update({node_id, f"l2:{node_id}"})
    return valid_ids


def _validated_canvas_positions(raw: Any, valid_ids: set[str]) -> dict[str, dict[str, float]]:
    if not isinstance(raw, dict):
        raise HTTPException(422, detail={
            "code": "invalid_canvas_layout",
            "message": "positions 必须是节点 ID 到坐标的对象",
        })
    positions: dict[str, dict[str, float]] = {}
    for raw_id, raw_position in raw.items():
        node_id = str(raw_id)
        if node_id not in valid_ids:
            raise HTTPException(422, detail={
                "code": "invalid_canvas_layout",
                "message": f"节点 {node_id} 不属于该版本",
            })
        if not isinstance(raw_position, dict):
            raise HTTPException(422, detail={
                "code": "invalid_canvas_layout",
                "message": f"节点 {node_id} 的坐标格式无效",
            })
        x, y = raw_position.get("x"), raw_position.get("y")
        if isinstance(x, bool) or isinstance(y, bool):
            x = y = None
        try:
            x_value, y_value = float(x), float(y)
        except (TypeError, ValueError):
            x_value = y_value = math.nan
        if not math.isfinite(x_value) or not math.isfinite(y_value):
            raise HTTPException(422, detail={
                "code": "invalid_canvas_layout",
                "message": f"节点 {node_id} 的坐标必须是有限数字",
            })
        positions[node_id] = {"x": x_value, "y": y_value}
    return positions


def _action_has_usable_default(parameter: dict) -> bool:
    for key in ("defaultValue", "default_value", "default"):
        if key in parameter:
            return parameter[key] not in (None, "")
    return False


_SENTINEL_PARAMETER_TEMPLATE = re.compile(
    r"\{\{\s*(?P<alias>[^.\s{}]+)\.(?P<property>[^{}\s]+)\s*\}\}"
)
_SENTINEL_EVENT_PROPERTIES = frozenset({
    "edge", "matchKey", "occurredAt", "sentinelId", "sentinelName",
})


def _normal_sentinel_source_type(raw: Any) -> str:
    value = str(raw or "string").strip().lower()
    return {
        "float": "number", "double": "number",
        "integer": "number", "int": "number",
        "bool": "boolean",
        "list": "array", "object_set": "array",
        "dict": "object",
        "timestamp": "datetime",
    }.get(value, value)


def _normal_action_parameter_type(raw: Any) -> str:
    value = str(raw or "string").strip().lower()
    return {
        "float": "number", "double": "number",
        "int": "integer",
        "bool": "boolean",
        "list": "array", "object_set": "array",
        "dict": "object",
        "timestamp": "datetime",
    }.get(value, value)


def _sentinel_parameter_types_compatible(
        source_type: str, target_type: str) -> bool:
    source = _normal_sentinel_source_type(source_type)
    target = _normal_action_parameter_type(target_type)
    if target in {"any", "json"}:
        return True
    if source == target:
        return True
    # Both parameter kinds are represented by immutable string identifiers at
    # the Sentinel boundary.
    if source in {"string", "reference"} and target in {"string", "reference"}:
        return True
    return False


def _sentinel_expression_property_errors(
        expression: Any, alias_properties: dict[str, set[str]], *,
        sentinel_id: str, sentinel_name: str, field: str) -> list[dict]:
    """Validate direct property references against the immutable release schema."""
    raw = str(expression or "").strip().rstrip(";").strip()
    if not raw:
        return []
    try:
        tree = ast.parse(raw, mode="eval")
    except SyntaxError:
        # validate_safe_expression owns the canonical syntax error.
        return []

    missing: set[str] = set()
    dynamic: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            alias = node.value.id
            if alias in alias_properties and node.attr not in alias_properties[alias]:
                missing.add(f"{alias}.{node.attr}")
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            alias = node.value.id
            if alias not in alias_properties:
                continue
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                if key.value not in alias_properties[alias]:
                    missing.add(f"{alias}[{key.value!r}]")
            else:
                dynamic.add(alias)

    errors = [
        _gate_error(
            "sentinel_expression_property_not_found", "sentinel",
            f"哨兵「{sentinel_name}」表达式引用了发布版本中不存在的属性: {reference}",
            item_id=sentinel_id, name=sentinel_name, field=field,
        )
        for reference in sorted(missing)
    ]
    errors.extend(
        _gate_error(
            "sentinel_dynamic_property_forbidden", "sentinel",
            f"哨兵「{sentinel_name}」表达式不允许通过动态下标访问 {alias} 的属性",
            item_id=sentinel_id, name=sentinel_name, field=field,
        )
        for alias in sorted(dynamic)
    )
    return errors


def _validate_sentinels(sentinels: list[Sentinel], object_types: list[FoObjectType],
                        link_types: list[FoLinkType], actions: list[FoActionType]) -> list[dict]:
    """发布前验证 Sentinel 的所有静态引用和动作参数可供给性。"""
    errors: list[dict] = []
    object_by_id = {item.id: item for item in object_types}
    link_by_id = {item.id: item for item in link_types}
    action_by_id = {item.id: item for item in actions}

    for sentinel in sentinels:
        sid = sentinel.id or ""
        label = sentinel.display_name or sentinel.name or sid
        bindings = sentinel.bindings or []
        if not isinstance(bindings, list) or not bindings:
            errors.append(_gate_error(
                "sentinel_bindings_missing", "sentinel",
                f"哨兵「{label}」至少需要一个对象绑定",
                item_id=sid, name=label, field="bindings"))
            bindings = []

        aliases: dict[str, str] = {}
        for index, binding in enumerate(bindings):
            if not isinstance(binding, dict):
                errors.append(_gate_error(
                    "invalid_sentinel_binding", "sentinel",
                    f"哨兵「{label}」第 {index + 1} 个 binding 必须是对象",
                    item_id=sid, name=label, field=f"bindings[{index}]"))
                continue
            alias = str(binding.get("alias") or "").strip()
            object_type_id = str(binding.get("objectTypeId") or "").strip()
            if not alias:
                errors.append(_gate_error(
                    "sentinel_alias_missing", "sentinel",
                    f"哨兵「{label}」第 {index + 1} 个 binding 缺少 alias",
                    item_id=sid, name=label, field=f"bindings[{index}].alias"))
            elif alias in aliases:
                errors.append(_gate_error(
                    "duplicate_sentinel_alias", "sentinel",
                    f"哨兵「{label}」的 alias \"{alias}\" 重复",
                    item_id=sid, name=label, field=f"bindings[{index}].alias"))
            elif alias in _RESERVED_SENTINEL_ALIASES:
                errors.append(_gate_error(
                    "reserved_sentinel_alias", "sentinel",
                    f"哨兵「{label}」的 alias \"{alias}\" 是运行时保留名称",
                    item_id=sid, name=label, field=f"bindings[{index}].alias"))
            else:
                aliases[alias] = object_type_id
            if object_type_id not in object_by_id:
                errors.append(_gate_error(
                    "sentinel_object_type_not_found", "sentinel",
                    f"哨兵「{label}」binding \"{alias or index}\" 引用的对象类型不存在",
                    item_id=sid, name=label, field=f"bindings[{index}].objectTypeId"))
            binding_filter = binding.get("filter")
            if binding_filter:
                try:
                    from app.services.formal.safe_eval import validate_safe_expression
                    validate_safe_expression(
                        str(binding_filter), {alias, "obj"} if alias else {"obj"})
                except Exception as exc:
                    errors.append(_gate_error(
                        "invalid_sentinel_binding_filter", "sentinel",
                        f"哨兵「{label}」binding "
                        f"\"{alias or index}\" 的 filter 无法编译: {exc}",
                        item_id=sid, name=label,
                        field=f"bindings[{index}].filter"))
                if alias and alias not in _RESERVED_SENTINEL_ALIASES:
                    object_type = object_by_id.get(object_type_id)
                    property_names = {
                        str(item.get("name"))
                        for item in (
                            (object_type.properties or [])
                            if object_type is not None else []
                        )
                        if isinstance(item, dict) and item.get("name")
                    }
                    errors.extend(_sentinel_expression_property_errors(
                        binding_filter,
                        {alias: property_names, "obj": property_names},
                        sentinel_id=sid, sentinel_name=label,
                        field=f"bindings[{index}].filter",
                    ))

        primary_alias = str(sentinel.primary_alias or "").strip()
        if not primary_alias or primary_alias not in aliases:
            errors.append(_gate_error(
                "invalid_sentinel_primary_alias", "sentinel",
                f"哨兵「{label}」的 primaryAlias 必须指向已声明且唯一的 alias",
                item_id=sid, name=label, field="primaryAlias"))
        if sentinel.condition:
            try:
                from app.services.formal.safe_eval import validate_safe_expression
                validate_safe_expression(str(sentinel.condition), set(aliases))
            except Exception as exc:
                errors.append(_gate_error(
                    "invalid_sentinel_condition", "sentinel",
                    f"哨兵「{label}」的 condition 无法编译: {exc}",
                    item_id=sid, name=label, field="condition"))
            alias_properties = {}
            for alias, object_type_id in aliases.items():
                object_type = object_by_id.get(object_type_id)
                alias_properties[alias] = {
                    str(item.get("name"))
                    for item in (
                        (object_type.properties or [])
                        if object_type is not None else []
                    )
                    if isinstance(item, dict) and item.get("name")
                }
            errors.extend(_sentinel_expression_property_errors(
                sentinel.condition, alias_properties,
                sentinel_id=sid, sentinel_name=label, field="condition",
            ))

        links = sentinel.links or []
        if not isinstance(links, list):
            errors.append(_gate_error(
                "invalid_sentinel_links", "sentinel",
                f"哨兵「{label}」的 links 必须是数组",
                item_id=sid, name=label, field="links"))
            links = []
        for index, link in enumerate(links):
            if not isinstance(link, dict):
                errors.append(_gate_error(
                    "invalid_sentinel_link", "sentinel",
                    f"哨兵「{label}」第 {index + 1} 个 link 必须是对象",
                    item_id=sid, name=label, field=f"links[{index}]"))
                continue
            from_alias = str(link.get("from") or "").strip()
            to_alias = str(link.get("to") or "").strip()
            link_type_id = str(link.get("linkTypeId") or "").strip()
            if from_alias not in aliases or to_alias not in aliases:
                errors.append(_gate_error(
                    "sentinel_link_alias_not_found", "sentinel",
                    f"哨兵「{label}」的 link 端点必须引用已声明 alias",
                    item_id=sid, name=label, field=f"links[{index}]"))
            link_type = link_by_id.get(link_type_id)
            if link_type is None:
                errors.append(_gate_error(
                    "sentinel_link_type_not_found", "sentinel",
                    f"哨兵「{label}」引用的关系类型不存在: {link_type_id}",
                    item_id=sid, name=label, field=f"links[{index}].linkTypeId"))
            elif from_alias in aliases and to_alias in aliases and (
                    aliases[from_alias] != link_type.source_object_type_id
                    or aliases[to_alias] != link_type.target_object_type_id):
                errors.append(_gate_error(
                    "sentinel_link_endpoint_mismatch", "sentinel",
                    f"哨兵「{label}」的 link 端点类型与关系类型方向不匹配",
                    item_id=sid, name=label, field=f"links[{index}]"))

        action_ids = sentinel.action_ids or []
        if not isinstance(action_ids, list):
            errors.append(_gate_error(
                "invalid_sentinel_actions", "sentinel",
                f"哨兵「{label}」的 actionIds 必须是数组",
                item_id=sid, name=label, field="actionIds"))
            action_ids = []
        if len(action_ids) != len(set(str(aid) for aid in action_ids)):
            errors.append(_gate_error(
                "duplicate_sentinel_action", "sentinel",
                f"哨兵「{label}」的 actionIds 存在重复",
                item_id=sid, name=label, field="actionIds"))

        all_parameters = sentinel.action_parameters or {}
        if not isinstance(all_parameters, dict):
            errors.append(_gate_error(
                "invalid_sentinel_action_parameters", "sentinel",
                f"哨兵「{label}」的 actionParameters 必须是对象",
                item_id=sid, name=label, field="actionParameters"))
            all_parameters = {}
        declared_action_ids = {str(aid) for aid in action_ids}
        for configured_id in all_parameters:
            if str(configured_id) not in declared_action_ids:
                errors.append(_gate_error(
                    "orphan_sentinel_action_parameters", "sentinel",
                    f"哨兵「{label}」为未声明动作 {configured_id} 配置了参数",
                    item_id=sid, name=label, field=f"actionParameters.{configured_id}"))

        for index, raw_action_id in enumerate(action_ids):
            action_id = str(raw_action_id or "")
            action = action_by_id.get(action_id)
            if action is None:
                errors.append(_gate_error(
                    "sentinel_action_not_found", "sentinel",
                    f"哨兵「{label}」引用的动作不存在: {action_id}",
                    item_id=sid, name=label, field=f"actionIds[{index}]"))
                continue
            if (primary_alias in aliases and action.object_type_id
                    and action.object_type_id != aliases[primary_alias]):
                errors.append(_gate_error(
                    "sentinel_action_target_mismatch", "sentinel",
                    f"哨兵「{label}」的动作目标类型与 primaryAlias 类型不匹配",
                    item_id=sid, name=label, field=f"actionIds[{index}]"))
            if getattr(sentinel, "trigger_mode", None) == "on_enter_leave":
                from app.ontologies.formal_modeling.action_engine import (
                    action_supports_snapshot_execution,
                )
                if not action_supports_snapshot_execution(action):
                    errors.append(_gate_error(
                        "sentinel_leave_action_not_snapshot_safe", "sentinel",
                        f"哨兵「{label}」启用了离开触发，但动作"
                        f"「{action.display_name or action.name}」依赖实时对象或关系，"
                        "目标删除后无法仅凭命中快照执行",
                        item_id=sid, name=label,
                        field=f"actionIds[{index}]"))
            configured = all_parameters.get(action_id, {})
            if not isinstance(configured, dict):
                errors.append(_gate_error(
                    "invalid_sentinel_action_parameters", "sentinel",
                    f"哨兵「{label}」为动作「{action.display_name or action.name}」配置的参数必须是对象",
                    item_id=sid, name=label, field=f"actionParameters.{action_id}"))
                configured = {}
            declared_parameters = {
                str(parameter.get("name") or ""): parameter
                for parameter in (action.parameters or [])
                if isinstance(parameter, dict) and parameter.get("name")
            }
            for parameter_name, spec in configured.items():
                if parameter_name not in declared_parameters:
                    errors.append(_gate_error(
                        "sentinel_action_parameter_unknown", "sentinel",
                        f"哨兵「{label}」为动作「{action.display_name or action.name}」提供了未声明参数 \"{parameter_name}\"",
                        item_id=sid, name=label,
                        field=f"actionParameters.{action_id}.{parameter_name}"))
                    continue
                field = f"actionParameters.{action_id}.{parameter_name}"
                target_parameter = declared_parameters[parameter_name]

                def validate_required_property_supply(
                        property_definition: dict, source_label: str) -> None:
                    if (
                        not target_parameter.get("required")
                        or _action_has_usable_default(target_parameter)
                        or property_definition.get("required") is True
                    ):
                        return
                    errors.append(_gate_error(
                        "sentinel_required_parameter_optional_property",
                        "sentinel",
                        f"哨兵「{label}」将动作必填参数"
                        f"「{parameter_name}」仅绑定到可选属性"
                        f" {source_label}；真实对象缺字段时动作必然失败",
                        item_id=sid, name=label, field=field))

                def validate_binding_type(
                        source_type: str | None, source_label: str) -> None:
                    if not source_type or _sentinel_parameter_types_compatible(
                            source_type,
                            str(target_parameter.get("type") or "string")):
                        return
                    errors.append(_gate_error(
                        "sentinel_parameter_type_mismatch", "sentinel",
                        f"哨兵「{label}」参数「{parameter_name}」绑定的"
                        f"{source_label}类型为 {source_type}，与动作参数类型 "
                        f"{target_parameter.get('type') or 'string'} 不兼容",
                        item_id=sid, name=label, field=field))

                if isinstance(spec, str):
                    if "{{" not in spec and "}}" not in spec:
                        validate_binding_type("string", "字符串常量")
                        continue
                    matches = list(_SENTINEL_PARAMETER_TEMPLATE.finditer(spec))
                    remainder = _SENTINEL_PARAMETER_TEMPLATE.sub("", spec)
                    if not matches or "{{" in remainder or "}}" in remainder:
                        errors.append(_gate_error(
                            "invalid_sentinel_parameter_template", "sentinel",
                            f"哨兵「{label}」参数「{parameter_name}」模板格式非法: {spec}",
                            item_id=sid, name=label, field=field))
                        continue
                    full_match = _SENTINEL_PARAMETER_TEMPLATE.fullmatch(spec)
                    template_source_type = (
                        "string" if full_match is None else None)
                    template_source_label = (
                        "插值模板" if full_match is None else "模板来源")
                    for match in matches:
                        template_alias = match.group("alias")
                        prop = match.group("property")
                        if template_alias in {"event", "edge"}:
                            if full_match is not None:
                                template_source_type = "string"
                                template_source_label = f"事件属性 {prop}"
                            if prop not in _SENTINEL_EVENT_PROPERTIES:
                                errors.append(_gate_error(
                                    "sentinel_event_property_not_found", "sentinel",
                                    f"哨兵「{label}」参数「{parameter_name}」"
                                    f"引用了不受支持的事件属性: {prop}",
                                    item_id=sid, name=label, field=field))
                            continue
                        resolved_alias = (
                            primary_alias
                            if template_alias in {"primary", "target"}
                            else template_alias
                        )
                        if resolved_alias not in aliases:
                            errors.append(_gate_error(
                                "sentinel_parameter_alias_not_found", "sentinel",
                                f"哨兵「{label}」参数「{parameter_name}」"
                                f"模板引用的 alias 不存在: {template_alias}",
                                item_id=sid, name=label, field=field))
                            continue
                        if prop == "id":
                            if full_match is not None:
                                template_source_type = "string"
                                template_source_label = (
                                    f"实例标识 {resolved_alias}.id")
                            continue
                        object_type = object_by_id.get(aliases[resolved_alias])
                        property_definitions = {
                            str(item.get("name")): item
                            for item in (
                                (object_type.properties or [])
                                if object_type is not None else []
                            )
                            if isinstance(item, dict) and item.get("name")
                        }
                        if prop not in property_definitions:
                            errors.append(_gate_error(
                                "sentinel_parameter_property_not_found", "sentinel",
                                f"哨兵「{label}」参数「{parameter_name}」"
                                "模板引用的发布属性不存在: "
                                f"{resolved_alias}.{prop}",
                                item_id=sid, name=label, field=field))
                        else:
                            property_definition = property_definitions[prop]
                            validate_required_property_supply(
                                property_definition,
                                f"{resolved_alias}.{prop}")
                            if full_match is not None:
                                template_source_type = str(
                                    property_definition.get("type")
                                    or "string")
                                template_source_label = (
                                    f"属性 {resolved_alias}.{prop}")
                    validate_binding_type(
                        template_source_type, template_source_label)
                    continue
                if not isinstance(spec, dict):
                    continue  # scalar/list/object literal; runtime contract validates its type
                raw_source = spec.get("sourceType", spec.get("source"))
                if raw_source is None:
                    continue  # plain object literal
                source = str(raw_source).strip().lower().replace("-", "_")
                allowed_sources = {
                    "constant", "literal", "property", "match",
                    "match_property", "target_id", "primary_id",
                    "event", "event_property", "edge",
                }
                if source not in allowed_sources:
                    errors.append(_gate_error(
                        "invalid_sentinel_parameter_source", "sentinel",
                        f"哨兵「{label}」参数「{parameter_name}」的绑定来源 {raw_source!r} 不受支持",
                        item_id=sid, name=label, field=field))
                    continue
                if source in {"constant", "literal"}:
                    if "value" not in spec and "sourceValue" not in spec:
                        errors.append(_gate_error(
                            "sentinel_constant_value_missing", "sentinel",
                            f"哨兵「{label}」参数「{parameter_name}」的常量绑定缺少 value",
                            item_id=sid, name=label, field=field))
                    else:
                        from types import SimpleNamespace
                        from app.services.formal.action_engine import prepare_action_parameters
                        value = spec.get("value") if "value" in spec else spec.get("sourceValue")
                        _, value_errors = prepare_action_parameters(
                            SimpleNamespace(parameters=[declared_parameters[parameter_name]]),
                            {parameter_name: value})
                        for value_error in value_errors:
                            errors.append(_gate_error(
                                "sentinel_constant_parameter_invalid", "sentinel",
                                f"哨兵「{label}」常量参数「{parameter_name}」无效: {value_error}",
                                item_id=sid, name=label, field=field))
                    continue
                if source in {"event", "event_property", "edge"}:
                    prop = str(
                        spec.get("property", spec.get("sourceValue"))
                        or ("edge" if source == "edge" else "")
                    ).strip()
                    allowed_event_properties = {
                        "edge", "matchKey", "occurredAt",
                        "sentinelId", "sentinelName",
                    }
                    if not prop:
                        errors.append(_gate_error(
                            "sentinel_event_property_missing", "sentinel",
                            f"哨兵「{label}」参数「{parameter_name}」"
                            "的事件绑定缺少 property",
                            item_id=sid, name=label, field=field))
                    elif prop not in allowed_event_properties:
                        errors.append(_gate_error(
                            "sentinel_event_property_not_found", "sentinel",
                            f"哨兵「{label}」参数「{parameter_name}」"
                            f"引用了不受支持的事件属性: {prop}",
                            item_id=sid, name=label, field=field))
                    else:
                        validate_binding_type("string", f"事件属性 {prop}")
                    continue
                raw_alias = str(
                    spec.get("alias") or primary_alias or "").strip()
                alias = (
                    primary_alias
                    if raw_alias in {"primary", "target"}
                    else raw_alias
                )
                if alias not in aliases:
                    errors.append(_gate_error(
                        "sentinel_parameter_alias_not_found", "sentinel",
                        f"哨兵「{label}」参数「{parameter_name}」引用的 alias 不存在: {raw_alias}",
                        item_id=sid, name=label, field=field))
                    continue
                if source in {"target_id", "primary_id"}:
                    validate_binding_type("string", f"实例标识 {alias}.id")
                    continue
                if source in {"property", "match", "match_property"}:
                    prop = str(spec.get("property", spec.get("sourceValue")) or "").strip()
                    if not prop:
                        errors.append(_gate_error(
                            "sentinel_parameter_property_missing", "sentinel",
                            f"哨兵「{label}」参数「{parameter_name}」的属性绑定缺少 property",
                            item_id=sid, name=label, field=field))
                    elif prop == "id":
                        validate_binding_type(
                            "string", f"实例标识 {alias}.id")
                    else:
                        object_type = object_by_id.get(aliases[alias])
                        property_definitions = {
                            str(item.get("name")): item
                            for item in (
                                (object_type.properties or [])
                                if object_type else []
                            )
                            if isinstance(item, dict) and item.get("name")
                        }
                        if prop not in property_definitions:
                            errors.append(_gate_error(
                                "sentinel_parameter_property_not_found", "sentinel",
                                f"哨兵「{label}」参数「{parameter_name}」绑定的属性不存在: {alias}.{prop}",
                                item_id=sid, name=label, field=field))
                        else:
                            property_definition = property_definitions[prop]
                            validate_required_property_supply(
                                property_definition, f"{alias}.{prop}")
                            validate_binding_type(
                                str(property_definition.get("type")
                                    or "string"),
                                f"属性 {alias}.{prop}")
            for parameter in (action.parameters or []):
                if not isinstance(parameter, dict) or not parameter.get("required"):
                    continue
                parameter_name = str(parameter.get("name") or "").strip()
                if not parameter_name:
                    continue
                configured_value = configured.get(parameter_name)
                if (_action_has_usable_default(parameter)
                        or (parameter_name in configured and configured_value not in (None, ""))):
                    continue
                errors.append(_gate_error(
                    "sentinel_required_action_parameter_missing", "sentinel",
                    f"哨兵「{label}」未为动作「{action.display_name or action.name}」提供必填参数 \"{parameter_name}\"，且动作未声明默认值",
                    item_id=sid, name=label,
                    field=f"actionParameters.{action_id}.{parameter_name}"))
    return errors


def _validate_production_mappings(db: Session, ontology_id: str,
                                  mappings: list[OntologyMapping],
                                  link_mappings: list[OntologyLinkMapping],
                                  instances: list[FoObjectInstance],
                                  object_types: list[FoObjectType]) -> list[dict]:
    errors: list[dict] = []
    if instances and not mappings:
        errors.append(_gate_error(
            "production_mapping_required", "mapping",
            "生产本体已有实例，但没有任何 OntologyMapping；无法证明数据来源与审批版本"))

    object_by_id = {item.id: item for item in object_types}
    mapped_type_ids: set[str] = set()
    for mapping in mappings:
        if mapping.target_object_type_id:
            mapped_type_ids.add(mapping.target_object_type_id)
        for object_type in object_types:
            if mapping.entity_class in {object_type.name, object_type.display_name}:
                mapped_type_ids.add(object_type.id)
    for instance in instances:
        if instance.source != "pipeline" or not instance.external_id:
            errors.append(_gate_error(
                "instance_lake_lineage_missing", "objectInstance",
                f"实例 {instance.id} 缺少 pipeline source/external_id，无法证明来自资产湖",
                item_id=instance.id or "", field="source"))
        if instance.object_type_id not in mapped_type_ids:
            object_type = object_by_id.get(instance.object_type_id)
            errors.append(_gate_error(
                "instance_object_type_mapping_missing", "objectInstance",
                f"实例类型「{(object_type.display_name if object_type else instance.object_type_id)}」"
                "没有对应 OntologyMapping",
                item_id=instance.id or "", field="objectTypeId"))

    for mapping in mappings:
        mid = mapping.id or ""
        label = mapping.entity_class or mid
        if mapping.status != "applied":
            errors.append(_gate_error(
                "mapping_not_applied", "mapping",
                f"Mapping「{label}」状态必须为 applied，当前为 {mapping.status}",
                item_id=mid, name=label, field="status"))
        dataset_id = mapping.curated_dataset_id
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first() if dataset_id else None
        if dataset is None:
            errors.append(_gate_error(
                "mapping_dataset_not_found", "mapping",
                f"Mapping「{label}」绑定的数据集不存在: {dataset_id or ''}",
                item_id=mid, name=label, field="curatedDatasetId"))
            continue
        latest = db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == dataset.id,
        ).order_by(DatasetVersion.version_no.desc()).first()
        if latest is None:
            errors.append(_gate_error(
                "mapping_dataset_version_missing", "mapping",
                f"Mapping「{label}」绑定的数据集没有可发布版本",
                item_id=mid, name=label, field="curatedDatasetId"))
            continue
        if not version_has_content(latest) or not latest.checksum:
            errors.append(_gate_error(
                "mapping_dataset_version_unverifiable", "mapping",
                f"Mapping「{label}」的数据版本缺少数据载荷/checksum",
                item_id=mid, name=label, field="curatedDatasetId"))
        if dataset.latest_version_id != latest.id:
            errors.append(_gate_error(
                "dataset_latest_pointer_stale", "mapping",
                f"数据集「{dataset.name}」的 latest_version_id 未指向最新 v{latest.version_no}",
                item_id=mid, name=label, field="curatedDatasetId"))
        if dataset.kind == "curated":
            review = db.query(CuratedReview).filter(
                CuratedReview.curated_dataset_id == dataset.id,
                CuratedReview.dataset_version_id == latest.id,
            ).order_by(CuratedReview.created_at.desc()).first()
            if review is None or review.status != "approved":
                errors.append(_gate_error(
                    "latest_dataset_version_not_approved", "mapping",
                    f"Mapping「{label}」的最新数据版本 v{latest.version_no} 未获得当前 approved 审批",
                    item_id=mid, name=label, field="curatedDatasetId"))
        else:
            from app.data_channel.datasets.version_events import (
                manual_dataset_automation_eligibility,
            )
            eligible, reason = manual_dataset_automation_eligibility(
                dataset, latest)
            if not eligible:
                errors.append(_gate_error(
                    "mapping_manual_dataset_not_governed", "mapping",
                    f"Mapping「{label}」的人工数据版本不满足治理契约：{reason}",
                    item_id=mid, name=label, field="curatedDatasetId"))
            if not (mapping.field_mapping or {}).get("__auto_apply_on_version__"):
                errors.append(_gate_error(
                    "mapping_manual_automation_not_subscribed", "mapping",
                    f"Mapping「{label}」消费人工数据，发布前必须显式开启“版本后自动灌入”",
                    item_id=mid, name=label,
                    field="fieldMapping.__auto_apply_on_version__"))
        applied_version_id = (mapping.field_mapping or {}).get(
            "__applied_dataset_version_id__")
        if applied_version_id != latest.id:
            errors.append(_gate_error(
                "mapping_applied_version_stale", "mapping",
                f"Mapping「{label}」尚未应用最新数据版本 v{latest.version_no}",
                item_id=mid, name=label,
                field="fieldMapping.__applied_dataset_version_id__"))

    for link in link_mappings:
        lid = link.id or ""
        label = link.relation_type or lid
        if link.status not in {"active", "inferred"}:
            errors.append(_gate_error(
                "link_mapping_not_active", "linkMapping",
                f"LinkMapping「{label}」状态必须为 active/inferred，当前为 {link.status}",
                item_id=lid, name=label, field="status"))
        for role, dataset_id in (
            ("source", link.src_dataset_id),
            ("target", link.tgt_dataset_id),
            ("edge", link.edge_dataset_id),
        ):
            if not dataset_id:
                if role == "edge":
                    continue
                errors.append(_gate_error(
                    "link_mapping_dataset_missing", "linkMapping",
                    f"LinkMapping「{label}」缺少 {role} 数据集",
                    item_id=lid, name=label, field=f"{role}DatasetId"))
                continue
            dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
            if dataset is None:
                errors.append(_gate_error(
                    "link_mapping_dataset_not_found", "linkMapping",
                    f"LinkMapping「{label}」的 {role} 数据集不存在: {dataset_id}",
                    item_id=lid, name=label, field=f"{role}DatasetId"))
                continue
            latest = db.query(DatasetVersion).filter(
                DatasetVersion.dataset_id == dataset_id,
            ).order_by(DatasetVersion.version_no.desc()).first()
            if latest is None:
                errors.append(_gate_error(
                    "link_mapping_dataset_version_missing", "linkMapping",
                    f"LinkMapping「{label}」的 {role} 数据集没有版本",
                    item_id=lid, name=label, field=f"{role}DatasetId"))
                continue
            if not version_has_content(latest) or not latest.checksum:
                errors.append(_gate_error(
                    "link_mapping_version_unverifiable", "linkMapping",
                    f"LinkMapping「{label}」的 {role} 版本缺少数据载荷/checksum",
                    item_id=lid, name=label, field=f"{role}DatasetId"))
            if dataset.kind == "curated":
                review = db.query(CuratedReview).filter(
                    CuratedReview.curated_dataset_id == dataset_id,
                    CuratedReview.dataset_version_id == latest.id,
                ).order_by(CuratedReview.created_at.desc()).first()
                if review is None or review.status != "approved":
                    errors.append(_gate_error(
                        "link_mapping_version_not_approved", "linkMapping",
                        f"LinkMapping「{label}」的 {role} 最新版本 v{latest.version_no} 未审批",
                        item_id=lid, name=label, field=f"{role}DatasetId"))
            else:
                from app.data_channel.datasets.version_events import (
                    manual_dataset_automation_eligibility,
                )
                eligible, reason = manual_dataset_automation_eligibility(
                    dataset, latest)
                if not eligible:
                    errors.append(_gate_error(
                        "link_mapping_manual_dataset_not_governed", "linkMapping",
                        f"LinkMapping「{label}」的 {role} 人工数据不满足治理契约：{reason}",
                        item_id=lid, name=label, field=f"{role}DatasetId"))
                if not (link.field_mapping or {}).get("__auto_apply_on_version__"):
                    errors.append(_gate_error(
                        "link_mapping_manual_automation_not_subscribed", "linkMapping",
                        f"LinkMapping「{label}」消费人工数据，发布前必须显式开启版本自动对账",
                        item_id=lid, name=label,
                        field="fieldMapping.__auto_apply_on_version__"))
            if dataset.latest_version_id != latest.id:
                errors.append(_gate_error(
                    "link_mapping_latest_pointer_stale", "linkMapping",
                    f"LinkMapping「{label}」的 {role} 数据集 latest 指针过期",
                    item_id=lid, name=label, field=f"{role}DatasetId"))
            applied = (link.field_mapping or {}).get(
                f"__applied_{role}_version_id__")
            if applied != latest.id:
                errors.append(_gate_error(
                    "link_mapping_applied_version_stale", "linkMapping",
                    f"LinkMapping「{label}」尚未应用 {role} 最新版本 v{latest.version_no}",
                    item_id=lid, name=label,
                    field=f"fieldMapping.__applied_{role}_version_id__"))
    return errors


def _release_errors(db: Session, ontology_id: str) -> list[dict]:
    """发布和回滚共用的全量、fail-closed 契约。"""
    def q(model):
        return db.query(model).filter(model.ontology_id == ontology_id).all()

    object_types = q(FoObjectType)
    link_types = q(FoLinkType)
    actions = q(FoActionType)
    functions = q(FoFunction)
    instances = q(FoObjectInstance)
    link_instances = q(FoLinkInstance)
    errors = validate_model(
        object_types, link_types, actions, functions, instances, link_instances)
    errors.extend(validate_expression_function_contract(
        functions, object_types))
    from app.ontologies.formal_modeling.action_engine import (
        validate_action_definition,
    )
    for action in actions:
        for message in validate_action_definition(
                action, object_types, link_types, functions):
            errors.append(_gate_error(
                "invalid_action_definition", "action", message,
                item_id=action.id or "",
                name=action.display_name or action.name or action.id or "",
                field="rules"))
    if not object_types:
        errors.append(_gate_error(
            "object_type_required", "ontology",
            "发布本体至少需要一个 ObjectType"))
    for function in functions:
        if (bool(function.enabled)
                and str(function.language or "").strip().lower() == "typescript"):
            errors.append(_gate_error(
                "enabled_typescript_function_forbidden", "function",
                f"启用的 TypeScript 函数「{function.display_name or function.name}」不能进入发布版本",
                item_id=function.id or "", name=function.display_name or function.name,
                field="language"))

    sentinels = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.origin == "release_builtin",
    ).all()
    errors.extend(validate_builtin_sentinel_contract(
        [_snapshot_sentinel(item) for item in sentinels],
    ))
    errors.extend(_validate_sentinels(sentinels, object_types, link_types, actions))

    mappings = q(OntologyMapping)
    object_type_ids = {item.id for item in object_types}
    for mapping in mappings:
        if (mapping.target_object_type_id
                and mapping.target_object_type_id not in object_type_ids):
            errors.append(_gate_error(
                "mapping_object_type_not_found", "mapping",
                f"Mapping「{mapping.entity_class}」绑定的 ObjectType 不存在",
                item_id=mapping.id or "", name=mapping.entity_class,
                field="targetObjectTypeId"))
    if settings.environment == "production":
        errors.extend(_validate_production_mappings(
            db, ontology_id, mappings, q(OntologyLinkMapping),
            instances, object_types))
    return errors


def _rebuild_required_query_projections(db: Session, ontology_id: str) -> dict:
    """Reconcile non-transactional query stores from committed SQL truth."""
    from app.services.v2.mapping.mapping_service import MappingService
    service = MappingService(db)
    neo4j_ok = service._rebuild_neo4j_projection(ontology_id)
    chroma_count = service._rebuild_chroma_projection(ontology_id)
    return {
        "ready": bool(neo4j_ok and chroma_count is not None),
        "neo4j": "ok" if neo4j_ok else "error",
        "chroma": "ok" if chroma_count is not None else "error",
        "chroma_count": chroma_count or 0,
    }


# ============ 正规模型 (fo_*) 快照与差异 ============

def _snapshot_sentinel(item: Sentinel) -> dict:
    return _json_safe({
        "id": item.id,
        "name": item.name,
        "displayName": item.display_name,
        "description": item.description,
        "bindings": item.bindings or [],
        "links": item.links or [],
        "condition": item.condition,
        "conditionRows": item.condition_rows or [],
        "conditionLogic": item.condition_logic or "and",
        "primaryAlias": item.primary_alias,
        "actionIds": item.action_ids or [],
        "actionParameters": item.action_parameters or {},
        "onChange": bool(item.on_change),
        "onSchedule": bool(item.on_schedule),
        "scanIntervalSeconds": item.scan_interval_seconds,
        "triggerMode": item.trigger_mode,
        "muted": bool(item.muted),
        "enabled": bool(item.enabled),
        "status": item.status,
        "source": item.source,
    })


def _snapshot_mapping(item: OntologyMapping) -> dict:
    return _json_safe({
        "id": item.id,
        "curatedDatasetId": item.curated_dataset_id,
        "entityClass": item.entity_class,
        "fieldMapping": item.field_mapping or {},
        "targetObjectTypeId": item.target_object_type_id,
        "status": item.status,
        "confidence": item.confidence,
    })


def _snapshot_link_mapping(item: OntologyLinkMapping) -> dict:
    return _json_safe({
        "id": item.id,
        "srcDatasetId": item.src_dataset_id,
        "tgtDatasetId": item.tgt_dataset_id,
        "relationType": item.relation_type,
        "srcKey": item.src_key,
        "tgtKey": item.tgt_key,
        "status": item.status,
        "linkTypeId": item.link_type_id,
        "edgeDatasetId": item.edge_dataset_id,
        "fieldMapping": item.field_mapping or {},
    })

def _snapshot_formal(db: Session, ontology_id: str) -> dict:
    """把可发布的 Formal + Sentinel + Mapping 定义序列化为 JSON 快照。

    不含实例、哨兵命中状态和执行日志；这些属于运行/Facts 层。
    """
    def q(model):
        return db.query(model).filter(model.ontology_id == ontology_id).all()

    return {
        "objectTypes": [FS.ObjectTypeOut.model_validate(x).model_dump(mode="json", by_alias=True) for x in q(FoObjectType)],
        "linkTypes": [FS.LinkTypeOut.model_validate(x).model_dump(mode="json", by_alias=True) for x in q(FoLinkType)],
        "actions": [FS.ActionTypeOut.model_validate(x).model_dump(mode="json", by_alias=True) for x in q(FoActionType)],
        "functions": [FS.FunctionOut.model_validate(x).model_dump(mode="json", by_alias=True) for x in q(FoFunction)],
        "sentinels": [_snapshot_sentinel(x) for x in db.query(Sentinel).filter(
            Sentinel.ontology_id == ontology_id,
            Sentinel.origin == "release_builtin",
        ).all()],
        "mappings": [_snapshot_mapping(x) for x in q(OntologyMapping)],
        "linkMappings": [_snapshot_link_mapping(x) for x in q(OntologyLinkMapping)],
    }


def _diff_formal(prev: dict | None, curr: dict) -> dict:
    """按 id 对比两个正规模型快照，输出各集合的 added/modified/deleted 计数。"""
    prev = prev or {}
    out: dict = {}
    total_added = total_modified = total_deleted = 0
    for key in (
        "objectTypes", "linkTypes", "actions", "functions",
        "sentinels", "mappings", "linkMappings",
    ):
        prev_items = {i["id"]: i for i in (prev.get(key) or [])}
        curr_items = {i["id"]: i for i in (curr.get(key) or [])}
        added = len(curr_items.keys() - prev_items.keys())
        deleted = len(prev_items.keys() - curr_items.keys())
        modified = 0
        for iid in curr_items.keys() & prev_items.keys():
            a = {k: v for k, v in prev_items[iid].items() if k not in ("createdAt", "updatedAt")}
            b = {k: v for k, v in curr_items[iid].items() if k not in ("createdAt", "updatedAt")}
            if a != b:
                modified += 1
        out[key] = {"added": added, "modified": modified, "deleted": deleted}
        total_added += added; total_modified += modified; total_deleted += deleted
    out["total"] = {"added": total_added, "modified": total_modified, "deleted": total_deleted}
    return out


@router.get("/{ontology_id}/versions")
def list_versions(ontology_id: str, limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
    """列出所有版本（分页）"""
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).first()
    if not project:
        raise HTTPException(404, "Ontology not found")
    current = _current_release(db, project)
    total = db.query(OntologyVersion).filter(
        OntologyVersion.ontology_id == ontology_id
    ).count()
    versions = db.query(OntologyVersion).filter(
        OntologyVersion.ontology_id == ontology_id
    ).order_by(desc(OntologyVersion.created_at)).offset(offset).limit(limit).all()
    trial_by_version: dict[str, OntologyTrialRun] = {}
    if versions:
        for run in db.query(OntologyTrialRun).filter(
                OntologyTrialRun.version_id.in_([item.id for item in versions])
        ).order_by(desc(OntologyTrialRun.created_at)).all():
            trial_by_version.setdefault(run.version_id, run)
    db.commit()
    return {
        "data": [_version_payload(v, trial_by_version.get(v.id)) for v in versions],
        "total": total, "limit": limit, "offset": offset,
        "current_release_id": current.id,
        "current_release_version": current.version_number,
    }


def _current_release(db: Session, project: OntologyProject) -> OntologyVersion:
    current = None
    if project.current_release_id:
        current = db.query(OntologyVersion).filter(
            OntologyVersion.id == project.current_release_id,
            OntologyVersion.ontology_id == project.id,
            OntologyVersion.node_kind == "release",
            OntologyVersion.lifecycle_status == "released",
        ).first()
    if current is None:
        current = db.query(OntologyVersion).filter(
            OntologyVersion.ontology_id == project.id,
            OntologyVersion.node_kind == "release",
            OntologyVersion.lifecycle_status == "released",
        ).order_by(desc(OntologyVersion.published_at),
                   desc(OntologyVersion.created_at)).first()
    if current is None:
        # 存量安装首次访问时补齐完整基线；不猜增量历史，直接冻结当前定义。
        snap = complete_snapshot(_snapshot_formal(db, project.id))
        release_id = str(uuid.uuid4())
        current = OntologyVersion(
            id=release_id, ontology_id=project.id,
            version_number="v0", version_label="迁移基线",
            description="从升级前当前完整结构生成",
            base_release_id=release_id,
            node_kind="release", lifecycle_status="released", revision=0,
            snapshot_formal=snap, snapshot_hash=snapshot_hash(snap),
            published_at=datetime.now(timezone.utc), created_by=project.created_by,
        )
        db.add(current)
        db.flush()
    elif current.snapshot_formal is None:
        # 旧部署可能只有扁平版本元数据。当前运行结构仍可被可靠观察，首次
        # 访问时将其冻结成完整迁移基线；历史非当前节点则不能这样猜测。
        current.snapshot_formal = complete_snapshot(_snapshot_formal(db, project.id))
        current.snapshot_hash = snapshot_hash(current.snapshot_formal)
        current.published_at = current.published_at or current.created_at
        db.flush()
    elif not current.snapshot_hash:
        current.snapshot_formal = complete_snapshot(current.snapshot_formal)
        current.snapshot_hash = snapshot_hash(current.snapshot_formal)
        current.published_at = current.published_at or current.created_at
        db.flush()
    if project.current_release_id != current.id:
        project.current_release_id = current.id
        project.version = current.version_number
        db.flush()
    return current


def _next_release_activation_number(
        db: Session, ontology_id: str) -> str:
    """Allocate after every historic release, including legacy pointer reuse."""
    highest = 0
    for (number,) in db.query(OntologyVersion.version_number).filter(
            OntologyVersion.ontology_id == ontology_id,
            OntologyVersion.node_kind == "release",
    ).all():
        raw = str(number or "").removeprefix("v").split(".", 1)[0]
        if raw.isdigit():
            highest = max(highest, int(raw))
    return next_release_number(f"v{highest}")


def _workspace_mode(version: OntologyVersion) -> str:
    if version.node_kind == "release":
        return "release"
    if version.lifecycle_status == "trial_ready":
        return "trial"
    if version.lifecycle_status == "superseded":
        return "archived"
    return "draft"


def _workspace_payload(
        project: OntologyProject, version: OntologyVersion, *,
        is_current_release: bool = False,
        trial_run: OntologyTrialRun | None = None,
        trial_objects: list[OntologyTrialObject] | None = None,
        trial_links: list[OntologyTrialLink] | None = None) -> dict:
    """Serialize one immutable/versioned structure workspace.

    The management detail page must never infer a release from mutable Formal
    projection rows.  This payload is built only from the version snapshot
    selected by the project's authoritative ``current_release_id`` pointer.
    """
    snap = _with_canvas_layout(version.snapshot_formal, version.canvas_layout)
    workspace_mode = _workspace_mode(version)
    trial_created_at = (
        trial_run.created_at.isoformat()
        if trial_run is not None and trial_run.created_at else None)
    isolated_objects = [{
        "id": item.object_id,
        "objectTypeId": item.object_type_id,
        "properties": _json_safe(item.properties or {}),
        "computed": _json_safe(item.computed or {}),
        "source": "trial",
        "externalId": item.external_id,
        "createdAt": trial_created_at,
        "updatedAt": trial_created_at,
    } for item in (trial_objects or [])]
    isolated_links = [{
        "id": item.link_id,
        "linkTypeId": item.link_type_id,
        "sourceObjectId": item.source_object_id,
        "targetObjectId": item.target_object_id,
        "properties": _json_safe(item.properties or {}),
        "sourceRelationId": item.source_relation_id,
        "createdAt": trial_created_at,
    } for item in (trial_links or [])]
    return {
        "id": project.id, "name": project.name,
        "description": project.description, "version": version.version_number,
        "revision": f"{version.revision}:{version.snapshot_hash}",
        "objectTypes": snap["objectTypes"], "linkTypes": snap["linkTypes"],
        "actions": snap["actions"], "functions": snap["functions"],
        "mappings": snap["mappings"],
        "linkMappings": snap["linkMappings"],
        "sentinels": snap["sentinels"],
        "canvasLayout": _json_safe(version.canvas_layout or {}),
        # Trial data is read from its isolated tables only. Other version nodes
        # carry definitions without leaking the current production projection.
        "instances": isolated_objects,
        "linkInstances": isolated_links,
        "executionLogs": [],
        "trialRun": _trial_payload(trial_run) if trial_run else None,
        "workspaceMode": workspace_mode,
        "editable": workspace_mode == "draft",
        "versionId": version.id,
        "nodeKind": version.node_kind,
        "lifecycleStatus": version.lifecycle_status,
        "isCurrentRelease": is_current_release,
        "publishedAt": (
            version.published_at.isoformat() if version.published_at else None),
    }


def _mapping_workspace_payload(version: OntologyVersion, *,
                               is_current_release: bool = False) -> dict:
    snap = complete_snapshot(version.snapshot_formal)
    workspace_mode = _workspace_mode(version)
    return {
        "mappings": snap["mappings"],
        "linkMappings": snap["linkMappings"],
        "sentinels": snap["sentinels"],
        "revision": f"{version.revision}:{version.snapshot_hash}",
        "versionId": version.id,
        "versionNumber": version.version_number,
        "workspaceMode": workspace_mode,
        "editable": workspace_mode == "draft",
        "isCurrentRelease": is_current_release,
    }


@router.get("/{ontology_id}/current-release/workspace")
def get_current_release_workspace(
    ontology_id: str, db: Session = Depends(get_db),
):
    """Read the one authoritative published structure snapshot."""
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    release = _current_release(db, project)
    payload = _workspace_payload(project, release, is_current_release=True)
    db.commit()
    return {"data": payload}


@router.get("/{ontology_id}/current-release/mappings")
def get_current_release_mappings(
    ontology_id: str, db: Session = Depends(get_db),
):
    """Read mappings frozen into the authoritative published snapshot."""
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    release = _current_release(db, project)
    payload = _mapping_workspace_payload(release, is_current_release=True)
    db.commit()
    return {"data": payload}


@router.get("/{ontology_id}/version-tree")
def get_version_tree(ontology_id: str, db: Session = Depends(get_db)):
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    current = _current_release(db, project)
    versions = db.query(OntologyVersion).filter(
        OntologyVersion.ontology_id == ontology_id,
    ).order_by(OntologyVersion.created_at.asc()).all()
    latest_trials: dict[str, OntologyTrialRun] = {}
    for run in db.query(OntologyTrialRun).filter(
            OntologyTrialRun.ontology_id == ontology_id,
    ).order_by(desc(OntologyTrialRun.created_at)).all():
        latest_trials.setdefault(run.version_id, run)
    db.commit()
    return {"data": {
        "current_release_id": current.id,
        "current_release_number": current.version_number,
        "current_release_version": current.version_number,
        "versions": [_version_payload(item, latest_trials.get(item.id))
                     for item in versions],
    }}


@router.post("/{ontology_id}/versions/{source_version_id}/drafts", status_code=201)
def create_draft_version(
    ontology_id: str, source_version_id: str, body: dict,
    db: Session = Depends(get_db), current_user=Depends(get_current_user),
):
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).with_for_update().first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    current = _current_release(db, project)
    source = db.query(OntologyVersion).filter(
        OntologyVersion.id == source_version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).first()
    if source is None:
        raise HTTPException(404, "Source version not found")
    if source.snapshot_formal is None:
        raise HTTPException(409, detail={
            "code": "legacy_snapshot_incomplete",
            "message": "该历史版本缺少完整结构快照，不能安全创建分支",
        })
    recovery_mode = body.get("recovery_mode", body.get("recoveryMode"))
    if recovery_mode not in {None, "current_release_trial"}:
        raise HTTPException(422, detail={
            "code": "invalid_recovery_mode",
            "message": "不支持的历史恢复模式",
        })
    is_recovery = recovery_mode == "current_release_trial"
    if is_recovery:
        if source.node_kind != "release" or source.id == current.id:
            raise HTTPException(409, detail={
                "code": "recovery_requires_historical_release",
                "message": "安全恢复只能选择非当前的历史发布版本",
                "currentReleaseId": current.id,
            })
        expected_current = body.get(
            "expected_current_release_id",
            body.get("expectedCurrentReleaseId"),
        )
        if not expected_current:
            raise HTTPException(422, detail={
                "code": "recovery_current_release_required",
                "message": "创建恢复草稿前必须确认当前发布版本",
                "currentReleaseId": current.id,
            })
        if str(expected_current) != current.id:
            raise HTTPException(409, detail={
                "code": "recovery_base_changed",
                "message": "当前发布版本已变化，请刷新版本树后重新确认恢复",
                "expectedCurrentReleaseId": str(expected_current),
                "currentReleaseId": current.id,
            })
    sibling_numbers = [item.version_number for item in db.query(OntologyVersion).filter(
        OntologyVersion.ontology_id == ontology_id,
        OntologyVersion.parent_version_id == source.id,
    ).all()]
    # Version numbers are part of audit/provenance and must not be reused after
    # a branch is deleted.  Keep the deleted row out of the visible tree while
    # reserving its former number from the durable audit log.
    deleted_numbers = []
    for log in db.query(AuditLog).filter(
            AuditLog.ontology_id == ontology_id,
            AuditLog.event_subtype == "version_branch_deleted",
    ).all():
        before = log.before_state if isinstance(log.before_state, dict) else {}
        deleted_number = before.get("versionNumber")
        if isinstance(deleted_number, str):
            deleted_numbers.append(deleted_number)
    number = next_draft_number(
        source.version_number, [*sibling_numbers, *deleted_numbers])
    # 从任何状态分支时继承视觉布局，但仍与新草稿的模型快照分开保存，
    # 避免仅调整过位置就被版本差异误报为对象定义变更。
    snap = complete_snapshot(source.snapshot_formal)
    base_release_id = current.id if is_recovery else (
        source.id if source.node_kind == "release" else (
            source.base_release_id or current.id))
    draft = OntologyVersion(
        id=str(uuid.uuid4()), ontology_id=ontology_id,
        version_number=number,
        version_label=str(body.get("version_label") or body.get("versionLabel") or ""),
        description=str(body.get("description") or ""),
        parent_version_id=source.id, base_release_id=base_release_id,
        node_kind="draft", lifecycle_status="editing", revision=0,
        snapshot_formal=snap, snapshot_hash=snapshot_hash(snap),
        canvas_layout=_json_safe(source.canvas_layout or {}),
        snapshot_entities=_json_safe(source.snapshot_entities or []),
        snapshot_relations=_json_safe(source.snapshot_relations or []),
        snapshot_logic=_json_safe(source.snapshot_logic or []),
        snapshot_actions=_json_safe(source.snapshot_actions or []),
        change_summary=_diff_formal(
            current.snapshot_formal, snap), created_by=current_user.id,
    )
    db.add(draft)
    db.commit()
    return {"data": _version_payload(draft)}


@router.delete("/{ontology_id}/versions/{version_id}")
def delete_draft_version(
    ontology_id: str, version_id: str,
    db: Session = Depends(get_db), current_user=Depends(get_current_user),
):
    """Delete only an unpublished leaf branch.

    A version with descendants is part of the evolution tree's provenance and
    cannot be removed. Published and superseded nodes are immutable audit facts.
    """
    version = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).with_for_update().first()
    if version is None:
        raise HTTPException(404, "Version not found")
    if (version.node_kind != "draft"
            or version.lifecycle_status not in {"editing", "trial_ready"}):
        raise HTTPException(409, detail={
            "code": "version_delete_forbidden",
            "message": "只有未发布的草稿态或试跑态分支可以删除",
        })
    child = db.query(OntologyVersion).filter(
        OntologyVersion.ontology_id == ontology_id,
        OntologyVersion.parent_version_id == version.id,
    ).first()
    if child is not None:
        raise HTTPException(409, detail={
            "code": "version_not_leaf",
            "message": "该版本下仍有分支，只有叶子节点可以删除",
            "childVersionId": child.id,
            "childVersionNumber": child.version_number,
        })
    _recover_expired_trial_runs(db, ontology_id, version.id)
    running_trial = db.query(OntologyTrialRun).filter(
        OntologyTrialRun.version_id == version.id,
        OntologyTrialRun.status == "running",
    ).first()
    if running_trial is not None:
        raise HTTPException(409, detail={
            "code": "trial_running",
            "message": "该版本仍在试跑中，暂时不能删除",
            "trialRunId": running_trial.id,
            "leaseExpiresAt": (
                running_trial.lease_expires_at.isoformat()
                if running_trial.lease_expires_at else None
            ),
        })

    number = version.version_number
    trial_ids = [item.id for item in db.query(OntologyTrialRun.id).filter(
        OntologyTrialRun.version_id == version.id,
    ).all()]
    if trial_ids:
        # Delete explicitly as well as relying on ON DELETE CASCADE. This keeps
        # SQLite/dev environments (where FK cascades may be disabled) aligned
        # with PostgreSQL production semantics.
        db.query(OntologyTrialLink).filter(
            OntologyTrialLink.trial_run_id.in_(trial_ids),
        ).delete(synchronize_session=False)
        db.query(OntologyTrialObject).filter(
            OntologyTrialObject.trial_run_id.in_(trial_ids),
        ).delete(synchronize_session=False)
        db.query(OntologyTrialRun).filter(
            OntologyTrialRun.id.in_(trial_ids),
        ).delete(synchronize_session=False)
    # Change logs are historical records and remain queryable after branch
    # deletion, but their optional FK must no longer point at the removed node.
    db.query(OntologyChangeLog).filter(
        OntologyChangeLog.version_id == version.id,
    ).update({OntologyChangeLog.version_id: None}, synchronize_session=False)
    db.delete(version)
    db.add(AuditLog(
        id=str(uuid.uuid4()), ontology_id=ontology_id,
        event_type="edit", event_subtype="version_branch_deleted",
        user_id=current_user.id, user_name=current_user.username,
        description=f"删除叶子分支 {number}",
        object_type="ontology_version", object_id=version_id,
        before_state={"versionNumber": number}, after_state=None,
        meta={"lifecycleStatus": version.lifecycle_status},
    ))
    db.commit()
    return {"data": {"id": version_id, "version_number": number}}


def _draft_or_404(db: Session, ontology_id: str, version_id: str) -> OntologyVersion:
    draft = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).first()
    if draft is None:
        raise HTTPException(404, "Version not found")
    if draft.node_kind != "draft":
        raise HTTPException(409, detail={
            "code": "immutable_release", "message": "发布版本不可修改，请先创建草稿分支",
        })
    return draft


def _ensure_editable_draft(draft: OntologyVersion) -> None:
    """Enforce the lifecycle boundary: a successful trial is an immutable snapshot."""
    if draft.lifecycle_status == "trial_ready":
        raise HTTPException(409, detail={
            "code": "trial_snapshot_frozen",
            "message": "试跑态快照已冻结；如需继续修改，请从该版本创建新的草稿分支",
        })
    if draft.lifecycle_status != "editing":
        raise HTTPException(409, detail={
            "code": "archived_version_immutable",
            "message": "该版本已归档且不可修改；如需继续演化，请从当前发布版创建新的草稿分支",
        })


def _stale_previous_trials(db: Session, draft: OntologyVersion) -> None:
    # Editing invalidates an in-flight snapshot immediately.  Clearing its
    # claim releases the single-flight slot, while the old materializer is
    # fenced again at terminal write-back.
    now = datetime.now(timezone.utc)
    for run in db.query(OntologyTrialRun).filter(
            OntologyTrialRun.version_id == draft.id,
            OntologyTrialRun.status == "running",
    ).with_for_update().all():
        _terminalize_running_trial(
            run,
            code="trial_snapshot_changed",
            message="草稿在试跑执行期间发生修改，原试跑已失效",
            now=now,
            currentRevision=draft.revision,
            currentSnapshotHash=draft.snapshot_hash,
        )
    db.query(OntologyTrialRun).filter(
        OntologyTrialRun.version_id == draft.id,
        OntologyTrialRun.status == "passed",
    ).update({OntologyTrialRun.status: "stale"}, synchronize_session=False)


@router.get("/{ontology_id}/versions/{version_id}/workspace")
def get_version_workspace(
    ontology_id: str, version_id: str, db: Session = Depends(get_db),
):
    version = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).first()
    if version is None:
        raise HTTPException(404, "Version not found")
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    trial_run = None
    trial_objects: list[OntologyTrialObject] = []
    trial_links: list[OntologyTrialLink] = []
    if _workspace_mode(version) == "trial":
        trial_run = db.query(OntologyTrialRun).filter(
            OntologyTrialRun.ontology_id == ontology_id,
            OntologyTrialRun.version_id == version.id,
            OntologyTrialRun.status == "passed",
        ).order_by(desc(OntologyTrialRun.created_at)).first()
        if trial_run is not None:
            trial_objects = db.query(OntologyTrialObject).filter(
                OntologyTrialObject.trial_run_id == trial_run.id,
            ).order_by(OntologyTrialObject.object_type_id,
                       OntologyTrialObject.object_id).all()
            trial_links = db.query(OntologyTrialLink).filter(
                OntologyTrialLink.trial_run_id == trial_run.id,
            ).order_by(OntologyTrialLink.link_type_id,
                       OntologyTrialLink.link_id).all()
    return {"data": _workspace_payload(
        project, version,
        is_current_release=project.current_release_id == version.id,
        trial_run=trial_run, trial_objects=trial_objects,
        trial_links=trial_links)}


@router.put("/{ontology_id}/layout")
def save_canvas_layout(
    ontology_id: str, body: dict, db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """保存共享画布布局；不推进模型 revision，也不改变 snapshot_hash。"""
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id,
    ).with_for_update().first()
    if project is None:
        raise HTTPException(404, "Ontology not found")

    version_id = body.get("versionId", body.get("version_id"))
    if version_id:
        version = db.query(OntologyVersion).filter(
            OntologyVersion.id == str(version_id),
            OntologyVersion.ontology_id == ontology_id,
        ).with_for_update().first()
    else:
        version = _current_release(db, project)
    if version is None:
        raise HTTPException(404, "Version not found")

    snapshot = complete_snapshot(version.snapshot_formal)
    valid_ids = _canvas_node_ids(snapshot)
    updates = _validated_canvas_positions(body.get("positions"), valid_ids)
    current = version.canvas_layout if isinstance(version.canvas_layout, dict) else {}
    merged = {
        str(node_id): value for node_id, value in current.items()
        if str(node_id) in valid_ids and isinstance(value, dict)
    }
    merged.update(updates)
    version.canvas_layout = merged
    db.commit()
    return {"data": {
        "versionId": version.id,
        "positions": merged,
    }}


@router.put("/{ontology_id}/versions/{version_id}/workspace")
def save_draft_workspace(
    ontology_id: str, version_id: str, body: dict,
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    draft = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).with_for_update().first()
    if draft is None:
        raise HTTPException(404, "Version not found")
    if draft.node_kind != "draft":
        raise HTTPException(409, detail={"code": "immutable_release", "message": "发布版本不可修改"})
    _ensure_editable_draft(draft)
    expected = f"{draft.revision}:{draft.snapshot_hash}"
    base_revision = body.get("baseRevision", body.get("base_revision"))
    if base_revision is not None and str(base_revision) != expected:
        raise HTTPException(409, detail={
            "code": "conflict", "message": "该草稿已被其他会话修改，请重新加载",
            "currentRevision": expected,
        })
    try:
        candidate = workspace_snapshot(body, draft.snapshot_formal)
    except Exception as exc:
        raise HTTPException(422, detail={
            "code": "invalid_workspace", "message": str(exc),
        }) from exc
    errors = validate_snapshot(candidate, require_object_type=False)
    errors.extend(_dynamic_sentinel_id_conflict_errors(
        db, ontology_id, candidate.get("sentinels"),
    ))
    _raise_publish_errors(errors, "草稿结构校验未通过")
    draft.snapshot_formal = candidate
    valid_layout_ids = _canvas_node_ids(candidate)
    previous_layout = draft.canvas_layout if isinstance(draft.canvas_layout, dict) else {}
    next_layout = {
        str(node_id): value for node_id, value in previous_layout.items()
        if str(node_id) in valid_layout_ids and isinstance(value, dict)
    }
    # Object coordinates are still edited by the full-screen graph workspace.
    # Preserve the independent L2 property/action coordinates while refreshing
    # those shared object positions from the submitted model workspace.
    next_layout.update({
        str(item["id"]): {
            "x": float(item.get("positionX") or 0),
            "y": float(item.get("positionY") or 0),
        }
        for item in candidate["objectTypes"] if item.get("id")
    })
    draft.canvas_layout = next_layout
    draft.revision = (draft.revision or 0) + 1
    draft.snapshot_hash = snapshot_hash(candidate)
    draft.lifecycle_status = "editing"
    _stale_previous_trials(db, draft)
    db.commit()
    return {"data": {
        "revision": f"{draft.revision}:{draft.snapshot_hash}",
        "snapshotHash": draft.snapshot_hash,
    }}


@router.get("/{ontology_id}/versions/{version_id}/workspace/mappings")
def get_draft_mappings(
    ontology_id: str, version_id: str, db: Session = Depends(get_db),
):
    version = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).first()
    if version is None:
        raise HTTPException(404, "Version not found")
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).first()
    return {"data": _mapping_workspace_payload(
        version,
        is_current_release=bool(
            project and project.current_release_id == version.id),
    )}


_MAPPING_AUTOMATION_POLICY_KEYS = (
    "__auto_apply_on_review__",
    "__auto_apply_on_version__",
)


def _validate_workspace_mapping_policy_types(body: dict) -> None:
    """Automation flags in an immutable draft snapshot must be JSON booleans."""
    errors: list[dict] = []
    for collection, kind in (
        ("mappings", "mapping"),
        ("linkMappings", "linkMapping"),
    ):
        items = body.get(collection)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            field_mapping = item.get("fieldMapping")
            if not isinstance(field_mapping, dict):
                continue
            for key in _MAPPING_AUTOMATION_POLICY_KEYS:
                if key not in field_mapping:
                    continue
                value = field_mapping[key]
                # ``bool`` is a subclass of ``int`` in Python; exact type is
                # required so JSON 0/1 cannot silently become policy values.
                if type(value) is bool:
                    continue
                errors.append({
                    "kind": kind,
                    "index": index,
                    "id": str(item.get("id") or ""),
                    "field": f"fieldMapping.{key}",
                    "valueType": (
                        "null" if value is None else type(value).__name__
                    ),
                })
    if errors:
        raise HTTPException(422, detail={
            "code": "invalid_mapping_automation_policy_type",
            "message": (
                "映射自动触发策略必须使用 JSON true/false，"
                "不能使用字符串、数字或 null。"
            ),
            "errors": errors,
        })


@router.put("/{ontology_id}/versions/{version_id}/workspace/mappings")
def save_draft_mappings(
    ontology_id: str, version_id: str, body: dict,
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    draft = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).with_for_update().first()
    if draft is None:
        raise HTTPException(404, "Version not found")
    if draft.node_kind != "draft":
        raise HTTPException(409, detail={"code": "immutable_release", "message": "发布版本不可修改"})
    _ensure_editable_draft(draft)
    expected = f"{draft.revision}:{draft.snapshot_hash}"
    base_revision = body.get("baseRevision", body.get("base_revision"))
    if base_revision is not None and str(base_revision) != expected:
        raise HTTPException(409, detail={
            "code": "conflict", "message": "该草稿映射已被修改，请重新加载",
            "currentRevision": expected,
        })
    _validate_workspace_mapping_policy_types(body)
    snap = complete_snapshot(draft.snapshot_formal)
    for key in ("mappings", "linkMappings", "sentinels"):
        if key in body:
            if not isinstance(body[key], list):
                raise HTTPException(422, f"{key} must be an array")
            snap[key] = _json_safe(body[key])
    sentinel_errors = validate_builtin_sentinel_contract(snap["sentinels"])
    sentinel_errors.extend(_dynamic_sentinel_id_conflict_errors(
        db, ontology_id, snap["sentinels"],
    ))
    _raise_publish_errors(
        sentinel_errors,
        "建模内置哨兵字段校验未通过",
    )
    draft.snapshot_formal = snap
    draft.revision = (draft.revision or 0) + 1
    draft.snapshot_hash = snapshot_hash(snap)
    draft.lifecycle_status = "editing"
    _stale_previous_trials(db, draft)
    db.commit()
    return {"data": {
        "revision": f"{draft.revision}:{draft.snapshot_hash}",
        "snapshotHash": draft.snapshot_hash,
    }}


@router.get("/{ontology_id}/versions/{version_id}/impact")
def get_draft_impact(
    ontology_id: str, version_id: str, db: Session = Depends(get_db),
):
    draft = _draft_or_404(db, ontology_id, version_id)
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).first()
    current = _current_release(db, project)
    report = impact_report(current.snapshot_formal, draft.snapshot_formal)
    return {"data": {
        **report,
        "baseReleaseId": draft.base_release_id,
        "currentReleaseId": current.id,
        "baseOutdated": draft.base_release_id != current.id,
        "releaseReadiness": _release_readiness(
            db, draft=draft, current=current, report=report),
    }}


def _snapshot_sentinel_models(snapshot: dict) -> list[SimpleNamespace]:
    result = []
    for item in complete_snapshot(snapshot)["sentinels"]:
        result.append(SimpleNamespace(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or ""),
            display_name=str(item.get("displayName") or item.get("name") or ""),
            bindings=item.get("bindings") or [], links=item.get("links") or [],
            condition=item.get("condition"),
            primary_alias=item.get("primaryAlias"),
            action_ids=item.get("actionIds") or [],
            action_parameters=item.get("actionParameters") or {},
            trigger_mode=item.get("triggerMode") or "on_enter",
        ))
    return result


def _invalidate_dynamic_sentinels_for_release(
        db: Session, ontology_id: str, release_id: str) -> int:
    """Fail closed every assistant overlay still bound to another release.

    Release activation and dynamic-sentinel reconciliation must not have a
    lazy window in which an enabled definition validated against the previous
    schema can run on the newly activated projection.  Keep the old binding as
    provenance; the assistant service will explicitly revalidate/rebind it
    when an operator next reviews the definition against this release.
    """
    rows = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.origin == "assistant_dynamic",
        Sentinel.retired_at.is_(None),
    ).with_for_update().all()
    stale = [row for row in rows if row.bound_release_id != release_id]
    if not stale:
        return 0
    stale_ids = [row.id for row in stale]
    for row in stale:
        row.enabled = False
        row.last_trial_at = None
        row.last_trial_release_id = None
        row.last_trial_revision = None
        row.last_trial_report = None
    db.query(SentinelMatchState).filter(
        SentinelMatchState.ontology_id == ontology_id,
        SentinelMatchState.sentinel_id.in_(stale_ids),
    ).delete(synchronize_session=False)
    return len(stale)


def _trial_materialization_candidate(run: OntologyTrialRun) -> SimpleNamespace:
    """Use a detached result carrier so heavy work cannot update the claim row."""
    return SimpleNamespace(
        id=run.id,
        ontology_id=run.ontology_id,
        version_id=run.version_id,
        revision=run.revision,
        snapshot_hash=run.snapshot_hash,
        base_release_id=run.base_release_id,
        status="running",
        dataset_versions=[],
        result_json={},
        impact_hash=run.impact_hash,
        created_by=run.created_by,
        created_at=run.created_at,
        completed_at=None,
    )


def _trial_claim_lost_error(run_id: str) -> HTTPException:
    return HTTPException(409, detail={
        "code": "trial_claim_lost",
        "message": "试跑执行权已被回收或版本已删除，迟到结果未写入",
        "trialRunId": run_id,
    })


def _load_trial_after_claim_loss(
        db: Session, ontology_id: str, version_id: str, run_id: str,
) -> OntologyTrialRun:
    run = db.query(OntologyTrialRun).filter(
        OntologyTrialRun.id == run_id,
        OntologyTrialRun.ontology_id == ontology_id,
        OntologyTrialRun.version_id == version_id,
    ).first()
    if run is None or run.status == "running":
        raise _trial_claim_lost_error(run_id)
    return run


def _finalize_trial_candidate(
        db: Session, *, ontology_id: str, version_id: str,
        run_id: str, claim_token: str, candidate: SimpleNamespace,
) -> OntologyTrialRun:
    """CAS-like terminal write after reacquiring every authoritative row.

    Materialized child rows remain pending in the Session until these locks and
    invariants pass.  Any drift rolls the whole candidate projection back
    before the persisted run is marked stale.
    """
    now = datetime.now(timezone.utc)
    with db.no_autoflush:
        project = db.query(OntologyProject).filter(
            OntologyProject.id == ontology_id,
        ).populate_existing().with_for_update().first()
        draft = db.query(OntologyVersion).filter(
            OntologyVersion.id == version_id,
            OntologyVersion.ontology_id == ontology_id,
        ).populate_existing().with_for_update().first()
        claimed = db.query(OntologyTrialRun).filter(
            OntologyTrialRun.id == run_id,
            OntologyTrialRun.ontology_id == ontology_id,
            OntologyTrialRun.version_id == version_id,
        ).populate_existing().with_for_update().first()

    if claimed is None:
        db.rollback()
        raise _trial_claim_lost_error(run_id)
    if claimed.status != "running" or claimed.claim_token != claim_token:
        db.rollback()
        return _load_trial_after_claim_loss(
            db, ontology_id, version_id, run_id)

    conflicts: list[str] = []
    if project is None:
        conflicts.append("ontology_missing")
    if draft is None:
        conflicts.append("draft_missing")
    else:
        if draft.node_kind != "draft":
            conflicts.append("node_kind_changed")
        if draft.lifecycle_status != "editing":
            conflicts.append("lifecycle_changed")
        if (draft.revision or 0) != claimed.revision:
            conflicts.append("revision_changed")
        if draft.snapshot_hash != claimed.snapshot_hash:
            conflicts.append("snapshot_hash_changed")
        try:
            formal_hash = snapshot_hash(complete_snapshot(
                draft.snapshot_formal))
        except Exception:
            formal_hash = None
        if formal_hash != claimed.snapshot_hash:
            conflicts.append("snapshot_content_changed")
        if draft.base_release_id != claimed.base_release_id:
            conflicts.append("draft_base_changed")
    if (
        project is not None
        and project.current_release_id != claimed.base_release_id
    ):
        conflicts.append("current_release_changed")
    if not claimed.base_release_id:
        conflicts.append("trial_base_missing")
    if (
        claimed.lease_expires_at is None
        or _as_utc(claimed.lease_expires_at) <= _as_utc(now)
    ):
        conflicts.append("lease_expired")

    if conflicts:
        # Drop every pending trial object/link before updating the durable
        # running row.  This also releases the project/draft/run locks.
        db.rollback()
        claimed = db.query(OntologyTrialRun).filter(
            OntologyTrialRun.id == run_id,
            OntologyTrialRun.ontology_id == ontology_id,
            OntologyTrialRun.version_id == version_id,
        ).with_for_update().first()
        if claimed is None:
            raise _trial_claim_lost_error(run_id)
        if claimed.status != "running" or claimed.claim_token != claim_token:
            db.rollback()
            return _load_trial_after_claim_loss(
                db, ontology_id, version_id, run_id)
        _terminalize_running_trial(
            claimed,
            code="trial_completion_conflict",
            message="试跑完成时草稿或发布基线已变化，迟到结果未写入",
            now=now,
            conflicts=conflicts,
        )
        db.commit()
        return claimed

    if candidate.status not in {"passed", "failed"}:
        db.rollback()
        raise RuntimeError("trial materializer did not produce a terminal status")

    claimed.dataset_versions = candidate.dataset_versions or []
    claimed.result_json = candidate.result_json or {}
    claimed.status = candidate.status
    claimed.completed_at = candidate.completed_at or now
    claimed.claim_token = None
    claimed.lease_expires_at = None
    if candidate.status == "passed":
        draft.lifecycle_status = "trial_ready"
    db.commit()
    return claimed


@router.get("/{ontology_id}/versions/{version_id}/trial-runs")
def list_trial_runs(
    ontology_id: str, version_id: str, db: Session = Depends(get_db),
):
    version = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).first()
    if version is None:
        raise HTTPException(404, "Version not found")
    if _recover_expired_trial_runs(db, ontology_id, version_id):
        db.commit()
    runs = db.query(OntologyTrialRun).filter(
        OntologyTrialRun.ontology_id == ontology_id,
        OntologyTrialRun.version_id == version_id,
    ).order_by(desc(OntologyTrialRun.created_at)).all()
    return {"data": [_trial_payload(item) for item in runs]}


@router.get("/{ontology_id}/versions/{version_id}/trial-runs/{run_id}")
def get_trial_run(
    ontology_id: str, version_id: str, run_id: str,
    db: Session = Depends(get_db),
):
    if _recover_expired_trial_runs(db, ontology_id, version_id):
        db.commit()
    run = db.query(OntologyTrialRun).filter(
        OntologyTrialRun.id == run_id,
        OntologyTrialRun.ontology_id == ontology_id,
        OntologyTrialRun.version_id == version_id,
    ).first()
    if run is None:
        raise HTTPException(404, "Trial run not found")
    return {"data": _trial_payload(run)}


@router.post("/{ontology_id}/versions/{version_id}/trial-runs", status_code=201)
def create_trial_run(
    ontology_id: str, version_id: str, body: dict,
    db: Session = Depends(get_db), current_user=Depends(get_current_user),
):
    # Match promotion's project→draft lock order.  PostgreSQL serializes starts
    # on the project/draft rows; the partial unique index remains the final
    # cross-dialect guard for databases where FOR UPDATE is weaker.
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id,
    ).with_for_update().first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    draft = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).with_for_update().first()
    if draft is None:
        raise HTTPException(404, "Version not found")
    if draft.node_kind != "draft":
        raise HTTPException(409, detail={
            "code": "trial_requires_draft", "message": "只有草稿分支可以试跑",
        })
    _recover_expired_trial_runs(db, ontology_id, version_id)
    active = _active_trial_run(db, ontology_id, version_id)
    if active is not None:
        _raise_trial_already_running(active)
    _ensure_editable_draft(draft)
    current = _current_release(db, project)
    if draft.base_release_id != current.id:
        raise HTTPException(409, detail={
            "code": "draft_base_outdated",
            "message": "当前发布版已变化，请从最新发布版创建草稿并合并改动后再试跑",
            "draftBaseReleaseId": draft.base_release_id,
            "currentReleaseId": current.id,
        })
    snap = complete_snapshot(draft.snapshot_formal)
    structural_errors = validate_snapshot(snap)
    structural_errors.extend(_dynamic_sentinel_id_conflict_errors(
        db, ontology_id, snap["sentinels"],
    ))
    structural_errors.extend(validate_trial_mapping_contract(snap))
    try:
        models = snapshot_models(snap)
        structural_errors.extend(_validate_sentinels(
            _snapshot_sentinel_models(snap), models["objectTypes"],
            models["linkTypes"], models["actions"],
        ))
    except Exception as exc:
        structural_errors.append(_gate_error(
            "sentinel_validation_failed", "sentinel", str(exc)))
    _raise_publish_errors(structural_errors, "试跑前发布就绪校验未通过")

    report = impact_report(current.snapshot_formal, snap)
    claim_token = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    run = OntologyTrialRun(
        id=run_id, ontology_id=ontology_id, version_id=draft.id,
        revision=draft.revision or 0,
        snapshot_hash=draft.snapshot_hash or snapshot_hash(snap),
        base_release_id=current.id,
        claim_token=claim_token,
        lease_expires_at=_trial_lease_deadline(),
        status="running", dataset_versions=[], result_json={},
        impact_hash=report["impactHash"], created_by=current_user.id,
    )
    db.add(run)
    # running 记录先落盘；进程中断后不会伪装成“从未试跑”。
    try:
        db.flush()
        candidate = _trial_materialization_candidate(run)
        db.commit()
    except IntegrityError:
        db.rollback()
        competing = _active_trial_run(db, ontology_id, version_id)
        if competing is not None:
            _raise_trial_already_running(competing)
        raise

    try:
        materialize_trial(db, candidate, snap)
    except Exception as exc:
        # Discard any partially-added trial objects/links.  The detached
        # candidate then follows the same locked terminal-write path as a
        # normal materialization result.
        db.rollback()
        candidate.status = "failed"
        candidate.dataset_versions = []
        candidate.completed_at = datetime.now(timezone.utc)
        candidate.result_json = _terminal_trial_result(
            candidate,
            "trial_internal_error",
            f"试跑事务已回滚: {exc}",
        )

    try:
        run = _finalize_trial_candidate(
            db,
            ontology_id=ontology_id,
            version_id=version_id,
            run_id=run_id,
            claim_token=claim_token,
            candidate=candidate,
        )
    except HTTPException:
        raise
    except Exception as exc:
        # A flush/commit failure must not leave the claim running forever.
        db.rollback()
        candidate.status = "failed"
        candidate.dataset_versions = []
        candidate.completed_at = datetime.now(timezone.utc)
        candidate.result_json = _terminal_trial_result(
            candidate,
            "trial_internal_error",
            f"试跑终态写入已回滚: {exc}",
        )
        run = _finalize_trial_candidate(
            db,
            ontology_id=ontology_id,
            version_id=version_id,
            run_id=run_id,
            claim_token=claim_token,
            candidate=candidate,
        )
    return {"data": _trial_payload(run)}


def _verify_trial_dataset_pins(db: Session, run: OntologyTrialRun) -> list[dict]:
    errors = []
    for pin in run.dataset_versions or []:
        dataset = db.query(Dataset).filter(
            Dataset.id == pin.get("datasetId")).first()
        version = db.query(DatasetVersion).filter(
            DatasetVersion.id == pin.get("versionId"),
            DatasetVersion.dataset_id == pin.get("datasetId"),
        ).first()
        if dataset is None or version is None:
            errors.append(_gate_error(
                "trial_dataset_version_missing", "dataset",
                "试跑固定的数据版本已不存在",
                item_id=str(pin.get("datasetId") or "")))
            continue
        if dataset.latest_version_id != version.id:
            errors.append(_gate_error(
                "trial_dataset_version_stale", "dataset",
                f"数据集「{dataset.name}」在试跑后已产生新版本，请从该试跑版本创建新草稿后重新试跑",
                item_id=dataset.id))
        if version.checksum != pin.get("checksum"):
            errors.append(_gate_error(
                "trial_dataset_checksum_changed", "dataset",
                f"数据集「{dataset.name}」固定版本校验和变化，拒绝发布",
                item_id=dataset.id))
    return errors


_RUNTIME_STATE_CONFLICT_LIMIT = 50
_RUNTIME_FACT_QUERY_CHUNK = 300
_RUNTIME_FACT_QUERY_POSTGRES_CHUNK = 5000
_RUNTIME_STATE_MASK = "••••••（已隐藏）"
_RUNTIME_STATE_SENSITIVE_FIELD = re.compile(
    r"(?:password|passwd|pwd|secret|token|api[\s_-]?key|authorization|"
    r"credential|cookie|session|private[\s_-]?key|client[\s_-]?secret|"
    r"signature)",
    re.IGNORECASE,
)
_RUNTIME_STATE_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_RUNTIME_STATE_ACCESS_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"Bearer\s+[A-Za-z0-9._~+/=-]{12,})\b",
    re.IGNORECASE,
)
_RUNTIME_STATE_INLINE_SECRET = re.compile(
    r"(\b(?:password|passwd|pwd|secret|token|api[\s_-]?key|"
    r"authorization|credential)\b\s*[:=]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;，；]+)",
    re.IGNORECASE,
)


def _empty_runtime_state_conflicts() -> dict:
    return {
        "totalCount": 0,
        "propertyConflictCount": 0,
        "objectConflictCount": 0,
        "linkConflictCount": 0,
        "itemLimit": _RUNTIME_STATE_CONFLICT_LIMIT,
        "truncated": False,
        "items": [],
    }


def _redact_runtime_state_value(
    value: Any, field_name: str = "", depth: int = 0,
) -> Any:
    """Bound and redact conflict values before they leave the backend."""
    if _RUNTIME_STATE_SENSITIVE_FIELD.search(str(field_name or "")):
        return _RUNTIME_STATE_MASK
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        redacted = _RUNTIME_STATE_JWT.sub("[令牌已隐藏]", value)
        redacted = _RUNTIME_STATE_ACCESS_TOKEN.sub("[凭据已隐藏]", redacted)
        redacted = _RUNTIME_STATE_INLINE_SECRET.sub(
            r"\1[凭据已隐藏]", redacted)
        return (
            redacted
            if len(redacted) <= 500
            else f"{redacted[:500]}…（已截断）"
        )
    if depth >= 4:
        return "[内容已折叠]"
    if isinstance(value, list):
        visible = [
            _redact_runtime_state_value(item, "", depth + 1)
            for item in value[:20]
        ]
        if len(value) > 20:
            visible.append(f"其余 {len(value) - 20} 项已折叠")
        return visible
    if isinstance(value, dict):
        entries = list(value.items())
        result = {
            str(key): _redact_runtime_state_value(
                item, str(key), depth + 1)
            for key, item in entries[:40]
        }
        if len(entries) > 40:
            result["…"] = f"其余 {len(entries) - 40} 个字段已折叠"
        return result
    return str(value)[:500]


def _is_lake_projection_fact_source(source: str | None) -> bool:
    """Return whether a property fact is a normal lake/release projection write.

    Publication is allowed to replace data-lake snapshots.  Every other source
    is runtime business state (actions, users/manual edits, collectors/imports,
    and future writers) and therefore fails closed instead of being guessed
    into an overwrite/retain policy.
    """
    value = str(source or "").strip().lower()
    return (
        value in {"pipeline", "pipeline-reconcile"}
        or value.startswith("pipeline://")
        or value.startswith("ontology-release://")
        or value.startswith("mapping://")
        or value.startswith("link-mapping://")
    )


def _safe_runtime_fact_source(source: str | None) -> str:
    """Keep useful provenance without leaking a user identifier in impact UI."""
    value = str(source or "unknown")
    if value.lower().startswith("user://"):
        return "user://[redacted]"
    return str(_redact_runtime_state_value(value))


def _runtime_fact_chunks(
    items: list[Any], chunk_size: int = _RUNTIME_FACT_QUERY_CHUNK,
) -> list[list[Any]]:
    return [
        items[index:index + chunk_size]
        for index in range(0, len(items), chunk_size)
    ]


def _runtime_fact_query_chunk_size(db: Session) -> int:
    # PostgreSQL safely supports a much larger bind budget than SQLite.  Keep
    # SQLite conservative for tests/embedded deployments while avoiding
    # thousands of round trips for a large production lake.
    dialect = str(getattr(db.get_bind().dialect, "name", "")).lower()
    return (
        _RUNTIME_FACT_QUERY_POSTGRES_CHUNK
        if dialect == "postgresql"
        else _RUNTIME_FACT_QUERY_CHUNK
    )


def _runtime_coordinate_facts(
    db: Session, *, ontology_id: str, release_ids: list[str],
    kind: str, coordinates: list[tuple[str, str]],
) -> list[PropertyFact]:
    """Return one canonical latest Fact per release/coordinate.

    The SQL window bounds materialization to
    ``len(release_ids) * len(coordinates)`` instead of loading the append-only
    history for every differing value into Python.
    """
    facts: list[PropertyFact] = []
    for chunk in _runtime_fact_chunks(
        coordinates, _runtime_fact_query_chunk_size(db),
    ):
        ranked = db.query(
            PropertyFact.id.label("fact_id"),
            func.row_number().over(
                partition_by=(
                    PropertyFact.ontology_release_id,
                    PropertyFact.instance_id,
                    PropertyFact.property_name,
                ),
                order_by=(
                    PropertyFact.recorded_at.desc(),
                    PropertyFact.seq.desc(),
                    PropertyFact.id.desc(),
                ),
            ).label("runtime_rank"),
        ).filter(
            PropertyFact.ontology_id == ontology_id,
            PropertyFact.ontology_release_id.in_(release_ids),
            PropertyFact.kind == kind,
            tuple_(
                PropertyFact.instance_id,
                PropertyFact.property_name,
            ).in_(chunk),
        ).subquery()
        facts.extend(db.query(PropertyFact).join(
            ranked, PropertyFact.id == ranked.c.fact_id,
        ).filter(ranked.c.runtime_rank == 1).all())
    return facts


def _runtime_existence_facts(
    db: Session, *, ontology_id: str, release_ids: list[str],
    kind: str, instance_ids: list[str],
) -> list[PropertyFact]:
    """Return one canonical latest existence Fact per release/instance."""
    facts: list[PropertyFact] = []
    for chunk in _runtime_fact_chunks(
        instance_ids, _runtime_fact_query_chunk_size(db),
    ):
        ranked = db.query(
            PropertyFact.id.label("fact_id"),
            func.row_number().over(
                partition_by=(
                    PropertyFact.ontology_release_id,
                    PropertyFact.instance_id,
                    PropertyFact.property_name,
                ),
                order_by=(
                    PropertyFact.recorded_at.desc(),
                    PropertyFact.seq.desc(),
                    PropertyFact.id.desc(),
                ),
            ).label("runtime_rank"),
        ).filter(
            PropertyFact.ontology_id == ontology_id,
            PropertyFact.ontology_release_id.in_(release_ids),
            PropertyFact.kind == kind,
            PropertyFact.property_name == "exists",
            PropertyFact.instance_id.in_(chunk),
        ).subquery()
        facts.extend(db.query(PropertyFact).join(
            ranked, PropertyFact.id == ranked.c.fact_id,
        ).filter(ranked.c.runtime_rank == 1).all())
    return facts


def _runtime_latest_by_scope(
    facts: list[PropertyFact], *, current_release_id: str,
    ancestor_release_ids: list[str],
) -> tuple[dict[tuple[str, str], PropertyFact],
           dict[tuple[str, str], PropertyFact]]:
    current: dict[tuple[str, str], PropertyFact] = {}
    ancestor: dict[tuple[str, str], PropertyFact] = {}
    ancestor_rank = {
        release_id: rank
        for rank, release_id in enumerate(ancestor_release_ids)
    }
    selected_rank: dict[tuple[str, str], int] = {}
    for fact in facts:
        key = (str(fact.instance_id), str(fact.property_name))
        release_id = str(fact.ontology_release_id or "")
        if release_id == str(current_release_id):
            current.setdefault(key, fact)
            continue
        rank = ancestor_rank.get(release_id)
        if rank is None:
            continue
        previous_rank = selected_rank.get(key)
        if previous_rank is None or rank < previous_rank:
            ancestor[key] = fact
            selected_rank[key] = rank
    return current, ancestor


def _release_ancestor_context(
    db: Session, ontology_id: str, current_release_id: str,
) -> tuple[list[str], bool, bool]:
    rows = db.query(
        OntologyVersion.id,
        OntologyVersion.parent_version_id,
        OntologyVersion.node_kind,
        OntologyVersion.promoted_from_id,
    ).filter(OntologyVersion.ontology_id == ontology_id).all()
    releases = {
        str(row.id): row for row in rows
        if (row.node_kind or "release") == "release"
    }
    result: list[str] = []
    seen = {str(current_release_id)}
    current = releases.get(str(current_release_id))
    explicit_trial_activation = bool(
        current is not None and current.promoted_from_id)
    cursor = str(current.parent_version_id or "") if current else ""
    reset_boundary_reached = False
    while cursor and cursor not in seen:
        seen.add(cursor)
        row = releases.get(cursor)
        if row is None:
            break
        result.append(cursor)
        if row.promoted_from_id:
            # Rollback/legacy activations inherit only as far as the nearest
            # complete promotion baseline, never through it.
            reset_boundary_reached = True
            break
        cursor = str(row.parent_version_id or "")
    return result, explicit_trial_activation, reset_boundary_reached


def _runtime_state_conflicts(
    db: Session, *, ontology_id: str, current_release_id: str,
    trial_objects: list[OntologyTrialObject],
    trial_links: list[OntologyTrialLink],
) -> dict:
    """Find trial values that would erase newer non-lake runtime facts.

    Current-release facts take precedence.  A normal trial promotion is an
    explicit new baseline, while a rollback/legacy activation inherits the
    nearest matching ancestor provenance.  Ordering matches the canonical Fact
    reader (recorded_at, per-property seq, id), including same-millisecond
    writes.
    """
    candidates = {str(item.object_id): item for item in trial_objects}
    (
        ancestor_release_ids,
        explicit_trial_activation,
        reset_boundary_reached,
    ) = (
        _release_ancestor_context(
        db, ontology_id, current_release_id)
    )
    current_objects = db.query(FoObjectInstance).filter(
        FoObjectInstance.ontology_id == ontology_id,
        FoObjectInstance.ontology_release_id == current_release_id,
    ).all()
    current_object_by_id = {
        str(item.id): item for item in current_objects
    }

    differing: dict[
        tuple[str, str],
        tuple[Any, bool, Any, bool, str | None, str | None],
    ] = {}
    for current_object in current_objects:
        candidate = candidates.get(str(current_object.id))
        current_props = dict(current_object.properties or {})
        candidate_props = dict(candidate.properties or {}) if candidate else {}
        candidate_object_present = candidate is not None
        property_names = (
            current_props.keys() | candidate_props.keys()
            if candidate_object_present
            else current_props.keys()
        )
        for property_name in property_names:
            current_present = property_name in current_props
            current_value = (
                current_props.get(property_name)
                if current_present
                else None
            )
            candidate_present = (
                candidate_object_present and property_name in candidate_props
            )
            candidate_value = (
                candidate_props.get(property_name) if candidate_present else None
            )
            if (
                current_present != candidate_present
                or (
                    current_present
                    and candidate_present
                    and current_value != candidate_value
                )
            ):
                differing[(str(current_object.id), str(property_name))] = (
                    current_value,
                    current_present,
                    candidate_value,
                    candidate_present,
                    str(current_object.object_type_id or "") or None,
                    str(current_object.source or "") or None,
                )
    release_scope = [str(current_release_id), *ancestor_release_ids]
    facts = (
        _runtime_coordinate_facts(
            db,
            ontology_id=ontology_id,
            release_ids=release_scope,
            kind="property",
            coordinates=sorted(differing),
        )
        if differing
        else []
    )
    latest, ancestor_latest = _runtime_latest_by_scope(
        facts,
        current_release_id=current_release_id,
        ancestor_release_ids=ancestor_release_ids,
    )

    conflicts: list[dict] = []

    def property_fact_matches_projection(
        fact: PropertyFact | None, current_value: Any, current_present: bool,
    ) -> bool:
        if fact is None:
            return False
        payload = fact.value or {}
        if current_present:
            return (
                payload.get("present") is not False
                and payload.get("v") == current_value
            )
        # Legacy {"v": None} facts prove explicit null, not removal: the old
        # writer did not emit removal facts at all.  Ambiguous history must
        # fail closed rather than laundering an unattributed deletion into a
        # lake-authoritative state.
        return payload.get("present") is False

    for key in sorted(differing):
        fact = latest.get(key)
        (
            current_value,
            current_present,
            candidate_value,
            candidate_present,
            object_type_id,
            object_source,
        ) = (
            differing[key]
        )
        fact_matches_projection = property_fact_matches_projection(
            fact, current_value, current_present,
        )
        if (
            fact_matches_projection
            and _is_lake_projection_fact_source(fact.source)
        ):
            continue
        ancestor_fact = ancestor_latest.get(key)
        ancestor_matches_projection = property_fact_matches_projection(
            ancestor_fact, current_value, current_present,
        )
        if (
            not current_present
            and candidate_present
            and fact is None
            and ancestor_fact is None
        ):
            # A draft adding a genuinely new property is normal schema/data
            # evolution.  Absence of both current and historical provenance is
            # not runtime drift and must not block publication.
            continue
        if (
            fact is None
            and str(object_source or "").lower() == "pipeline"
            and (
                (
                    explicit_trial_activation
                    and ancestor_matches_projection
                )
                or (
                    ancestor_matches_projection
                    and _is_lake_projection_fact_source(
                        ancestor_fact.source)
                )
                or (
                    reset_boundary_reached
                    and ancestor_fact is None
                )
            )
        ):
            # A no-op activation rebinds the materialized lake object to the
            # new release but intentionally appends no duplicate Fact.  The
            # release-owned pipeline projection is the activation baseline.
            continue
        reported_fact = fact if fact_matches_projection else (
            ancestor_fact
            if (
                not explicit_trial_activation
                and ancestor_matches_projection
            )
            else None
        )
        conflicts.append({
            "resourceKind": "objectProperty",
            "objectId": key[0],
            "objectTypeId": object_type_id,
            "property": key[1],
            "current": _redact_runtime_state_value(
                current_value, key[1]),
            "currentPresent": current_present,
            "candidate": _redact_runtime_state_value(
                candidate_value, key[1]),
            "candidatePresent": candidate_present,
            "candidateObjectPresent": key[0] in candidates,
            "source": _safe_runtime_fact_source(
                reported_fact.source if reported_fact is not None else None),
            "factId": (
                reported_fact.id if reported_fact is not None else None),
        })

    # Object existence is a first-class temporal chain.  This covers both
    # runtime-created objects which a candidate would delete and tombstoned
    # objects which a candidate would revive, including zero-property objects.
    object_ids = sorted(set(current_object_by_id) | set(candidates))
    object_facts = _runtime_existence_facts(
        db,
        ontology_id=ontology_id,
        release_ids=release_scope,
        kind="object",
        instance_ids=object_ids,
    ) if object_ids else []
    current_object_facts, ancestor_object_facts = _runtime_latest_by_scope(
        object_facts,
        current_release_id=current_release_id,
        ancestor_release_ids=ancestor_release_ids,
    )
    current_object_facts_by_id = {
        key[0]: fact for key, fact in current_object_facts.items()
    }
    ancestor_object_facts_by_id = {
        key[0]: fact for key, fact in ancestor_object_facts.items()
    }

    for object_id in sorted(set(current_object_by_id) - set(candidates)):
        current_object = current_object_by_id[object_id]
        fact = current_object_facts_by_id.get(object_id)
        fact_matches_projection = (
            fact is not None
            and (fact.value or {}).get("v") is True
        )
        ancestor_fact = ancestor_object_facts_by_id.get(object_id)
        ancestor_matches_projection = (
            ancestor_fact is not None
            and (ancestor_fact.value or {}).get("v") is True
        )
        if (
            fact_matches_projection
            and _is_lake_projection_fact_source(fact.source)
        ):
            continue
        if fact is None and (
            (
                explicit_trial_activation
                and ancestor_matches_projection
                and str(current_object.source or "").lower() == "pipeline"
            )
            or (
                ancestor_matches_projection
                and _is_lake_projection_fact_source(ancestor_fact.source)
            )
            or (
                reset_boundary_reached
                and ancestor_fact is None
                and str(current_object.source or "").lower() == "pipeline"
            )
        ):
            continue
        reported_fact = fact if fact_matches_projection else (
            ancestor_fact
            if (
                not explicit_trial_activation
                and ancestor_matches_projection
            )
            else None
        )
        conflicts.append({
            "resourceKind": "object",
            "objectId": object_id,
            "objectTypeId": (
                str(current_object.object_type_id or "") or None),
            "current": _redact_runtime_state_value({
                "exists": True,
                "objectTypeId": str(current_object.object_type_id),
                "properties": dict(current_object.properties or {}),
            }),
            "candidate": {"exists": False},
            "source": _safe_runtime_fact_source(
                reported_fact.source if reported_fact is not None else None),
            "factId": (
                reported_fact.id if reported_fact is not None else None),
        })

    for object_id in sorted(set(candidates) - set(current_object_by_id)):
        candidate = candidates[object_id]
        fact = current_object_facts_by_id.get(object_id)
        fact_matches_projection = (
            fact is not None
            and (fact.value or {}).get("v") is False
        )
        ancestor_fact = ancestor_object_facts_by_id.get(object_id)
        ancestor_matches_projection = (
            ancestor_fact is not None
            and (ancestor_fact.value or {}).get("v") is False
        )
        if (
            fact_matches_projection
            and _is_lake_projection_fact_source(fact.source)
        ):
            continue
        if fact is None and ancestor_fact is None:
            # No existence history means a genuine first release addition.
            continue
        if (
            fact is None
            and ancestor_matches_projection
            and (
                explicit_trial_activation
                or _is_lake_projection_fact_source(ancestor_fact.source)
            )
        ):
            continue
        reported_fact = fact if fact_matches_projection else (
            ancestor_fact
            if (
                not explicit_trial_activation
                and ancestor_matches_projection
            )
            else None
        )
        conflicts.append({
            "resourceKind": "object",
            "objectId": object_id,
            "objectTypeId": str(candidate.object_type_id or "") or None,
            "current": {"exists": False},
            "candidate": _redact_runtime_state_value({
                "exists": True,
                "objectTypeId": str(candidate.object_type_id),
                "properties": dict(candidate.properties or {}),
            }),
            "source": _safe_runtime_fact_source(
                reported_fact.source if reported_fact is not None else None),
            "factId": (
                reported_fact.id if reported_fact is not None else None),
        })

    current_links = {
        str(item.id): item
        for item in db.query(FoLinkInstance).filter(
            FoLinkInstance.ontology_id == ontology_id,
            FoLinkInstance.ontology_release_id == current_release_id,
        ).all()
    }
    candidate_links = {
        str(item.link_id): item for item in trial_links
    }
    link_ids = sorted(current_links.keys() | candidate_links.keys())
    link_facts = _runtime_existence_facts(
        db,
        ontology_id=ontology_id,
        release_ids=release_scope,
        kind="link",
        instance_ids=link_ids,
    ) if link_ids else []
    scoped_link_facts, scoped_ancestor_link_facts = (
        _runtime_latest_by_scope(
            link_facts,
            current_release_id=current_release_id,
            ancestor_release_ids=ancestor_release_ids,
        )
    )
    latest_link_facts = {
        key[0]: fact for key, fact in scoped_link_facts.items()
    }
    ancestor_link_facts = {
        key[0]: fact for key, fact in scoped_ancestor_link_facts.items()
    }

    def link_state(item: Any | None) -> dict:
        if item is None:
            return {"exists": False}
        return {
            "exists": True,
            "linkTypeId": str(item.link_type_id),
            "sourceObjectId": str(item.source_object_id),
            "targetObjectId": str(item.target_object_id),
            "properties": dict(item.properties or {}),
        }

    for link_id in link_ids:
        current_item = current_links.get(link_id)
        candidate_item = candidate_links.get(link_id)
        current_state = link_state(current_item)
        candidate_state = link_state(candidate_item)
        if current_state == candidate_state:
            continue
        fact = latest_link_facts.get(link_id)
        current_exists = current_item is not None
        fact_matches_projection = (
            fact is not None
            and isinstance((fact.value or {}).get("v"), bool)
            and (fact.value or {}).get("v") == current_exists
        )
        # A candidate-only ID with no Fact is a genuine first release of that
        # relationship, not unattributed runtime drift.  A current row without
        # provenance remains fail-closed; a candidate-only ID with a current-
        # release tombstone is handled below and can therefore block revival.
        ancestor_fact = ancestor_link_facts.get(link_id)
        ancestor_matches_projection = (
            ancestor_fact is not None
            and isinstance((ancestor_fact.value or {}).get("v"), bool)
            and (ancestor_fact.value or {}).get("v") == current_exists
        )
        if fact is None and current_item is None:
            if ancestor_fact is None:
                continue
            if (ancestor_fact.value or {}).get("v") is False:
                if (
                    explicit_trial_activation
                    or _is_lake_projection_fact_source(
                        ancestor_fact.source)
                ):
                    continue
                fact = ancestor_fact
                fact_matches_projection = True
        if (
            fact is None
            and current_item is not None
            and (
                (
                    explicit_trial_activation
                    and ancestor_matches_projection
                )
                or (
                    bool(current_item.source_relation_id)
                    and
                    ancestor_matches_projection
                    and _is_lake_projection_fact_source(
                        ancestor_fact.source)
                )
                or (
                    bool(current_item.source_relation_id)
                    and
                    reset_boundary_reached
                    and ancestor_fact is None
                )
            )
        ):
            # Same no-op activation baseline as objects: promoted lake links
            # are explicitly adopted by a normal release even when a legacy
            # edge has no Relation id.  Rollback inheritance remains stricter:
            # it needs immutable Relation lineage before an implicit baseline
            # can be trusted.
            continue
        if (
            fact is None
            and not explicit_trial_activation
            and ancestor_matches_projection
            and not _is_lake_projection_fact_source(
                ancestor_fact.source)
        ):
            fact = ancestor_fact
            fact_matches_projection = True
        if (
            fact_matches_projection
            and _is_lake_projection_fact_source(fact.source)
        ):
            continue
        conflicts.append({
            "resourceKind": "link",
            "linkId": link_id,
            "linkTypeId": str(
                getattr(current_item, "link_type_id", None)
                or getattr(candidate_item, "link_type_id", None)
                or (
                    fact.object_type_id
                    if fact_matches_projection else None
                )
                or ""
            ),
            "current": _redact_runtime_state_value(current_state),
            "candidate": _redact_runtime_state_value(candidate_state),
            "source": _safe_runtime_fact_source(
                fact.source if fact_matches_projection else None),
            "factId": fact.id if fact_matches_projection else None,
        })

    conflicts.sort(key=lambda item: (
        str(item.get("resourceKind") or ""),
        str(item.get("objectId") or item.get("linkId") or ""),
        str(item.get("property") or ""),
    ))
    total = len(conflicts)
    property_count = sum(
        item["resourceKind"] == "objectProperty" for item in conflicts)
    object_count = sum(
        item["resourceKind"] == "object" for item in conflicts)
    link_count = total - property_count - object_count
    return {
        "totalCount": total,
        "propertyConflictCount": property_count,
        "objectConflictCount": object_count,
        "linkConflictCount": link_count,
        "itemLimit": _RUNTIME_STATE_CONFLICT_LIMIT,
        "truncated": total > _RUNTIME_STATE_CONFLICT_LIMIT,
        "items": conflicts[:_RUNTIME_STATE_CONFLICT_LIMIT],
    }


def _release_readiness(
        db: Session, *, draft: OntologyVersion,
        current: OntologyVersion, report: dict) -> dict:
    """Return a read-only, structured preview of every deterministic publish gate.

    The impact dialog consumes this before the user confirms publication.  It
    deliberately never mutates the trial record: the authoritative promote
    endpoint repeats the same fail-closed checks under row locks.
    """
    snap = complete_snapshot(draft.snapshot_formal)
    errors: list[dict] = []

    if draft.lifecycle_status != "trial_ready":
        errors.append(_gate_error(
            "trial_ready_required", "version",
            "只有已通过并冻结的试跑态版本可以转为发布态",
            item_id=draft.id, name=draft.version_number))
    if draft.base_release_id != current.id:
        errors.append(_gate_error(
            "draft_base_outdated", "version",
            "当前发布版已变化，需要先基于最新发布版合并本分支改动",
            item_id=draft.id, name=draft.version_number))

    # Revalidate mappings even for legacy passed trials. Older deployments may
    # have allowed partial mappings, while current publication is fail-closed.
    errors.extend(validate_builtin_sentinel_contract(snap["sentinels"]))
    errors.extend(_dynamic_sentinel_id_conflict_errors(
        db, draft.ontology_id, snap["sentinels"],
    ))
    errors.extend(validate_release_mapping_contract(snap))

    run = db.query(OntologyTrialRun).filter(
        OntologyTrialRun.ontology_id == draft.ontology_id,
        OntologyTrialRun.version_id == draft.id,
        OntologyTrialRun.status == "passed",
    ).order_by(desc(OntologyTrialRun.created_at)).first()
    exact_trial = False
    runtime_conflicts = _empty_runtime_state_conflicts()
    if run is None:
        errors.append(_gate_error(
            "passed_trial_required", "trialRun",
            "发布前必须先完成一次通过的隔离试跑",
            item_id=draft.id, name=draft.version_number))
    else:
        current_hash = snapshot_hash(snap)
        exact_trial = (
            run.revision == (draft.revision or 0)
            and run.snapshot_hash == draft.snapshot_hash
            and run.snapshot_hash == current_hash
        )
        if not exact_trial:
            errors.append(_gate_error(
                "trial_snapshot_stale", "trialRun",
                "试跑记录与当前快照不一致，需要创建新草稿并重新试跑",
                item_id=run.id, name=draft.version_number))
        else:
            errors.extend(_verify_trial_dataset_pins(db, run))
            if settings.environment == "production":
                errors.extend(validate_manual_mapping_trial_contract(
                    db, snap, run.dataset_versions,
                ))
            expected = (run.result_json or {}).get("counts") or {}
            object_count = db.query(OntologyTrialObject).filter(
                OntologyTrialObject.trial_run_id == run.id).count()
            link_count = db.query(OntologyTrialLink).filter(
                OntologyTrialLink.trial_run_id == run.id).count()
            if (object_count != int(expected.get("objects") or 0)
                    or link_count != int(expected.get("links") or 0)):
                errors.append(_gate_error(
                    "trial_materialization_incomplete", "trialRun",
                    "试跑隔离投影不完整，需要创建新草稿后重新试跑",
                    item_id=run.id, name=draft.version_number))
            if run.impact_hash != report.get("impactHash"):
                errors.append(_gate_error(
                    "trial_impact_stale", "trialRun",
                    "试跑影响范围与当前发布基线不一致，需要重新试跑",
                    item_id=run.id, name=draft.version_number))
            runtime_conflicts = _runtime_state_conflicts(
                db,
                ontology_id=draft.ontology_id,
                current_release_id=current.id,
                trial_objects=db.query(OntologyTrialObject).filter(
                    OntologyTrialObject.trial_run_id == run.id,
                ).all(),
                trial_links=db.query(OntologyTrialLink).filter(
                    OntologyTrialLink.trial_run_id == run.id,
                ).all(),
            )
            if runtime_conflicts["totalCount"]:
                issue = _gate_error(
                    "runtime_state_conflict", "runtimeState",
                    "试跑候选会覆盖当前发布版中的非数据湖运行态事实，"
                    "系统不会自动选择保留或覆盖",
                    item_id=run.id,
                    name=draft.version_number,
                    field="runtimeStateConflicts",
                )
                issue["conflictCount"] = runtime_conflicts["totalCount"]
                errors.append(issue)

    ready = len(errors) == 0
    base_outdated = draft.base_release_id != current.id
    return {
        "ready": ready,
        "blockingCount": len(errors),
        "errors": errors,
        "trialRunId": run.id if run else None,
        "runtimeStateConflicts": runtime_conflicts,
        "repairStrategy": (
            None
            if ready or runtime_conflicts["totalCount"]
            else "rebase" if base_outdated else "create_draft"
        ),
        "repairSourceVersionId": current.id if base_outdated else draft.id,
    }


@router.post("/{ontology_id}/versions/{version_id}/promote", status_code=201)
def promote_draft(
    ontology_id: str, version_id: str, body: dict,
    db: Session = Depends(get_db), current_user=Depends(require_admin),
):
    # Acquire the cross-process projection lock before the project row lock.
    # ``build_all`` uses the same advisory→row order; reversing it here would
    # allow an ABBA deadlock during publication.
    from app.ontologies.mappings.mapping_service import _ontology_build_lock
    with _ontology_build_lock(db, ontology_id):
        return _promote_draft_locked(
            ontology_id, version_id, body, db, current_user)


def _promote_draft_locked(
    ontology_id: str, version_id: str, body: dict,
    db: Session, current_user,
):
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).with_for_update().first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    current = _current_release(db, project)
    draft = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).with_for_update().first()
    if draft is None:
        raise HTTPException(404, "Version not found")
    if draft.node_kind != "draft":
        raise HTTPException(409, detail={
            "code": "promotion_requires_draft", "message": "只能晋级草稿分支",
        })
    if draft.lifecycle_status != "trial_ready":
        raise HTTPException(409, detail={
            "code": "trial_ready_required",
            "message": "只有已通过并冻结的试跑态版本可以转为发布态",
        })
    if draft.base_release_id != current.id:
        raise HTTPException(409, detail={
            "code": "draft_base_outdated",
            "message": "草稿基线不是当前发布版，拒绝覆盖并发发布",
            "draftBaseReleaseId": draft.base_release_id,
            "currentReleaseId": current.id,
        })
    trial_run_id = body.get("trial_run_id", body.get("trialRunId"))
    run = db.query(OntologyTrialRun).filter(
        OntologyTrialRun.id == trial_run_id,
        OntologyTrialRun.ontology_id == ontology_id,
        OntologyTrialRun.version_id == draft.id,
    ).first()
    if run is None or run.status != "passed":
        raise HTTPException(409, detail={
            "code": "passed_trial_required", "message": "发布前必须选择一次通过的试跑",
        })
    snap = complete_snapshot(draft.snapshot_formal)
    current_hash = snapshot_hash(snap)
    if (run.revision != (draft.revision or 0)
            or run.snapshot_hash != draft.snapshot_hash
            or run.snapshot_hash != current_hash):
        run.status = "stale"
        db.commit()
        raise HTTPException(409, detail={
            "code": "trial_snapshot_stale",
            "message": "试跑快照与当前结构不一致，请从该版本创建新草稿后重新试跑",
        })
    sentinel_errors = validate_builtin_sentinel_contract(snap["sentinels"])
    sentinel_errors.extend(_dynamic_sentinel_id_conflict_errors(
        db, ontology_id, snap["sentinels"],
    ))
    _raise_publish_errors(
        sentinel_errors,
        "发布前建模内置哨兵字段校验未通过",
    )
    _raise_publish_errors(
        validate_release_mapping_contract(snap),
        "发布前数据映射完整性校验未通过",
    )
    report = impact_report(current.snapshot_formal, snap)
    acknowledged = body.get("impact_hash", body.get("impactHash"))
    if not acknowledged or acknowledged != report["impactHash"] or run.impact_hash != report["impactHash"]:
        raise HTTPException(409, detail={
            "code": "impact_review_required",
            "message": "影响分析已变化或尚未确认，请重新审核",
            "currentImpactHash": report["impactHash"],
        })
    trial_pin_errors = _verify_trial_dataset_pins(db, run)
    if settings.environment == "production":
        trial_pin_errors.extend(validate_manual_mapping_trial_contract(
            db, snap, run.dataset_versions,
        ))
    _raise_publish_errors(
        trial_pin_errors,
        "试跑数据版本或人工数据自动灌入契约已变化",
    )

    trial_objects = db.query(OntologyTrialObject).filter(
        OntologyTrialObject.trial_run_id == run.id).all()
    trial_links = db.query(OntologyTrialLink).filter(
        OntologyTrialLink.trial_run_id == run.id).all()
    expected = (run.result_json or {}).get("counts") or {}
    if len(trial_objects) != int(expected.get("objects") or 0) or len(trial_links) != int(expected.get("links") or 0):
        raise HTTPException(409, detail={
            "code": "trial_materialization_incomplete",
            "message": "试跑隔离投影不完整；请从该版本创建新草稿后重新试跑",
        })

    # This check intentionally runs inside the same cross-process projection
    # lock and project-row transaction used by action/manual runtime writers.
    # It is repeated here (rather than trusting the impact preview) so an
    # action committed after trial/preview cannot be silently overwritten.
    # No formal definition, projection, Fact, audit, or release row has been
    # mutated at this point.
    runtime_conflicts = _runtime_state_conflicts(
        db,
        ontology_id=ontology_id,
        current_release_id=current.id,
        trial_objects=trial_objects,
        trial_links=trial_links,
    )
    if runtime_conflicts["totalCount"]:
        raise HTTPException(409, detail={
            "code": "runtime_state_conflict",
            "message": (
                "试跑候选会覆盖当前发布版中的非数据湖运行态事实；"
                "发布已在写入前停止，请重新审核运行态与候选值"
            ),
            "runtimeStateConflicts": runtime_conflicts,
        })

    from app.ontologies.formal_modeling.facts import (
        record_link_fact,
        record_object_presence,
        record_object_tombstone,
        record_property_facts,
    )
    old_objects = db.query(FoObjectInstance).filter(
        FoObjectInstance.ontology_id == ontology_id).all()
    old_links = db.query(FoLinkInstance).filter(
        FoLinkInstance.ontology_id == ontology_id).all()
    old_object_by_id = {item.id: item for item in old_objects}
    candidate_ids = {item.object_id for item in trial_objects}
    candidate_link_ids = {item.link_id for item in trial_links}
    release_id = str(uuid.uuid4())
    release_number = _next_release_activation_number(db, ontology_id)
    source = f"ontology-release://{release_id}"

    try:
        _restore_formal_snapshot(db, ontology_id, snap)
        pinned = {str(item.get("datasetId")): item for item in (run.dataset_versions or [])}
        for mapping in db.query(OntologyMapping).filter(
                OntologyMapping.ontology_id == ontology_id).all():
            mapping.status = "applied"
            fields = dict(mapping.field_mapping or {})
            pin = pinned.get(str(mapping.curated_dataset_id))
            if pin:
                fields["__applied_dataset_version_id__"] = pin.get("versionId")
            mapping.field_mapping = fields
        for mapping in db.query(OntologyLinkMapping).filter(
                OntologyLinkMapping.ontology_id == ontology_id).all():
            mapping.status = "active"
            fields = dict(mapping.field_mapping or {})
            for role, dataset_id in (
                ("source", mapping.src_dataset_id),
                ("target", mapping.tgt_dataset_id),
                ("edge", mapping.edge_dataset_id),
            ):
                if dataset_id and str(dataset_id) in pinned:
                    fields[f"__applied_{role}_version_id__"] = pinned[str(dataset_id)].get("versionId")
            mapping.field_mapping = fields
        for sentinel in db.query(Sentinel).filter(
                Sentinel.ontology_id == ontology_id,
                Sentinel.origin == "release_builtin").all():
            sentinel.status = "published"

        for item in old_links:
            if item.id not in candidate_link_ids:
                record_link_fact(
                    db, ontology_id=ontology_id, link_instance_id=item.id,
                    link_type_id=item.link_type_id, exists=False,
                    source=source, actor_id=current_user.id, caused_by=run.id,
                    ontology_version=release_number,
                    ontology_release_id=release_id)
        for item in old_objects:
            if item.id not in candidate_ids:
                record_object_tombstone(
                    db, ontology_id=ontology_id, instance_id=item.id,
                    object_type_id=item.object_type_id, source=source,
                    actor_id=current_user.id, caused_by=run.id,
                    ontology_version=release_number,
                    ontology_release_id=release_id)

        db.query(FoLinkInstance).filter(
            FoLinkInstance.ontology_id == ontology_id).delete(synchronize_session=False)
        db.query(FoObjectInstance).filter(
            FoObjectInstance.ontology_id == ontology_id).delete(synchronize_session=False)
        # bulk delete 不会同步 Session identity map。后续用相同稳定 ID 写入
        # 新投影前先移除旧 ORM 身份，避免 v1→v2 时出现对象冲突或脏缓存。
        for old_item in [*old_links, *old_objects]:
            db.expunge(old_item)
        promoted_instances: list[
            tuple[FoObjectInstance, list, OntologyTrialObject]
        ] = []
        for item in trial_objects:
            old = old_object_by_id.get(item.object_id)
            old_props = dict(old.properties or {}) if old else None
            new_props = dict(item.properties or {})
            if old is None:
                # Only a genuine creation/revival starts a new existence edge.
                # Backfilling exists=True for a legacy already-present object
                # would make as-of queries incorrectly report it absent before
                # this release.  Existence precedes properties so no cutoff can
                # observe properties before the object itself exists.
                record_object_presence(
                    db, ontology_id=ontology_id, instance_id=item.object_id,
                    object_type_id=item.object_type_id, source=source,
                    actor_id=current_user.id, caused_by=run.id,
                    ontology_version=release_number,
                    ontology_release_id=release_id,
                )
            new_facts = record_property_facts(
                db, ontology_id=ontology_id, instance_id=item.object_id,
                object_type_id=item.object_type_id, old_props=old_props,
                new_props=new_props, source=source, actor_id=current_user.id,
                caused_by=run.id, confidence=1.0,
                ontology_version=release_number,
                ontology_release_id=release_id)
            promoted_instance = FoObjectInstance(
                id=item.object_id, ontology_id=ontology_id,
                ontology_release_id=release_id,
                object_type_id=item.object_type_id,
                properties=dict(item.properties or {}),
                computed=dict(item.computed or {}),
                source="pipeline", external_id=item.external_id,
            )
            db.add(promoted_instance)
            promoted_instances.append((promoted_instance, new_facts, item))
        # Collection-scope derived functions must observe the complete promoted
        # object universe, not a prefix determined by insertion order.
        db.flush()
        for item in trial_links:
            db.add(FoLinkInstance(
                id=item.link_id, ontology_id=ontology_id,
                ontology_release_id=release_id,
                link_type_id=item.link_type_id,
                source_object_id=item.source_object_id,
                target_object_id=item.target_object_id,
                properties=dict(item.properties or {}),
                source_relation_id=item.source_relation_id,
            ))
            record_link_fact(
                db, ontology_id=ontology_id, link_instance_id=item.link_id,
                link_type_id=item.link_type_id, exists=True,
                source=source, actor_id=current_user.id, caused_by=run.id,
                ontology_version=release_number,
                ontology_release_id=release_id)
        db.flush()
        from app.ontologies.formal_modeling.derived import (
            recompute_instance_derived,
        )
        for promoted_instance, new_facts, trial_item in promoted_instances:
            recompute_instance_derived(
                db,
                ontology_id=ontology_id,
                instance=promoted_instance,
                trigger_facts=new_facts,
                caused_by=run.id,
            )
            if dict(promoted_instance.computed or {}) != dict(
                    trial_item.computed or {}):
                raise RuntimeError(
                    "试跑冻结的派生值与发布激活时重算结果不一致: "
                    f"{trial_item.object_id}")
        db.flush()
        _raise_publish_errors(_release_errors(db, ontology_id))

        # The release snapshot is the activated, self-contained definition
        # set—not the pre-activation draft JSON. Mapping application pins and
        # built-in Sentinel publication status are part of what a later
        # rollback must be able to restore without consulting mutable rows.
        release_snapshot = _snapshot_formal(db, ontology_id)
        release = OntologyVersion(
            id=release_id, ontology_id=ontology_id,
            version_number=release_number,
            version_label=str(body.get("version_label") or body.get("versionLabel") or draft.version_label or ""),
            description=str(body.get("description") or draft.description or ""),
            parent_version_id=current.id, base_release_id=release_id,
            promoted_from_id=draft.id, node_kind="release",
            lifecycle_status="released", revision=0,
            snapshot_formal=release_snapshot,
            snapshot_hash=snapshot_hash(release_snapshot),
            canvas_layout=_json_safe(draft.canvas_layout or {}),
            published_at=datetime.now(timezone.utc),
            change_summary={"formal": _diff_formal(
                                current.snapshot_formal, release_snapshot),
                            "impact": report},
            created_by=current_user.id,
        )
        db.add(release)
        # ``OntologyProject.current_release_id`` is a real PostgreSQL foreign
        # key.  SQLAlchemy cannot infer the flush dependency from plain FK
        # scalar assignments, so updating the project pointer in the same
        # flush can issue the UPDATE before the version INSERT.  Persist the
        # release row first; the surrounding transaction still makes the
        # promotion atomic and a later failure rolls both changes back.
        db.flush()
        project.current_release_id = release.id
        project.version = release_number
        project.status = "published"
        draft.lifecycle_status = "superseded"
        invalidated_dynamic_sentinels = _invalidate_dynamic_sentinels_for_release(
            db, ontology_id, release.id)
        db.add(AuditLog(
            id=str(uuid.uuid4()), ontology_id=ontology_id,
            event_type="publish", event_subtype="draft_promoted",
            user_id=current_user.id, user_name=current_user.username,
            description=f"将 {draft.version_number} 晋级为 {release_number}",
            object_type="ontology_version", object_id=release.id,
            meta={"draft_version_id": draft.id, "trial_run_id": run.id,
                  "impact_hash": report["impactHash"],
                  "invalidated_dynamic_sentinels": invalidated_dynamic_sentinels},
        ))
        db.flush()
        projection_check = None
        if settings.environment == "production":
            projection_check = _rebuild_required_query_projections(db, ontology_id)
            if not projection_check["ready"]:
                raise RuntimeError("Neo4j/Chroma candidate projection is not ready")
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        compensation = None
        if settings.environment == "production":
            try:
                compensation = _rebuild_required_query_projections(db, ontology_id)
            except Exception as compensation_exc:  # noqa: BLE001
                compensation = {"ready": False, "error": str(compensation_exc)}
        raise HTTPException(503, detail={
            "code": "promotion_failed",
            "message": f"发布事务已回滚，当前发布版保持 {current.version_number}: {exc}",
            "compensation": compensation,
        }) from exc

    return {"data": {
        **_version_payload(release),
        "trial_run_id": run.id, "impact_hash": report["impactHash"],
        "query_projection": projection_check,
    }}


@router.post("/{ontology_id}/versions", status_code=410, deprecated=True)
def create_version(ontology_id: str, body: dict | None = None,
                   db: Session = Depends(get_db),
                   current_user=Depends(require_admin)):
    """Retired one-click publication endpoint.

    It used mutable runtime rows as its source and therefore skipped both the
    isolated trial and immutable-candidate checks.  Keeping it callable would
    make the three-state lifecycle advisory rather than authoritative.
    """
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    raise HTTPException(410, detail={
        "code": "legacy_publish_endpoint_retired",
        "message": "一键发布接口已停用；请创建草稿、完成隔离试跑后再调用 promote",
        "currentReleaseId": project.current_release_id,
        "requiredFlow": ["draft", "trial", "promote"],
    })


@router.get("/{ontology_id}/versions/{version_id}")
def get_version_detail(ontology_id: str, version_id: str, db: Session = Depends(get_db)):
    """获取版本详情（含完整快照）"""
    v = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).first()
    if not v:
        raise HTTPException(404, "Version not found")
    return {"data": {
        **_version_payload(v),
        "snapshot": {
            "entities": v.snapshot_entities or [],
            "relations": v.snapshot_relations or [],
            "logic": v.snapshot_logic or [],
            "actions": v.snapshot_actions or [],
            "formal": v.snapshot_formal or None,
        },
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }}


@router.post("/{ontology_id}/unpublish", deprecated=True)
@router.post("/{ontology_id}/versions/unpublish", include_in_schema=False)
def unpublish_ontology(ontology_id: str, db: Session = Depends(get_db),
                       current_user=Depends(require_admin)):
    """Retired mutable release withdrawal endpoint."""
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    raise HTTPException(410, detail={
        "code": "unpublish_endpoint_retired",
        "message": "发布节点不可撤回；请从目标发布快照创建草稿并完成试跑晋级",
        "currentReleaseId": project.current_release_id,
        "requiredFlow": ["draft", "trial", "promote"],
    })


def _restore_formal_snapshot(db: Session, ontology_id: str, snap: dict) -> dict:
    """只恢复定义；Object/Link Instance 始终保留，交由回滚门禁校验。"""
    for model in (FoLinkType, FoActionType, FoFunction, FoObjectType):
        db.query(model).filter(model.ontology_id == ontology_id).delete(
            synchronize_session=False)

    def restore(model, create_schema, items):
        for item in items or []:
            parsed = create_schema.model_validate(item)
            data = parsed.model_dump(exclude_none=False)
            if "source" in item:
                data["source"] = _json_safe(item.get("source"))
            db.add(model(
                id=item.get("id") or str(uuid.uuid4()),
                ontology_id=ontology_id,
                **data,
            ))

    restore(FoObjectType, FS.ObjectTypeCreate, snap.get("objectTypes"))
    restore(FoLinkType, FS.LinkTypeCreate, snap.get("linkTypes"))
    restore(FoActionType, FS.ActionTypeCreate, snap.get("actions"))
    restore(FoFunction, FS.FunctionCreate, snap.get("functions"))

    # 旧版快照没有下列 key 时保留当前定义，不把“未快照”误解为空集合。
    if "sentinels" in snap:
        builtin_ids = [item[0] for item in db.query(Sentinel.id).filter(
            Sentinel.ontology_id == ontology_id,
            Sentinel.origin == "release_builtin",
        ).all()]
        db.query(SentinelMatchState).filter(
            SentinelMatchState.ontology_id == ontology_id,
            SentinelMatchState.sentinel_id.in_(builtin_ids or [""]),
        ).delete(synchronize_session=False)
        db.query(Sentinel).filter(
            Sentinel.ontology_id == ontology_id,
            Sentinel.origin == "release_builtin",
        ).delete(synchronize_session=False)
        for item in snap.get("sentinels") or []:
            db.add(Sentinel(
                id=item.get("id") or str(uuid.uuid4()),
                ontology_id=ontology_id,
                name=item.get("name") or "",
                display_name=item.get("displayName") or item.get("name") or "",
                description=item.get("description"),
                bindings=_json_safe(item.get("bindings") or []),
                links=_json_safe(item.get("links") or []),
                condition=item.get("condition"),
                condition_rows=_json_safe(item.get("conditionRows") or []),
                condition_logic=item.get("conditionLogic") or "and",
                primary_alias=item.get("primaryAlias"),
                action_ids=_json_safe(item.get("actionIds") or []),
                action_parameters=_json_safe(item.get("actionParameters") or {}),
                on_change=bool(item.get("onChange", True)),
                on_schedule=bool(item.get("onSchedule", False)),
                scan_interval_seconds=int(item.get("scanIntervalSeconds") or 300),
                trigger_mode=item.get("triggerMode") or "on_enter",
                muted=bool(item.get("muted", False)),
                enabled=bool(item.get("enabled", True)),
                status=item.get("status") or "draft",
                origin="release_builtin",
                source=_json_safe(item.get("source")),
            ))

    if "mappings" in snap:
        db.query(OntologyMapping).filter(
            OntologyMapping.ontology_id == ontology_id,
        ).delete(synchronize_session=False)
        for item in snap.get("mappings") or []:
            db.add(OntologyMapping(
                id=item.get("id") or str(uuid.uuid4()),
                ontology_id=ontology_id,
                curated_dataset_id=item.get("curatedDatasetId"),
                entity_class=item.get("entityClass") or "",
                field_mapping=_json_safe(item.get("fieldMapping") or {}),
                target_object_type_id=item.get("targetObjectTypeId"),
                status=item.get("status") or "draft",
                confidence=item.get("confidence"),
            ))

    if "linkMappings" in snap:
        db.query(OntologyLinkMapping).filter(
            OntologyLinkMapping.ontology_id == ontology_id,
        ).delete(synchronize_session=False)
        for item in snap.get("linkMappings") or []:
            db.add(OntologyLinkMapping(
                id=item.get("id") or str(uuid.uuid4()),
                ontology_id=ontology_id,
                src_dataset_id=item.get("srcDatasetId"),
                tgt_dataset_id=item.get("tgtDatasetId"),
                relation_type=item.get("relationType") or "",
                src_key=item.get("srcKey") or "",
                tgt_key=item.get("tgtKey") or "",
                status=item.get("status") or "draft",
                link_type_id=item.get("linkTypeId"),
                edge_dataset_id=item.get("edgeDatasetId"),
                field_mapping=_json_safe(item.get("fieldMapping") or {}),
            ))

    # SessionLocal deliberately uses ``autoflush=False``. Promotion and
    # rollback immediately query the definitions restored above in order to
    # publish Sentinels, pin mappings and validate the candidate. Without an
    # explicit flush those queries see an empty/old projection, leaving new
    # mappings as ``draft`` and their dataset-version lineage unset until the
    # later validation fails. Make restoration an observable unit before any
    # caller continues.
    db.flush()

    return {
        "objectTypes": len(snap.get("objectTypes") or []),
        "linkTypes": len(snap.get("linkTypes") or []),
        "actions": len(snap.get("actions") or []),
        "functions": len(snap.get("functions") or []),
        "sentinels": len(snap.get("sentinels") or []) if "sentinels" in snap else None,
        "mappings": len(snap.get("mappings") or []) if "mappings" in snap else None,
        "linkMappings": len(snap.get("linkMappings") or []) if "linkMappings" in snap else None,
        "retainedInstances": db.query(FoObjectInstance).filter(
            FoObjectInstance.ontology_id == ontology_id).count(),
        "retainedLinkInstances": db.query(FoLinkInstance).filter(
            FoLinkInstance.ontology_id == ontology_id).count(),
        # 保留旧客户端字段，新契约下永远为 0。
        "prunedInstances": 0,
        "prunedLinkInstances": 0,
    }


@router.post("/{ontology_id}/versions/{version_id}/rollback")
def rollback_version(ontology_id: str, version_id: str, db: Session = Depends(get_db),
                     current_user=Depends(require_admin)):
    """Activate a new release whose definitions come from a historic release.

    A rollback is a new deployment event, never pointer reuse. Runtime rows are
    rebound to the new immutable activation id while facts, firings and
    approvals keep the release ids under which they were originally produced.
    """
    from app.ontologies.mappings.mapping_service import _ontology_build_lock
    with _ontology_build_lock(db, ontology_id):
        return _rollback_version_locked(
            ontology_id, version_id, db, current_user)


def _rollback_version_locked(
        ontology_id: str, version_id: str, db: Session, current_user):
    v = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).first()
    if not v:
        raise HTTPException(404, "Version not found")
    if v.node_kind != "release" or v.lifecycle_status != "released":
        raise HTTPException(409, detail={
            "code": "draft_cannot_rollback",
            "message": "草稿不能成为运行版本；请先完成试跑并晋级",
        })
    if v.snapshot_formal is None:
        raise HTTPException(409, detail={
            "code": "legacy_snapshot_incomplete",
            "message": "目标发布节点缺少完整结构快照，不能安全激活",
        })

    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).with_for_update().first()
    if not project:
        raise HTTPException(404, "Ontology not found")
    current = _current_release(db, project)
    activation = None
    formal_restored = None
    projection_check = None

    try:
        # 旧扁平投影也随版本恢复；先删关系，再删实体，避免 FK 顺序错误。
        db.query(Relation).filter(Relation.ontology_id == ontology_id).delete(
            synchronize_session=False)
        db.query(Entity).filter(Entity.ontology_id == ontology_id).delete(
            synchronize_session=False)
        db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).delete(
            synchronize_session=False)
        db.query(Action).filter(Action.ontology_id == ontology_id).delete(
            synchronize_session=False)

        for item in (v.snapshot_entities or []):
            db.add(Entity(
                id=item.get("id") or str(uuid.uuid4()),
                ontology_id=ontology_id,
                name_cn=item.get("name_cn") or "",
                name_en=item.get("name_en") or "",
                type=item.get("type") or "",
                description=item.get("description") or "",
                confidence=item.get("confidence", 1.0),
                properties=_json_safe(item.get("properties") or {}),
            ))
        for item in (v.snapshot_relations or []):
            db.add(Relation(
                id=item.get("id") or str(uuid.uuid4()),
                ontology_id=ontology_id,
                source_entity=item.get("source_entity") or "",
                target_entity=item.get("target_entity") or "",
                type=item.get("type") or "关联",
                confidence=item.get("confidence", 1.0),
                properties=_json_safe(item.get("properties") or {}),
            ))
        for item in (v.snapshot_logic or []):
            db.add(LogicRule(
                id=item.get("id") or str(uuid.uuid4()),
                ontology_id=ontology_id,
                name_cn=item.get("name_cn") or "",
                formula=item.get("formula"),
                enabled=bool(item.get("enabled", True)),
                status=item.get("status") or "draft",
            ))
        for item in (v.snapshot_actions or []):
            db.add(Action(
                id=item.get("id") or str(uuid.uuid4()),
                ontology_id=ontology_id,
                name_cn=item.get("name_cn") or "",
                enabled=bool(item.get("enabled", True)),
                status=item.get("status") or "draft",
            ))

        formal_restored = _restore_formal_snapshot(
            db, ontology_id, dict(v.snapshot_formal or {}))
        for sentinel in db.query(Sentinel).filter(
                Sentinel.ontology_id == ontology_id,
                Sentinel.origin == "release_builtin").all():
            sentinel.status = "published"
        restored_object_types = {
            item.id: item
            for item in db.query(FoObjectType).filter(
                FoObjectType.ontology_id == ontology_id).all()
        }
        for instance in db.query(FoObjectInstance).filter(
                FoObjectInstance.ontology_id == ontology_id).all():
            # No computed value from the release being left is authoritative
            # under the restored function set. Server-owned values are rebuilt
            # after the new activation becomes the transaction's current
            # release; legacy/browser-only values remain absent.
            instance.computed = {}

        # flush 后在“快照定义 + 原有实例”的合并视图上重跑发布门禁。
        # 任何悬挂/类型/主键/基数错误都不能通过删实例“修好”。
        db.flush()
        errors = _release_errors(db, ontology_id)
        if errors:
            db.rollback()
            raise HTTPException(409, detail={
                "code": "rollback_validation_failed",
                "message": f"回滚快照与当前运行实例不兼容（{len(errors)} 个错误）",
                "errors": errors,
            })

        activation_id = str(uuid.uuid4())
        activation_number = _next_release_activation_number(db, ontology_id)
        activation_snapshot = _snapshot_formal(db, ontology_id)
        activation = OntologyVersion(
            id=activation_id,
            ontology_id=ontology_id,
            version_number=activation_number,
            version_label=f"回滚至 {v.version_number}",
            description=v.description or "",
            parent_version_id=current.id,
            base_release_id=activation_id,
            promoted_from_id=None,
            node_kind="release",
            lifecycle_status="released",
            revision=0,
            snapshot_entities=_json_safe(v.snapshot_entities or []),
            snapshot_relations=_json_safe(v.snapshot_relations or []),
            snapshot_logic=_json_safe(v.snapshot_logic or []),
            snapshot_actions=_json_safe(v.snapshot_actions or []),
            snapshot_formal=activation_snapshot,
            snapshot_hash=snapshot_hash(activation_snapshot),
            canvas_layout=_json_safe(v.canvas_layout or {}),
            published_at=datetime.now(timezone.utc),
            change_summary={
                "formal": _diff_formal(
                    current.snapshot_formal, activation_snapshot),
                "rollback": {
                    "targetReleaseId": v.id,
                    "targetVersionNumber": v.version_number,
                    "previousReleaseId": current.id,
                    "previousVersionNumber": current.version_number,
                },
            },
            created_by=current_user.id,
        )
        db.add(activation)
        # Persist the FK/self-FK target before rebinding the project and runtime
        # projection to the new activation id.
        db.flush()
        db.query(FoObjectInstance).filter(
            FoObjectInstance.ontology_id == ontology_id,
        ).update(
            {FoObjectInstance.ontology_release_id: activation.id},
            synchronize_session="fetch",
        )
        db.query(FoLinkInstance).filter(
            FoLinkInstance.ontology_id == ontology_id,
        ).update(
            {FoLinkInstance.ontology_release_id: activation.id},
            synchronize_session="fetch",
        )
        project.version = activation.version_number
        project.status = "published"
        project.current_release_id = activation.id
        db.flush()

        # Retained objects must not carry computed values produced by the
        # release being left. Recompute against the restored target functions
        # after rebinding every row to the new activation.
        from app.ontologies.formal_modeling.derived import (
            recompute_instance_derived,
        )
        for instance in db.query(FoObjectInstance).filter(
                FoObjectInstance.ontology_id == ontology_id).all():
            object_type = restored_object_types.get(instance.object_type_id)
            recompute_instance_derived(
                db,
                ontology_id=ontology_id,
                instance=instance,
                object_type=object_type,
                caused_by=activation.id,
            )
        db.flush()
        post_activation_errors = _release_errors(db, ontology_id)
        if post_activation_errors:
            raise HTTPException(409, detail={
                "code": "rollback_validation_failed",
                "message": (
                    "回滚快照重算派生投影后不满足发布契约"
                    f"（{len(post_activation_errors)} 个错误）"),
                "errors": post_activation_errors,
            })

        invalidated_dynamic_sentinels = _invalidate_dynamic_sentinels_for_release(
            db, ontology_id, activation.id)
        db.add(AuditLog(
            id=str(uuid.uuid4()),
            ontology_id=ontology_id,
            event_type="rollback",
            event_subtype="release_activated",
            user_id=current_user.id,
            user_name=current_user.username,
            description=(
                f"从 {v.version_number} 快照激活新发布版本 "
                f"{activation.version_number}"),
            object_type="ontology_version",
            object_id=activation.id,
            meta={
                "target_release_id": v.id,
                "target_version_number": v.version_number,
                "previous_release_id": current.id,
                "activation_release_id": activation.id,
                "activation_version_number": activation.version_number,
                "invalidated_dynamic_sentinels": invalidated_dynamic_sentinels,
            },
        ))
        db.flush()
        if settings.environment == "production":
            try:
                projection_check = _rebuild_required_query_projections(
                    db, ontology_id)
            except Exception as projection_exc:  # noqa: BLE001
                raise HTTPException(503, detail={
                    "code": "rollback_projection_not_ready",
                    "message": (
                        "Neo4j/Chroma 构建回滚候选投影时失败；"
                        "发布激活事务已回滚"),
                    "projection": {
                        "ready": False,
                        "error": str(projection_exc),
                    },
                }) from projection_exc
            if not projection_check["ready"]:
                raise HTTPException(503, detail={
                    "code": "rollback_projection_not_ready",
                    "message": (
                        "Neo4j/Chroma 未能构建回滚候选投影；"
                        "发布激活事务已回滚"),
                    "projection": projection_check,
                })
        db.commit()
    except HTTPException as exc:
        db.rollback()
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if (
            exc.status_code == 503
            and detail.get("code") == "rollback_projection_not_ready"
        ):
            try:
                compensation = _rebuild_required_query_projections(
                    db, ontology_id)
            except Exception as compensation_exc:  # noqa: BLE001
                compensation = {
                    "ready": False,
                    "error": str(compensation_exc),
                }
            raise HTTPException(503, detail={
                **detail,
                "compensation": compensation,
            }) from exc
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(409, detail={
            "code": "rollback_restore_failed",
            "message": f"回滚恢复失败，当前本体保持不变: {exc}",
            "errors": [_gate_error(
                "rollback_restore_failed", "ontologyVersion", str(exc),
                item_id=version_id, name=v.version_number)],
        }) from exc

    if settings.environment != "production":
        try:
            projection_check = _rebuild_required_query_projections(
                db, ontology_id)
        except Exception as projection_exc:  # noqa: BLE001
            # SQL activation is already committed in non-production. Surface
            # optional query-store health without turning a successful,
            # durable rollback into an ambiguous HTTP 500.
            projection_check = {
                "ready": False,
                "error": str(projection_exc),
                "nonBlocking": True,
            }

    return {"data": {
        **_version_payload(activation),
        "rolled_back_to_id": v.id,
        "rolled_back_to_version": v.version_number,
        "status": "published",
        "message": "Rollback successful",
        "formal_restored": formal_restored,
        "query_projection": projection_check,
    }}


@router.get("/{ontology_id}/change-logs")
def list_change_logs(ontology_id: str, object_type: str = None, limit: int = 100, db: Session = Depends(get_db)):
    """列出变更日志"""
    q = db.query(OntologyChangeLog).filter(OntologyChangeLog.ontology_id == ontology_id)
    if object_type:
        q = q.filter(OntologyChangeLog.object_type == object_type)
    logs = q.order_by(desc(OntologyChangeLog.created_at)).limit(limit).all()
    return {"data": [{
        "id": log.id,
        "action": log.action,
        "object_type": log.object_type,
        "object_name": log.object_name,
        "before": log.before,
        "after": log.after,
        "created_by_name": log.created_by_name,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    } for log in logs]}
