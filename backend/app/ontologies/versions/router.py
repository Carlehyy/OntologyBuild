"""本体版本化路由 — 版本历史 / diff / 回滚"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
import uuid
from app.deps import get_db, require_admin
from app.config import settings
from app.models.ontology_version import OntologyVersion, OntologyChangeLog
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
from app.ontologies.formal_modeling import schemas as FS
from app.ontologies.formal_modeling.validation import validate_model
from app.ontologies.access import ontology_access_guard

router = APIRouter(dependencies=[Depends(ontology_access_guard)])


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
        if dataset.kind != "curated":
            errors.append(_gate_error(
                "mapping_dataset_not_curated", "mapping",
                f"Mapping「{label}」必须消费已治理的 curated 数据集，当前 kind={dataset.kind}",
                item_id=mid, name=label, field="curatedDatasetId"))
        latest = db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == dataset.id,
        ).order_by(DatasetVersion.version_no.desc()).first()
        if latest is None:
            errors.append(_gate_error(
                "mapping_dataset_version_missing", "mapping",
                f"Mapping「{label}」绑定的数据集没有可发布版本",
                item_id=mid, name=label, field="curatedDatasetId"))
            continue
        if not latest.storage_uri or not latest.checksum:
            errors.append(_gate_error(
                "mapping_dataset_version_unverifiable", "mapping",
                f"Mapping「{label}」的数据版本缺少 storage_uri/checksum",
                item_id=mid, name=label, field="curatedDatasetId"))
        if dataset.latest_version_id != latest.id:
            errors.append(_gate_error(
                "dataset_latest_pointer_stale", "mapping",
                f"数据集「{dataset.name}」的 latest_version_id 未指向最新 v{latest.version_no}",
                item_id=mid, name=label, field="curatedDatasetId"))
        review = db.query(CuratedReview).filter(
            CuratedReview.curated_dataset_id == dataset.id,
            CuratedReview.dataset_version_id == latest.id,
        ).order_by(CuratedReview.created_at.desc()).first()
        if review is None or review.status != "approved":
            errors.append(_gate_error(
                "latest_dataset_version_not_approved", "mapping",
                f"Mapping「{label}」的最新数据版本 v{latest.version_no} 未获得当前 approved 审批",
                item_id=mid, name=label, field="curatedDatasetId"))
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
            if dataset.kind != "curated":
                errors.append(_gate_error(
                    "link_mapping_dataset_not_curated", "linkMapping",
                    f"LinkMapping「{label}」的 {role} 数据集必须为 curated",
                    item_id=lid, name=label, field=f"{role}DatasetId"))
            latest = db.query(DatasetVersion).filter(
                DatasetVersion.dataset_id == dataset_id,
            ).order_by(DatasetVersion.version_no.desc()).first()
            if latest is None:
                errors.append(_gate_error(
                    "link_mapping_dataset_version_missing", "linkMapping",
                    f"LinkMapping「{label}」的 {role} 数据集没有版本",
                    item_id=lid, name=label, field=f"{role}DatasetId"))
                continue
            if not latest.storage_uri or not latest.checksum:
                errors.append(_gate_error(
                    "link_mapping_version_unverifiable", "linkMapping",
                    f"LinkMapping「{label}」的 {role} 版本缺少 storage_uri/checksum",
                    item_id=lid, name=label, field=f"{role}DatasetId"))
            review = db.query(CuratedReview).filter(
                CuratedReview.curated_dataset_id == dataset_id,
                CuratedReview.dataset_version_id == latest.id,
            ).order_by(CuratedReview.created_at.desc()).first()
            if review is None or review.status != "approved":
                errors.append(_gate_error(
                    "link_mapping_version_not_approved", "linkMapping",
                    f"LinkMapping「{label}」的 {role} 最新版本 v{latest.version_no} 未审批",
                    item_id=lid, name=label, field=f"{role}DatasetId"))
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

    sentinels = q(Sentinel)
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
        "sentinels": [_snapshot_sentinel(x) for x in q(Sentinel)],
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
    total = db.query(OntologyVersion).filter(
        OntologyVersion.ontology_id == ontology_id
    ).count()
    versions = db.query(OntologyVersion).filter(
        OntologyVersion.ontology_id == ontology_id
    ).order_by(desc(OntologyVersion.created_at)).offset(offset).limit(limit).all()
    return {"data": [{
        "id": v.id,
        "version_number": v.version_number,
        "version_label": v.version_label,
        "description": v.description,
        "change_summary": v.change_summary or {},
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    } for v in versions], "total": total, "limit": limit, "offset": offset}


@router.post("/{ontology_id}/versions", status_code=201)
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

    # 计算新版本号
    latest = db.query(OntologyVersion).filter(
        OntologyVersion.ontology_id == ontology_id
    ).order_by(desc(OntologyVersion.created_at)).first()

    if latest:
        # 简单语义化：v{major}.{minor}.{patch}
        parts = latest.version_number.replace("v", "").split(".")
        try:
            major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
            minor += 1
            new_version = f"v{major}.{minor}.0"
        except (ValueError, IndexError):
            new_version = f"v1.0.0"
    else:
        new_version = "v1.0.0"

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
            Sentinel.ontology_id == ontology_id).all():
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
        change_summary={
            "added": added, "modified": modified, "deleted": deleted,
            "formal": formal_diff,
        },
        created_by=current_user.id,
    )
    db.add(version)

    # 更新项目版本号
    project.version = new_version
    # 发布版本后，项目状态同步为"已发布"
    project.status = "published"

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
        "id": v.id,
        "version_number": v.version_number,
        "version_label": v.version_label,
        "description": v.description,
        "change_summary": v.change_summary or {},
        "snapshot": {
            "entities": v.snapshot_entities or [],
            "relations": v.snapshot_relations or [],
            "logic": v.snapshot_logic or [],
            "actions": v.snapshot_actions or [],
            "formal": v.snapshot_formal or None,
        },
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }}


@router.post("/{ontology_id}/unpublish")
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
            Sentinel.ontology_id == ontology_id).all():
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
        db.query(SentinelMatchState).filter(
            SentinelMatchState.ontology_id == ontology_id,
        ).delete(synchronize_session=False)
        db.query(Sentinel).filter(Sentinel.ontology_id == ontology_id).delete(
            synchronize_session=False)
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
                Sentinel.ontology_id == ontology_id).all():
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
