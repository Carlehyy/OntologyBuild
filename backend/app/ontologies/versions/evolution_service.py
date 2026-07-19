"""本体版本树、影响分析与隔离试跑的纯服务层。

这里刻意不写生产 ObjectInstance/LinkInstance，也不执行 Action。试跑的唯一
副作用是写入 ontology_trial_* 隔离表，因而可以安全使用真实数据湖版本。
"""
from __future__ import annotations

import hashlib
import itertools
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.mapping import OntologyMapping
from app.ontologies.formal_modeling import schemas as FS
from app.ontologies.formal_modeling.safe_eval import SafeEvalError, safe_eval
from app.ontologies.formal_modeling.validation import validate_model
from app.ontologies.mappings.mapping_service import (
    MappingSourceError, load_mapping_source_rows,
)
from app.ontologies.versions.models import (
    OntologyTrialLink, OntologyTrialObject, OntologyTrialRun,
)


SNAPSHOT_KEYS = (
    "objectTypes", "linkTypes", "actions", "functions",
    "sentinels", "mappings", "linkMappings",
)
MAX_SENTINEL_TUPLES = 1000


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def complete_snapshot(snapshot: dict | None) -> dict:
    """历史快照也归一为包含全部集合的完整结构。"""
    source = snapshot or {}
    return {key: json_safe(source.get(key) or []) for key in SNAPSHOT_KEYS}


def canonical_snapshot(snapshot: dict | None) -> dict:
    normalized = complete_snapshot(snapshot)
    for key in SNAPSHOT_KEYS:
        normalized[key] = sorted(
            normalized[key],
            key=lambda item: str(item.get("id") or item.get("name") or ""),
        )
    return normalized


