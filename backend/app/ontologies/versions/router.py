"""本体版本化路由 — 版本历史 / diff / 回滚"""
from __future__ import annotations

import json
import hashlib
import math
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
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
)
from app.models.sentinel import Sentinel, SentinelMatchState
from app.models.v2.mapping import OntologyMapping, OntologyLinkMapping
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.curated import CuratedReview
from app.data_channel.datasets.service import version_has_content
from app.ontologies.formal_modeling import schemas as FS
from app.ontologies.formal_modeling.validation import validate_model
from app.ontologies.access import ontology_access_guard
from app.ontologies.versions.evolution_service import (
    complete_snapshot, impact_report, materialize_trial, next_draft_number,
    next_release_number, snapshot_hash, snapshot_models, validate_snapshot,
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
        "dataset_versions": run.dataset_versions or [],
        "result": run.result_json or {}, "impact_hash": run.impact_hash,
        "created_by": run.created_by,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
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


def _raise_publish_errors(errors: list[dict], message: str = "本体发布门禁未通过") -> None:
    if errors:
        raise HTTPException(422, detail={
            "code": "publish_validation_failed",
            "message": f"{message}（{len(errors)} 个错误）",
            "errors": errors,
        })


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
                if not isinstance(spec, dict):
                    continue  # scalar/list/object literal; runtime contract validates its type
                raw_source = spec.get("sourceType", spec.get("source"))
                if raw_source is None:
                    continue  # plain object literal
                source = str(raw_source).strip().lower().replace("-", "_")
                allowed_sources = {
                    "constant", "literal", "property", "match",
                    "match_property", "target_id", "primary_id",
                }
                field = f"actionParameters.{action_id}.{parameter_name}"
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
                alias = str(spec.get("alias") or primary_alias or "").strip()
                if alias not in aliases:
                    errors.append(_gate_error(
                        "sentinel_parameter_alias_not_found", "sentinel",
                        f"哨兵「{label}」参数「{parameter_name}」引用的 alias 不存在: {alias}",
                        item_id=sid, name=label, field=field))
                    continue
                if source in {"property", "match", "match_property"}:
                    prop = str(spec.get("property", spec.get("sourceValue")) or "").strip()
                    if not prop:
                        errors.append(_gate_error(
                            "sentinel_parameter_property_missing", "sentinel",
                            f"哨兵「{label}」参数「{parameter_name}」的属性绑定缺少 property",
                            item_id=sid, name=label, field=field))
                    elif prop != "id":
                        object_type = object_by_id.get(aliases[alias])
                        property_names = {
                            str(item.get("name"))
                            for item in ((object_type.properties or []) if object_type else [])
                            if isinstance(item, dict) and item.get("name")
                        }
                        if prop not in property_names:
                            errors.append(_gate_error(
                                "sentinel_parameter_property_not_found", "sentinel",
                                f"哨兵「{label}」参数「{parameter_name}」绑定的属性不存在: {alias}.{prop}",
                                item_id=sid, name=label, field=field))
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
        "computed": {},
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
    base_release_id = source.id if source.node_kind == "release" else (
        source.base_release_id or current.id)
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
    running_trial = db.query(OntologyTrialRun).filter(
        OntologyTrialRun.version_id == version.id,
        OntologyTrialRun.status == "running",
    ).first()
    if running_trial is not None:
        raise HTTPException(409, detail={
            "code": "trial_running",
            "message": "该版本仍在试跑中，暂时不能删除",
            "trialRunId": running_trial.id,
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
    snap = complete_snapshot(draft.snapshot_formal)
    for key in ("mappings", "linkMappings", "sentinels"):
        if key in body:
            if not isinstance(body[key], list):
                raise HTTPException(422, f"{key} must be an array")
            snap[key] = _json_safe(body[key])
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
        ))
    return result


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
    _ensure_editable_draft(draft)
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).first()
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
    run = OntologyTrialRun(
        id=str(uuid.uuid4()), ontology_id=ontology_id, version_id=draft.id,
        revision=draft.revision or 0,
        snapshot_hash=draft.snapshot_hash or snapshot_hash(snap),
        status="running", dataset_versions=[], result_json={},
        impact_hash=report["impactHash"], created_by=current_user.id,
    )
    db.add(run)
    # running 记录先落盘；进程中断后不会伪装成“从未试跑”。
    db.commit()
    try:
        materialize_trial(db, run, snap)
        if run.status == "passed":
            draft.lifecycle_status = "trial_ready"
        db.commit()
    except Exception as exc:
        db.rollback()
        run = db.query(OntologyTrialRun).filter(
            OntologyTrialRun.id == run.id).with_for_update().one()
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        run.result_json = {
            "counts": {"objects": 0, "links": 0, "facts": 0, "datasets": 0},
            "errors": [_gate_error(
                "trial_internal_error", "trialRun",
                f"试跑事务已回滚: {exc}", item_id=run.id)],
            "warnings": [], "samples": {"objects": [], "links": []},
            "actionsExecuted": 0, "sideEffects": "blocked",
        }
        db.commit()
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
    errors.extend(validate_release_mapping_contract(snap))

    run = db.query(OntologyTrialRun).filter(
        OntologyTrialRun.ontology_id == draft.ontology_id,
        OntologyTrialRun.version_id == draft.id,
        OntologyTrialRun.status == "passed",
    ).order_by(desc(OntologyTrialRun.created_at)).first()
    exact_trial = False
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

    ready = len(errors) == 0
    base_outdated = draft.base_release_id != current.id
    return {
        "ready": ready,
        "blockingCount": len(errors),
        "errors": errors,
        "trialRunId": run.id if run else None,
        "repairStrategy": (
            None if ready else "rebase" if base_outdated else "create_draft"
        ),
        "repairSourceVersionId": current.id if base_outdated else draft.id,
    }