def snapshot_hash(snapshot: dict | None) -> str:
    payload = json.dumps(
        canonical_snapshot(snapshot), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def next_draft_number(parent_number: str, sibling_numbers: list[str]) -> str:
    prefix = f"{parent_number}."
    used: list[int] = []
    for number in sibling_numbers:
        if not number.startswith(prefix):
            continue
        tail = number[len(prefix):]
        if tail.isdigit():
            used.append(int(tail))
    return f"{parent_number}.{max(used, default=0) + 1}"


def next_release_number(current_number: str | None) -> str:
    raw = str(current_number or "v0")
    head = raw.removeprefix("v").split(".", 1)[0]
    try:
        major = int(head)
    except ValueError:
        major = 0
    return f"v{major + 1}"


def snapshot_models(snapshot: dict) -> dict[str, list[SimpleNamespace]]:
    specs = (
        ("objectTypes", FS.ObjectTypeCreate),
        ("linkTypes", FS.LinkTypeCreate),
        ("actions", FS.ActionTypeCreate),
        ("functions", FS.FunctionCreate),
    )
    result: dict[str, list[SimpleNamespace]] = {}
    for key, schema in specs:
        values: list[SimpleNamespace] = []
        for raw in complete_snapshot(snapshot)[key]:
            parsed = schema.model_validate(raw)
            values.append(SimpleNamespace(
                id=str(raw.get("id") or ""),
                **parsed.model_dump(exclude_none=False),
            ))
        result[key] = values
    return result


def validate_snapshot(snapshot: dict, *, require_object_type: bool = True) -> list[dict]:
    try:
        models = snapshot_models(snapshot)
    except Exception as exc:  # Pydantic gives precise field details in the text.
        return [{
            "code": "invalid_snapshot_shape", "kind": "ontology",
            "id": "", "name": "", "message": str(exc),
        }]
    errors = validate_model(
        models["objectTypes"], models["linkTypes"], models["actions"],
        models["functions"], [], [],
    )
    if require_object_type and not models["objectTypes"]:
        errors.append({
            "code": "object_type_required", "kind": "ontology",
            "id": "", "name": "", "message": "试跑本体至少需要一个 ObjectType",
        })
    return errors


def workspace_snapshot(body: dict, previous: dict | None) -> dict:
    """把图谱编辑器 DTO 写回完整快照，同时保留独立维护的映射和哨兵。"""
    parsed = FS.SaveFullOntologyRequest.model_validate(body)
    now = datetime.now(timezone.utc).isoformat()

    def dump(items: list) -> list[dict]:
        out = []
        for item in items:
            data = item.model_dump(mode="json", by_alias=True, exclude_none=False)
            data["id"] = data.get("id") or str(uuid.uuid4())
            data.setdefault("createdAt", now)
            data["updatedAt"] = now
            out.append(data)
        return out

    old = complete_snapshot(previous)
    return {
        "objectTypes": dump(parsed.object_types),
        "linkTypes": dump(parsed.link_types),
        "actions": dump(parsed.actions),
        "functions": dump(parsed.functions),
        "sentinels": old["sentinels"],
        "mappings": old["mappings"],
        "linkMappings": old["linkMappings"],
    }


def impact_report(base: dict | None, candidate: dict | None) -> dict:
    """输出可审核、可哈希的结构影响；breaking 项不会藏在总计里。"""
    before = complete_snapshot(base)
    after = complete_snapshot(candidate)
    resources: dict[str, dict] = {}
    breaking: list[dict] = []

    for key in SNAPSHOT_KEYS:
        left = {str(item.get("id")): item for item in before[key]}
        right = {str(item.get("id")): item for item in after[key]}
        added_ids = sorted(right.keys() - left.keys())
        deleted_ids = sorted(left.keys() - right.keys())
        modified_ids = sorted(
            item_id for item_id in left.keys() & right.keys()
            if left[item_id] != right[item_id]
        )
        resources[key] = {
            "added": added_ids, "modified": modified_ids,
            "deleted": deleted_ids,
        }
        for item_id in deleted_ids:
            breaking.append({
                "code": "resource_deleted", "resource": key, "id": item_id,
                "name": left[item_id].get("displayName") or left[item_id].get("name"),
                "message": "已发布结构中的元素将被删除",
            })

    left_types = {str(item.get("id")): item for item in before["objectTypes"]}
    right_types = {str(item.get("id")): item for item in after["objectTypes"]}
    for type_id in sorted(left_types.keys() & right_types.keys()):
        old = left_types[type_id]
        new = right_types[type_id]
        old_props = {str(p.get("id") or p.get("name")): p for p in old.get("properties") or []}
        new_props = {str(p.get("id") or p.get("name")): p for p in new.get("properties") or []}
        for prop_id in sorted(old_props.keys() - new_props.keys()):
            breaking.append({
                "code": "property_deleted", "resource": "objectTypes",
                "id": type_id, "propertyId": prop_id,
                "name": old_props[prop_id].get("displayName") or old_props[prop_id].get("name"),
                "message": "旧事实中的属性将失去新版定义",
            })
        for prop_id in sorted(old_props.keys() & new_props.keys()):
            old_prop, new_prop = old_props[prop_id], new_props[prop_id]
            if old_prop.get("type") != new_prop.get("type"):
                breaking.append({
                    "code": "property_type_changed", "resource": "objectTypes",
                    "id": type_id, "propertyId": prop_id,
                    "from": old_prop.get("type"), "to": new_prop.get("type"),
                    "message": "属性类型变化可能使旧数据无法注入",
                })
            if not old_prop.get("required") and new_prop.get("required"):
                breaking.append({
                    "code": "property_became_required", "resource": "objectTypes",
                    "id": type_id, "propertyId": prop_id,
                    "message": "新增必填约束可能拒绝旧数据",
                })
        if old.get("primaryKey") != new.get("primaryKey"):
            breaking.append({
                "code": "primary_key_changed", "resource": "objectTypes",
                "id": type_id, "from": old.get("primaryKey"),
                "to": new.get("primaryKey"),
                "message": "主键变化会改变对象身份和关系端点",
            })

    totals = {
        name: sum(len(resources[key][name]) for key in SNAPSHOT_KEYS)
        for name in ("added", "modified", "deleted")
    }
    report = {
        "resources": resources, "total": totals,
        "breaking": breaking, "breakingCount": len(breaking),
    }
    report["impactHash"] = hashlib.sha256(json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()
    return report


def _pk_columns(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _row_identity(row: dict, pk: str) -> str | None:
    columns = _pk_columns(pk)
    if not columns or any(row.get(column) in (None, "") for column in columns):
        return None
    if len(columns) == 1:
        return f"{columns[0]}:{row.get(columns[0])}"
    return "composite_pk:" + json.dumps({
        "columns": columns, "values": [row.get(column) for column in columns],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _latest_version(db: Session, dataset_id: str) -> tuple[Dataset, DatasetVersion]:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if dataset is None:
        raise MappingSourceError(f"映射绑定的数据集 {dataset_id} 不存在")
    version = None
    if dataset.latest_version_id:
        version = db.query(DatasetVersion).filter(
            DatasetVersion.id == dataset.latest_version_id,
            DatasetVersion.dataset_id == dataset_id,
        ).first()
    if version is None:
        raise MappingSourceError(f"数据集「{dataset.name}」没有可固定的 latest 版本")
    return dataset, version


def _object_type_for_mapping(mapping: dict, object_types: dict[str, dict]) -> dict | None:
    target = mapping.get("targetObjectTypeId")
    if target and str(target) in object_types:
        return object_types[str(target)]
    entity_class = str(mapping.get("entityClass") or "")
    return next((item for item in object_types.values()
                 if item.get("name") == entity_class
                 or item.get("displayName") == entity_class), None)


def validate_release_mapping_contract(snapshot: dict | None) -> list[dict]:
    """Validate configured mappings without making them a publication prerequisite.

    A structure-only or partially mapped release is valid and simply materializes
    no data for its unmapped types.  Once a mapping is present, its identity,
    required fields and endpoint contract remain fail-closed.  This gate is
    snapshot-only so trial entry and promotion enforce the same definition.
    """
    snap = complete_snapshot(snapshot)
    errors: list[dict] = []
    object_types = {
        str(item.get("id") or ""): item
        for item in snap["objectTypes"]
        if item.get("id")
    }
    mappings_by_type: dict[str, list[dict]] = {}

    def label(item: dict) -> str:
        return str(item.get("displayName") or item.get("name") or item.get("id") or "")

    def stored_required_properties(item: dict) -> list[dict]:
        return [
            prop for prop in (item.get("properties") or [])
            if isinstance(prop, dict)
            and prop.get("name")
            and prop.get("required")
            and prop.get("source") != "computed"
            and not prop.get("computed")
        ]

    for mapping in snap["mappings"]:
        mapping_id = str(mapping.get("id") or "")
        mapping_name = str(mapping.get("entityClass") or mapping_id)
        target = _object_type_for_mapping(mapping, object_types)
        if target is None:
            errors.append({
                "code": "mapping_object_type_not_found", "kind": "mapping",
                "id": mapping_id, "name": mapping_name,
                "message": f"Mapping「{mapping_name}」未绑定有效的 ObjectType",
                "field": "targetObjectTypeId",
            })
            continue

        target_id = str(target.get("id") or "")
        mappings_by_type.setdefault(target_id, []).append(mapping)
        if not mapping.get("curatedDatasetId"):
            errors.append({
                "code": "mapping_dataset_missing", "kind": "mapping",
                "id": mapping_id, "name": mapping_name,
                "message": f"Mapping「{mapping_name}」未绑定数据集",
                "field": "curatedDatasetId",
            })

        field_mapping = mapping.get("fieldMapping")
        if not isinstance(field_mapping, dict):
            errors.append({
                "code": "mapping_field_mapping_invalid", "kind": "mapping",
                "id": mapping_id, "name": mapping_name,
                "message": f"Mapping「{mapping_name}」的字段映射必须是对象",
                "field": "fieldMapping",
            })
            field_mapping = {}
        mapped_properties = {
            str(target_name)
            for source_name, target_name in field_mapping.items()
            if not str(source_name).startswith("__")
            and isinstance(target_name, str) and target_name
        }
        required = {str(prop["name"]) for prop in stored_required_properties(target)}
        primary_key = target.get("primaryKey")
        primary_property = next((
            prop for prop in (target.get("properties") or [])
            if isinstance(prop, dict)
            and primary_key
            and (prop.get("id") == primary_key or prop.get("name") == primary_key)
        ), None)
        if primary_property and primary_property.get("name"):
            required.add(str(primary_property["name"]))
        for property_name in sorted(required - mapped_properties):
            errors.append({
                "code": "mapping_required_property_missing", "kind": "mapping",
                "id": mapping_id, "name": mapping_name,
                "message": (
                    f"Mapping「{mapping_name}」未覆盖 ObjectType「{label(target)}」"
                    f"的必需属性「{property_name}」"
                ),
                "field": property_name,
            })

    for object_type_id, object_type in object_types.items():
        if not object_type.get("primaryKey"):
            errors.append({
                "code": "object_type_primary_key_required", "kind": "objectType",
                "id": object_type_id, "name": label(object_type),
                "message": f"ObjectType「{label(object_type)}」未设置主键，不能进入试跑态",
                "field": "primaryKey",
            })

    link_types = {
        str(item.get("id") or ""): item
        for item in snap["linkTypes"]
        if item.get("id")
    }
    for mapping in snap["linkMappings"]:
        mapping_id = str(mapping.get("id") or "")
        mapping_name = str(mapping.get("relationType") or mapping_id)
        link_type_id = str(mapping.get("linkTypeId") or "")
        link_type = link_types.get(link_type_id)
        if link_type is None:
            errors.append({
                "code": "link_mapping_type_not_found", "kind": "linkMapping",
                "id": mapping_id, "name": mapping_name,
                "message": f"LinkMapping「{mapping_name}」未绑定有效的 LinkType",
                "field": "linkTypeId",
            })
            continue
        for field, text in (("srcDatasetId", "源端"), ("tgtDatasetId", "目标端"),
                            ("srcKey", "源端外键"), ("tgtKey", "目标端外键")):
            if not mapping.get(field):
                errors.append({
                    "code": "link_mapping_endpoint_missing", "kind": "linkMapping",
                    "id": mapping_id, "name": mapping_name,
                    "message": f"LinkMapping「{mapping_name}」缺少{text}配置",
                    "field": field,
                })

        source_type_id = str(link_type.get("sourceObjectTypeId") or "")
        target_type_id = str(link_type.get("targetObjectTypeId") or "")
        source_dataset_id = str(mapping.get("srcDatasetId") or "")
        target_dataset_id = str(mapping.get("tgtDatasetId") or "")
        if source_dataset_id and not any(
            str(item.get("curatedDatasetId") or "") == source_dataset_id
            for item in mappings_by_type.get(source_type_id, [])
        ):
            errors.append({
                "code": "link_mapping_source_object_mapping_missing",
                "kind": "linkMapping", "id": mapping_id, "name": mapping_name,
                "message": f"LinkMapping「{mapping_name}」的源端数据集没有对应的对象映射",
                "field": "srcDatasetId",
            })
        if target_dataset_id and not any(
            str(item.get("curatedDatasetId") or "") == target_dataset_id
            for item in mappings_by_type.get(target_type_id, [])
        ):
            errors.append({
                "code": "link_mapping_target_object_mapping_missing",
                "kind": "linkMapping", "id": mapping_id, "name": mapping_name,
                "message": f"LinkMapping「{mapping_name}」的目标端数据集没有对应的对象映射",
                "field": "tgtDatasetId",
            })

        field_mapping = mapping.get("fieldMapping")
        if not isinstance(field_mapping, dict):
            errors.append({
                "code": "link_mapping_field_mapping_invalid", "kind": "linkMapping",
                "id": mapping_id, "name": mapping_name,
                "message": f"LinkMapping「{mapping_name}」的字段映射必须是对象",
                "field": "fieldMapping",
            })
            field_mapping = {}
        mapped_properties = {
            str(property_name)
            for property_name, source_name in field_mapping.items()
            if not str(property_name).startswith("__")
            and isinstance(source_name, str) and source_name
        }
        required = {
            str(prop["name"]) for prop in stored_required_properties(link_type)
        }
        for property_name in sorted(required - mapped_properties):
            errors.append({
                "code": "link_mapping_required_property_missing",
                "kind": "linkMapping", "id": mapping_id, "name": mapping_name,
                "message": (
                    f"LinkMapping「{mapping_name}」未覆盖 LinkType「{label(link_type)}」"
                    f"的必需属性「{property_name}」"
                ),
                "field": property_name,
            })

    return errors


def _simulate_sentinels(snapshot: dict, objects: list[dict], links: list[dict]) -> list[dict]:
    by_type: dict[str, list[dict]] = {}
    for item in objects:
        by_type.setdefault(item["objectTypeId"], []).append(item)
    link_keys = {
        (item["linkTypeId"], item["sourceObjectId"], item["targetObjectId"])
        for item in links
    }
    results = []
    for sentinel in snapshot.get("sentinels") or []:
        bindings = sentinel.get("bindings") or []
        errors: list[str] = []
        matched = 0
        if not sentinel.get("enabled", True) or sentinel.get("muted", False):
            results.append({"id": sentinel.get("id"), "matched": 0, "errors": [], "skipped": True})
            continue
        candidates: list[list[dict]] = []
        for binding in bindings:
            filtered = []
            for obj in by_type.get(str(binding.get("objectTypeId")), []):
                try:
                    if not binding.get("filter") or safe_eval(
                        str(binding["filter"]),
                        {str(binding.get("alias")): obj["properties"], "obj": obj["properties"]},
                    ):
                        filtered.append(obj)
                except SafeEvalError as exc:
                    if len(errors) < 5:
                        errors.append(str(exc))
            candidates.append(filtered)
        for combo in itertools.islice(itertools.product(*candidates), MAX_SENTINEL_TUPLES):
            scope = {str(bindings[i].get("alias")): combo[i]["properties"] for i in range(len(combo))}
            ids = {str(bindings[i].get("alias")): combo[i]["objectId"] for i in range(len(combo))}
            valid_links = all(
                (str(link.get("linkTypeId")), ids.get(str(link.get("from"))),
                 ids.get(str(link.get("to")))) in link_keys
                for link in (sentinel.get("links") or [])
            )
            if not valid_links:
                continue
            try:
                if not sentinel.get("condition") or safe_eval(str(sentinel["condition"]), scope):
                    matched += 1
            except SafeEvalError as exc:
                if len(errors) < 5:
                    errors.append(str(exc))
        results.append({
            "id": sentinel.get("id"),
            "name": sentinel.get("displayName") or sentinel.get("name"),
            "matched": matched, "errors": errors,
            # 动作只展示计划，绝不在试跑执行外部副作用。
            "plannedActions": len(sentinel.get("actionIds") or []) * matched,
        })
    return results


def materialize_trial(db: Session, run: OntologyTrialRun, snapshot: dict) -> dict:
    """固定 latest 湖版本并构建隔离对象/关系；任何错误都 fail-closed。"""
    snap = complete_snapshot(snapshot)
    errors = validate_snapshot(snap)
    warnings: list[dict] = []
    object_types = {str(item.get("id")): item for item in snap["objectTypes"]}
    mapped_types: set[str] = set()
    objects: list[dict] = []
    links: list[dict] = []
    pinned: dict[str, dict] = {}
    # 一个资产可以同时映射到多个对象类型。端点行必须按
    # (dataset, object type) 精确索引，不能让后出现的映射覆盖前者。
    mapping_rows: dict[tuple[str, str], dict[str, Any]] = {}
    object_ids: set[str] = set()

    for raw_mapping in snap["mappings"]:
        mapping_id = str(raw_mapping.get("id") or "")
        dataset_id = str(raw_mapping.get("curatedDatasetId") or "")
        target = _object_type_for_mapping(raw_mapping, object_types)
        if target is None:
            errors.append({
                "code": "mapping_object_type_not_found", "kind": "mapping",
                "id": mapping_id, "name": raw_mapping.get("entityClass") or "",
                "message": "Mapping 绑定的 ObjectType 不存在",
            })
            continue
        if not dataset_id:
            errors.append({
                "code": "mapping_dataset_missing", "kind": "mapping",
                "id": mapping_id, "name": raw_mapping.get("entityClass") or "",
                "message": "Mapping 未绑定数据集",
            })
            continue
        try:
            dataset, version = _latest_version(db, dataset_id)
            mapping = OntologyMapping(
                id=mapping_id or str(uuid.uuid4()), ontology_id=run.ontology_id,
                curated_dataset_id=dataset_id,
                entity_class=str(raw_mapping.get("entityClass") or target.get("name") or ""),
                target_object_type_id=str(target.get("id")),
                field_mapping=json_safe(raw_mapping.get("fieldMapping") or {}),
                status=str(raw_mapping.get("status") or "draft"),
                confidence=raw_mapping.get("confidence"),
            )
            rows, loaded_version = load_mapping_source_rows(db, mapping, require_approved=True)
            if loaded_version is None or loaded_version.id != version.id:
                raise MappingSourceError("读取的数据版本与 latest 指针不一致")
        except Exception as exc:
            errors.append({
                "code": "mapping_source_unavailable", "kind": "mapping",
                "id": mapping_id, "name": raw_mapping.get("entityClass") or "",
                "message": str(exc),
            })
            continue

        pinned[dataset_id] = {
            "datasetId": dataset_id, "datasetName": dataset.name,
            "versionId": version.id, "versionNo": version.version_no,
            "checksum": version.checksum, "rowCount": len(rows),
        }
        field_map = raw_mapping.get("fieldMapping") or {}
        pk = field_map.get("__primary_key__") or (dataset.schema_json or {}).get("primary_key")
        if not _pk_columns(pk):
            errors.append({
                "code": "mapping_primary_key_missing", "kind": "mapping",
                "id": mapping_id, "name": raw_mapping.get("entityClass") or "",
                "message": "数据集没有稳定主键，无法证明对象身份",
            })
            continue
        mapped_types.add(str(target.get("id")))
        rows_with_ids: list[tuple[dict, str]] = []
        for index, row in enumerate(rows):
            identity = _row_identity(row, str(pk))
            if identity is None:
                errors.append({
                    "code": "mapping_primary_key_value_missing", "kind": "objectInstance",
                    "id": f"{mapping_id}:{index}", "name": raw_mapping.get("entityClass") or "",
                    "message": "数据行主键为空，无法生成稳定对象身份",
                })
                continue
            object_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{run.ontology_id}:{target.get('id')}:{identity}",
            ))
            if object_id in object_ids:
                errors.append({
                    "code": "duplicate_primary_key", "kind": "objectInstance",
                    "id": object_id, "name": raw_mapping.get("entityClass") or "",
                    "message": "数据集主键重复，多个数据行会覆盖同一对象",
                })
                continue
            object_ids.add(object_id)
            properties = {
                str(target_name): row.get(source_name)
                for source_name, target_name in field_map.items()
                if not str(source_name).startswith("__") and source_name in row
            }
            item = {
                "objectId": object_id, "objectTypeId": str(target.get("id")),
                "properties": properties, "sourceDatasetId": dataset_id,
                "sourceDatasetVersionId": version.id, "externalId": identity,
            }
            objects.append(item)
            rows_with_ids.append((row, object_id))
        mapping_rows[(dataset_id, str(target.get("id")))] = {
            "rows": rows_with_ids,
            "primaryKey": pk,
        }

    for object_type in snap["objectTypes"]:
        if str(object_type.get("id")) not in mapped_types:
            warnings.append({
                "code": "object_type_unmapped", "kind": "objectType",
                "id": object_type.get("id"),
                "message": "该对象类型没有数据映射，试跑中不会产生实例",
            })

    link_types = {str(item.get("id")): item for item in snap["linkTypes"]}
    for link_mapping in snap["linkMappings"]:
        link_type_id = str(link_mapping.get("linkTypeId") or "")
        if link_type_id not in link_types:
            errors.append({
                "code": "link_mapping_type_not_found", "kind": "linkMapping",
                "id": link_mapping.get("id") or "", "name": link_mapping.get("relationType") or "",
                "message": "LinkMapping 绑定的 LinkType 不存在",
            })
            continue
        link_type = link_types[link_type_id]
        src_dataset = str(link_mapping.get("srcDatasetId") or "")
        tgt_dataset = str(link_mapping.get("tgtDatasetId") or "")
        src_type_id = str(link_type.get("sourceObjectTypeId") or "")
        tgt_type_id = str(link_type.get("targetObjectTypeId") or "")
        src_materialization = mapping_rows.get((src_dataset, src_type_id))
        tgt_materialization = mapping_rows.get((tgt_dataset, tgt_type_id))
        if src_materialization is None or tgt_materialization is None:
            errors.append({
                "code": "link_mapping_endpoint_mapping_missing",
                "kind": "linkMapping", "id": link_mapping.get("id") or "",
                "name": link_mapping.get("relationType") or "",
                "message": "关系两端必须分别存在与对象类型匹配的数据映射",
            })
            continue
        src_rows = src_materialization["rows"]
        tgt_rows = tgt_materialization["rows"]
        src_key = str(link_mapping.get("srcKey") or "")
        tgt_key = str(link_mapping.get("tgtKey") or "")
        pairs: list[tuple[str, str, dict]] = []
        edge_dataset = link_mapping.get("edgeDatasetId")
        if edge_dataset:
            try:
                dataset, version = _latest_version(db, str(edge_dataset))
                transient = OntologyMapping(
                    id=f"edge:{link_mapping.get('id')}", ontology_id=run.ontology_id,
                    curated_dataset_id=str(edge_dataset), entity_class="__edge__",
                    field_mapping={}, status="draft",
                )
                edge_rows, loaded = load_mapping_source_rows(
                    db, transient, require_approved=True)
                if loaded is None or loaded.id != version.id:
                    raise MappingSourceError("关系数据版本与 latest 指针不一致")
                pinned[str(edge_dataset)] = {
                    "datasetId": str(edge_dataset), "datasetName": dataset.name,
                    "versionId": version.id, "versionNo": version.version_no,
                    "checksum": version.checksum, "rowCount": len(edge_rows),
                }
                # srcKey/tgtKey 是连接表的外键列；端点侧必须按各自对象映射
                # 的主键建立索引，不能错误地把连接表列名套到端点数据集。
                src_index = {
                    str(row.get(_pk_columns(src_materialization["primaryKey"])[0])): oid
                    for row, oid in src_rows
                    if len(_pk_columns(src_materialization["primaryKey"])) == 1
                }
                tgt_index = {
                    str(row.get(_pk_columns(tgt_materialization["primaryKey"])[0])): oid
                    for row, oid in tgt_rows
                    if len(_pk_columns(tgt_materialization["primaryKey"])) == 1
                }
                if not src_index and src_rows:
                    raise MappingSourceError("连接表暂不支持复合主键源端点")
                if not tgt_index and tgt_rows:
                    raise MappingSourceError("连接表暂不支持复合主键目标端点")
                fmap = link_mapping.get("fieldMapping") or {}
                for row in edge_rows:
                    source_id = src_index.get(str(row.get(src_key)))
                    target_id = tgt_index.get(str(row.get(tgt_key)))
                    if source_id and target_id:
                        props = {str(prop): row.get(column) for prop, column in fmap.items()
                                 if not str(prop).startswith("__") and column in row}
                        pairs.append((source_id, target_id, props))
            except Exception as exc:
                errors.append({
                    "code": "link_mapping_source_unavailable", "kind": "linkMapping",
                    "id": link_mapping.get("id") or "", "name": link_mapping.get("relationType") or "",
                    "message": str(exc),
                })
        else:
            target_index: dict[str, list[str]] = {}
            for row, oid in tgt_rows:
                target_index.setdefault(str(row.get(tgt_key)), []).append(oid)
            for row, source_id in src_rows:
                for target_id in target_index.get(str(row.get(src_key)), []):
                    pairs.append((source_id, target_id, {}))
        for source_id, target_id, properties in pairs:
            link_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{run.ontology_id}:{source_id}:{link_type_id}:{target_id}:{link_mapping.get('id')}",
            ))
            links.append({
                "linkId": link_id, "linkTypeId": link_type_id,
                "sourceObjectId": source_id, "targetObjectId": target_id,
                "properties": properties,
            })

    try:
        models = snapshot_models(snap)
        instance_models = [SimpleNamespace(
            id=item["objectId"], object_type_id=item["objectTypeId"],
            properties=item["properties"], computed={},
        ) for item in objects]
        link_models = [SimpleNamespace(
            id=item["linkId"], link_type_id=item["linkTypeId"],
            source_object_id=item["sourceObjectId"],
            target_object_id=item["targetObjectId"], properties=item["properties"],
        ) for item in links]
        errors.extend(validate_model(
            models["objectTypes"], models["linkTypes"], models["actions"],
            models["functions"], instance_models, link_models,
        ))
    except Exception as exc:
        errors.append({
            "code": "trial_contract_validation_failed", "kind": "ontology",
            "id": "", "name": "", "message": str(exc),
        })

    sentinel_results = _simulate_sentinels(snap, objects, links)
    for item in sentinel_results:
        for error in item.get("errors") or []:
            errors.append({
                "code": "sentinel_trial_error", "kind": "sentinel",
                "id": item.get("id") or "", "name": item.get("name") or "",
                "message": error,
            })

    # 仅通过的试跑保留可晋级的完整投影；失败试跑保留摘要和样例，避免把大量
    # 无效数据误认为可发布候选。
    if not errors:
        for item in objects:
            db.add(OntologyTrialObject(
                trial_run_id=run.id, object_id=item["objectId"],
                object_type_id=item["objectTypeId"], properties=item["properties"],
                source_dataset_id=item["sourceDatasetId"],
                source_dataset_version_id=item["sourceDatasetVersionId"],
                external_id=item["externalId"],
            ))
        for item in links:
            db.add(OntologyTrialLink(
                trial_run_id=run.id, link_id=item["linkId"],
                link_type_id=item["linkTypeId"],
                source_object_id=item["sourceObjectId"],
                target_object_id=item["targetObjectId"],
                properties=item["properties"],
            ))

    run.dataset_versions = list(pinned.values())
    result = {
        "counts": {
            "objects": len(objects), "links": len(links),
            "facts": sum(len(item["properties"]) for item in objects),
            "datasets": len(pinned),
        },
        "errors": errors, "warnings": warnings,
        "sentinels": sentinel_results,
        "samples": {"objects": objects[:10], "links": links[:10]},
        "actionsExecuted": 0,
        "sideEffects": "blocked",
    }
    run.result_json = json_safe(result)
    run.status = "passed" if not errors else "failed"
    run.completed_at = datetime.now(timezone.utc)
    return result