@router.post("/{ontology_id}/versions/{version_id}/promote", status_code=201)
def promote_draft(
    ontology_id: str, version_id: str, body: dict,
    db: Session = Depends(get_db), current_user=Depends(require_admin),
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
    _raise_publish_errors(_verify_trial_dataset_pins(db, run), "试跑数据版本已变化")

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

    from app.ontologies.formal_modeling.facts import (
        record_link_fact, record_object_tombstone, record_property_facts,
    )
    old_objects = db.query(FoObjectInstance).filter(
        FoObjectInstance.ontology_id == ontology_id).all()
    old_links = db.query(FoLinkInstance).filter(
        FoLinkInstance.ontology_id == ontology_id).all()
    old_object_by_id = {item.id: item for item in old_objects}
    candidate_ids = {item.object_id for item in trial_objects}
    candidate_link_ids = {item.link_id for item in trial_links}
    release_id = str(uuid.uuid4())
    release_number = next_release_number(current.version_number)
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
        for item in trial_objects:
            old = old_object_by_id.get(item.object_id)
            old_props = dict(old.properties or {}) if old else None
            new_props = dict(item.properties or {})
            fact_props = dict(new_props)
            if old_props is not None:
                for removed in old_props.keys() - new_props.keys():
                    fact_props[removed] = None
            record_property_facts(
                db, ontology_id=ontology_id, instance_id=item.object_id,
                object_type_id=item.object_type_id, old_props=old_props,
                new_props=fact_props, source=source, actor_id=current_user.id,
                caused_by=run.id, confidence=1.0,
                ontology_version=release_number,
                ontology_release_id=release_id)
            db.add(FoObjectInstance(
                id=item.object_id, ontology_id=ontology_id,
                ontology_release_id=release_id,
                object_type_id=item.object_type_id,
                properties=dict(item.properties or {}), computed={},
                source="pipeline", external_id=item.external_id,
            ))
        for item in trial_links:
            db.add(FoLinkInstance(
                id=item.link_id, ontology_id=ontology_id,
                ontology_release_id=release_id,
                link_type_id=item.link_type_id,
                source_object_id=item.source_object_id,
                target_object_id=item.target_object_id,
                properties=dict(item.properties or {}),
            ))
            record_link_fact(
                db, ontology_id=ontology_id, link_instance_id=item.link_id,
                link_type_id=item.link_type_id, exists=True,
                source=source, actor_id=current_user.id, caused_by=run.id,
                ontology_version=release_number,
                ontology_release_id=release_id)
        db.flush()
        _raise_publish_errors(_release_errors(db, ontology_id))

        release = OntologyVersion(
            id=release_id, ontology_id=ontology_id,
            version_number=release_number,
            version_label=str(body.get("version_label") or body.get("versionLabel") or draft.version_label or ""),
            description=str(body.get("description") or draft.description or ""),
            parent_version_id=current.id, base_release_id=release_id,
            promoted_from_id=draft.id, node_kind="release",
            lifecycle_status="released", revision=0,
            snapshot_formal=snap, snapshot_hash=current_hash,
            canvas_layout=_json_safe(draft.canvas_layout or {}),
            published_at=datetime.now(timezone.utc),
            change_summary={"formal": _diff_formal(current.snapshot_formal, snap),
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
        db.add(AuditLog(
            id=str(uuid.uuid4()), ontology_id=ontology_id,
            event_type="publish", event_subtype="draft_promoted",
            user_id=current_user.id, user_name=current_user.username,
            description=f"将 {draft.version_number} 晋级为 {release_number}",
            object_type="ontology_version", object_id=release.id,
            meta={"draft_version_id": draft.id, "trial_run_id": run.id,
                  "impact_hash": report["impactHash"]},
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


@router.post("/{ontology_id}/versions", status_code=201, deprecated=True)
def create_version(ontology_id: str, body: dict, db: Session = Depends(get_db),
                   current_user=Depends(require_admin)):
    """创建新版本快照（通常在发布时调用）"""
    # Serialize publication with mapping rebuilds, actions and other release
    # transitions.  The release gate and snapshot must observe one stable state.
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).with_for_update().first()
    if not project:
        raise HTTPException(404, "Ontology not found")
    if project.status != "draft":
        raise HTTPException(409, detail={
            "code": "invalid_publish_state",
            "message": f"只有 draft 本体可发布，当前状态为 {project.status}",
        })

    _raise_publish_errors(_release_errors(db, ontology_id))

    projection_check = None
    if settings.environment == "production":
        projection_check = _rebuild_required_query_projections(db, ontology_id)
        if not projection_check["ready"]:
            raise HTTPException(503, detail={
                "code": "query_projection_not_ready",
                "message": "Neo4j/Chroma 派生查询投影未完成，拒绝发布",
                "projection": projection_check,
            })

    # 兼容旧客户端的一键发布仍然只生成发布主线 v1/v2；新 UI 走草稿→试跑→晋级。
    latest = None
    if project.current_release_id:
        latest = db.query(OntologyVersion).filter(
            OntologyVersion.id == project.current_release_id,
            OntologyVersion.ontology_id == ontology_id,
        ).first()
    if latest is None:
        latest = db.query(OntologyVersion).filter(
            OntologyVersion.ontology_id == ontology_id,
            OntologyVersion.node_kind == "release",
        ).order_by(desc(OntologyVersion.created_at)).first()
    new_version = next_release_number(latest.version_number if latest else None)

    # 快照当前数据
    entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
    logic_rules = db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).all()
    actions = db.query(Action).filter(Action.ontology_id == ontology_id).all()

    # 计算变更统计
    prev_entities = latest.snapshot_entities if latest else []
    prev_entity_ids = {e.get("id") for e in prev_entities}
    curr_entity_ids = {e.id for e in entities}

    added = len(curr_entity_ids - prev_entity_ids)
    deleted = len(prev_entity_ids - curr_entity_ids)
    modified = 0
    if latest:
        curr_map = {e.id: e for e in entities}
        for prev in prev_entities:
            curr = curr_map.get(prev.get("id"))
            if curr and (prev.get("name_cn") != curr.name_cn or prev.get("type") != curr.type):
                modified += 1

    # 正规模型（图谱编辑器 fo_* 模式层）快照 + 差异
    # Sentinel 没有独立 publish 端点：本体发布是它的唯一上线边界。
    # 先提升 enabled 定义，再做快照，保证版本记录与实际运行状态一致。
    for sentinel in db.query(Sentinel).filter(
            Sentinel.ontology_id == ontology_id,
            Sentinel.origin == "release_builtin").all():
        sentinel.status = "published"
    db.flush()
    formal_snapshot = _snapshot_formal(db, ontology_id)
    formal_diff = _diff_formal(latest.snapshot_formal if latest else None, formal_snapshot)

    version = OntologyVersion(
        id=str(uuid.uuid4()),
        ontology_id=ontology_id,
        version_number=new_version,
        version_label=body.get("version_label", ""),
        description=body.get("description", ""),
        snapshot_entities=[{
            "id": e.id, "name_cn": e.name_cn, "name_en": e.name_en,
            "type": e.type, "description": e.description, "confidence": e.confidence,
            "properties": e.properties or {},
        } for e in entities],
        snapshot_relations=[{
            "id": r.id, "source_entity": r.source_entity,
            "target_entity": r.target_entity, "type": r.type,
            "confidence": r.confidence, "properties": r.properties or {},
        } for r in relations],
        snapshot_logic=[{
            "id": lr.id, "name_cn": lr.name_cn, "formula": lr.formula,
            "enabled": lr.enabled, "status": lr.status,
        } for lr in logic_rules],
        snapshot_actions=[{
            "id": a.id, "name_cn": a.name_cn,
            "enabled": a.enabled, "status": a.status,
        } for a in actions],
        snapshot_formal=formal_snapshot,
        parent_version_id=latest.id if latest else None,
        base_release_id=latest.id if latest else None,
        node_kind="release", lifecycle_status="released", revision=0,
        snapshot_hash=snapshot_hash(formal_snapshot),
        published_at=datetime.now(timezone.utc),
        change_summary={
            "added": added, "modified": modified, "deleted": deleted,
            "formal": formal_diff,
        },
        created_by=current_user.id,
    )
    db.add(version)

    # Persist the FK target before switching the project's release pointer.
    # PostgreSQL otherwise may execute the project UPDATE before this INSERT
    # because these models are connected only through scalar FK values rather
    # than an ORM relationship.
    db.flush()

    # 更新项目版本号
    project.version = new_version
    # 发布版本后，项目状态同步为"已发布"
    project.status = "published"
    project.current_release_id = version.id

    # 记录审计
    audit = AuditLog(
        id=str(uuid.uuid4()),
        ontology_id=ontology_id,
        event_type="publish",
        event_subtype="version_created",
        user_id=current_user.id,
        user_name=current_user.username,
        description=f"创建版本 {new_version}",
        object_type="ontology_version",
        object_id=version.id,
        meta={"version_number": new_version},
    )
    db.add(audit)
    db.commit()

    return {"data": {
        "id": version.id,
        "version_number": new_version,
        "change_summary": version.change_summary,
        "query_projection": projection_check,
    }}


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
    """撤回发布态，回到可编辑 draft；版本快照与运行历史保留。"""
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).with_for_update().first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    if project.status != "published":
        raise HTTPException(409, detail={
            "code": "invalid_unpublish_state",
            "message": f"只有 published 本体可撤回，当前状态为 {project.status}",
        })
    project.status = "draft"
    for sentinel in db.query(Sentinel).filter(
            Sentinel.ontology_id == ontology_id,
            Sentinel.origin == "release_builtin").all():
        sentinel.status = "draft"
    db.add(AuditLog(
        id=str(uuid.uuid4()),
        ontology_id=ontology_id,
        event_type="unpublish",
        event_subtype="version_withdrawn",
        user_id=current_user.id,
        user_name=current_user.username,
        description=f"撤回本体发布版本 {project.version}",
        object_type="ontology",
        object_id=ontology_id,
        meta={"version_number": project.version},
    ))
    db.commit()
    return {"data": {
        "id": ontology_id,
        "status": "draft",
        "version_number": project.version,
    }}


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
    """回滚到指定版本"""
    v = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).first()
    if not v:
        raise HTTPException(404, "Version not found")
    if v.node_kind == "draft":
        raise HTTPException(409, detail={
            "code": "draft_cannot_rollback",
            "message": "草稿不能成为运行版本；请先完成试跑并晋级",
        })

    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).with_for_update().first()
    if not project:
        raise HTTPException(404, "Ontology not found")

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

        formal_restored = None
        if v.snapshot_formal is not None:
            formal_restored = _restore_formal_snapshot(
                db, ontology_id, dict(v.snapshot_formal or {}))

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

        project.version = v.version_number
        project.status = "published"
        project.current_release_id = v.id
        db.add(AuditLog(
            id=str(uuid.uuid4()),
            ontology_id=ontology_id,
            event_type="rollback",
            user_id=current_user.id,
            user_name=current_user.username,
            description=f"回滚到版本 {v.version_number}",
            object_type="ontology",
            object_id=ontology_id,
            meta={"version_id": version_id, "version_number": v.version_number},
        ))
        db.commit()
    except HTTPException:
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

    projection_check = _rebuild_required_query_projections(db, ontology_id)
    if settings.environment == "production" and not projection_check["ready"]:
        # Relational rollback is complete, but a published runtime must never
        # advertise readiness while graph/search still expose another release.
        project = db.query(OntologyProject).filter(
            OntologyProject.id == ontology_id).with_for_update().first()
        if project is not None:
            project.status = "draft"
        for sentinel in db.query(Sentinel).filter(
                Sentinel.ontology_id == ontology_id,
                Sentinel.origin == "release_builtin").all():
            sentinel.status = "draft"
        db.commit()
        raise HTTPException(503, detail={
            "code": "rollback_projection_not_ready",
            "message": "关系型回滚已完成，但 Neo4j/Chroma 重建失败；本体已保持 draft，重试回滚后方可上线",
            "projection": projection_check,
        })

    return {"data": {
        "version_number": v.version_number,
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
