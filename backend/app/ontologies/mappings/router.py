"""v2 Ontology Mapping API — 含 Link Mapping 手动配置"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional
from app.database import SessionLocal
from app.deps import get_current_user
from app.ontologies.access import ontology_access_guard

router = APIRouter(dependencies=[Depends(ontology_access_guard)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SuggestRequest(BaseModel):
    dataset_name: str
    columns: list[str]
    sample_rows: list[dict] = []
    ontology_domain: str = ""


class CreateMappingRequest(BaseModel):
    curated_dataset_id: str
    entity_class: str
    field_mapping: dict
    ignored_fields: list[str] = Field(default_factory=list)
    primary_key_column: Optional[str] = None
    property_mappings: Optional[list[dict]] = None
    confidence: float = 1.0
    # 人工绑定：灌入到图谱里已有的对象实体（空=按名匹配，再无则由数据生成新类型）
    target_object_type_id: Optional[str] = None
    # 审核通过后自动灌入本体（存入 field_mapping.__auto_apply_on_review__）
    auto_apply_on_review: bool = False
    # 人工数据集发布通过契约校验的新版本后自动做本体全量对账。
    auto_apply_on_version: bool = False


class UpdateMappingRequest(BaseModel):
    entity_class: Optional[str] = None
    field_mapping: Optional[dict] = None
    ignored_fields: Optional[list[str]] = None
    primary_key_column: Optional[str] = None
    target_object_type_id: Optional[str] = None
    auto_apply_on_review: Optional[bool] = None
    auto_apply_on_version: Optional[bool] = None


@router.post("/{ontology_id}/mappings/suggest")
def suggest_mapping(ontology_id: str, body: SuggestRequest, db: Session = Depends(get_db)):
    from app.services.v2.mapping.auto_mapper import AutoMapper
    mapper = AutoMapper(db)
    suggestion = mapper.suggest_field_mapping(
        body.dataset_name, body.columns, body.sample_rows, body.ontology_domain
    )
    return {
        "entity_class": suggestion.entity_class,
        "entity_class_cn": suggestion.entity_class_cn,
        "description": suggestion.description,
        "primary_key_column": suggestion.primary_key_column,
        "field_mappings": [
            {
                "column_name": fm.column_name,
                "property_name": fm.property_name,
                "property_type": fm.property_type,
                "confidence": fm.confidence,
                "reason": fm.reason,
            }
            for fm in suggestion.field_mappings
        ],
    }


def _validate_target_type(db: Session, ontology_id: str, type_id: Optional[str]):
    if not type_id:
        return None
    from app.models.ontology_formal import ObjectType
    ot = db.query(ObjectType).filter(
        ObjectType.id == type_id, ObjectType.ontology_id == ontology_id).first()
    if not ot:
        raise HTTPException(422, f"绑定的对象实体不存在: {type_id}")
    return ot


def _normal_mapping_type(raw: object) -> str:
    """Normalize lake and ontology type vocabularies for mapping contracts."""
    value = str(raw or "string").strip().lower().split("(", 1)[0]
    aliases = {
        "str": "string", "text": "string", "varchar": "string",
        "char": "string", "uuid": "string",
        "int": "number", "integer": "number", "bigint": "number",
        "smallint": "number", "float": "number", "double": "number",
        "decimal": "number", "decimal128": "number",
        "date": "datetime", "timestamp": "datetime", "time": "datetime",
        "bool": "boolean", "list": "array", "set": "array",
        "object": "json", "map": "json",
    }
    return aliases.get(value, value)


def _mapping_types_compatible(source_type: str, target_type: str) -> bool:
    """Allow a lake JSON column to feed an ontology array property.

    The manual-dataset contract deliberately stores nested lists as JSON, while
    the formal ontology vocabulary exposes them as ``array``.  Treating that
    lossless representation as incompatible made array properties impossible
    to map through the asset lake.
    """
    return source_type == target_type or (
        source_type == "json" and target_type == "array"
    )


def _dataset_column_types(db: Session, dataset_id: str) -> dict[str, str]:
    from app.models.v2.dataset import Dataset

    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if dataset is None:
        raise HTTPException(404, detail={
            "code": "mapping_dataset_not_found",
            "message": f"映射数据集不存在或尚未迁入资产湖：{dataset_id}",
        })
    schema = dataset.schema_json if isinstance(dataset.schema_json, dict) else {}
    return {
        str(column.get("name")): _normal_mapping_type(column.get("type"))
        for column in (schema.get("columns_typed") or [])
        if isinstance(column, dict) and column.get("name")
    }


def _assert_mapping_types_compatible(
    db: Session,
    dataset_id: str,
    target_type: object | None,
    field_mapping: dict,
) -> None:
    """Enforce source-to-property compatibility at the trusted API boundary.

    Older imported datasets may not yet carry ``columns_typed``.  Those remain
    readable for backwards compatibility; every lake-managed dataset with a
    typed contract is checked strictly.
    """
    if target_type is None:
        return
    source_types = _dataset_column_types(db, dataset_id)
    if not source_types:
        return
    properties = {
        str(item.get("name")): item
        for item in (getattr(target_type, "properties", None) or [])
        if isinstance(item, dict) and item.get("name")
    }
    failures: list[dict] = []
    for source, target in (field_mapping or {}).items():
        source_name, target_name = str(source), str(target)
        source_type = source_types.get(source_name)
        target_property = properties.get(target_name)
        if source_type is None:
            failures.append({
                "source": source_name, "target": target_name,
                "reason": "source_column_not_found",
            })
            continue
        if target_property is None:
            failures.append({
                "source": source_name, "target": target_name,
                "reason": "target_property_not_found",
            })
            continue
        target_type_name = _normal_mapping_type(target_property.get("type"))
        if not _mapping_types_compatible(source_type, target_type_name):
            failures.append({
                "source": source_name, "source_type": source_type,
                "target": target_name, "target_type": target_type_name,
                "reason": "type_mismatch",
            })
    if failures:
        raise HTTPException(422, detail={
            "code": "mapping_type_mismatch",
            "message": "字段映射包含不存在的字段或不兼容的数据类型，请修正后再保存。",
            "errors": failures,
        })


def _assert_link_mapping_types_compatible(
    db: Session, *, src_dataset_id: str, tgt_dataset_id: str,
    edge_dataset_id: str | None, src_key: str, tgt_key: str,
    link_type: object | None, field_mapping: dict,
) -> None:
    """Validate relation endpoint keys and edge properties before persistence."""
    from app.data_channel.datasets.lake_gate import split_pk

    if field_mapping and not edge_dataset_id:
        raise HTTPException(422, detail={
            "code": "edge_dataset_required_for_properties",
            "message": "关系属性必须来自同一张连接表；请先选择关系数据集再映射属性。",
        })

    src_types = _dataset_column_types(db, src_dataset_id)
    tgt_types = _dataset_column_types(db, tgt_dataset_id)
    edge_types = (
        _dataset_column_types(db, edge_dataset_id) if edge_dataset_id else None)
    failures: list[dict] = []

    def compare(source_name: str, source_type: str | None,
                target_name: str, target_type: str | None, role: str) -> None:
        # Untyped legacy assets remain operable; typed contracts are strict.
        if source_type is None or target_type is None:
            return
        if source_type != target_type:
            failures.append({
                "role": role, "source": source_name, "source_type": source_type,
                "target": target_name, "target_type": target_type,
                "reason": "type_mismatch",
            })

    src_pk = split_pk(_canonical_primary_key(db, src_dataset_id))[0]
    tgt_pk = split_pk(_canonical_primary_key(db, tgt_dataset_id))[0]
    if edge_types is not None:
        compare(src_key, edge_types.get(src_key), src_pk, src_types.get(src_pk),
                "source_endpoint")
        compare(tgt_key, edge_types.get(tgt_key), tgt_pk, tgt_types.get(tgt_pk),
                "target_endpoint")
    else:
        compare(src_key, src_types.get(src_key), tgt_key, tgt_types.get(tgt_key),
                "endpoint_join")

    if link_type is not None:
        properties = {
            str(item.get("name")): item
            for item in (getattr(link_type, "properties", None) or [])
            if isinstance(item, dict) and item.get("name")
        }
        property_source_types = edge_types or {}
        for target_property, source_column in (field_mapping or {}).items():
            target_name, source_name = str(target_property), str(source_column)
            source_type = property_source_types.get(source_name)
            target = properties.get(target_name)
            if edge_types is not None and source_type is None:
                failures.append({
                    "role": "edge_property", "source": source_name,
                    "target": target_name, "reason": "source_column_not_found",
                })
                continue
            if target is None:
                failures.append({
                    "role": "edge_property", "source": source_name,
                    "target": target_name, "reason": "target_property_not_found",
                })
                continue
            compare(source_name, source_type, target_name,
                    _normal_mapping_type(target.get("type")), "edge_property")

    if failures:
        raise HTTPException(422, detail={
            "code": "link_mapping_type_mismatch",
            "message": "关系映射的端点外键或关系属性类型不兼容，请修正后再保存。",
            "errors": failures,
        })


def _require_draft_ontology(db: Session, ontology_id: str) -> None:
    """发布后冻结 Mapping 结构；数据实例仍可通过 apply-from-dataset 更新。"""
    from app.models.ontology import OntologyProject
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).with_for_update().first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    if project.status != "draft":
        raise HTTPException(
            409,
            "本体已发布，Mapping/LinkMapping 结构已冻结；"
            "请先创建或切换到 draft 版本再维护映射")


def _lock_ontology(db: Session, ontology_id: str):
    """Lock an ontology for guarded non-structural mapping operations."""
    from app.models.ontology import OntologyProject
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id).with_for_update().first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    return project


def _validate_version_automation_policy(db: Session, dataset_id: str) -> None:
    from app.data_channel.datasets.version_events import (
        manual_dataset_automation_eligibility,
    )
    from app.models.v2.dataset import Dataset, DatasetVersion

    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    version = db.query(DatasetVersion).filter(
        DatasetVersion.dataset_id == dataset_id,
    ).order_by(DatasetVersion.version_no.desc()).first()
    if dataset is None:
        raise HTTPException(404, "Mapping dataset not found")
    eligible, reason = manual_dataset_automation_eligibility(dataset, version)
    if not eligible:
        raise HTTPException(409, detail={
            "code": "manual_version_automation_not_eligible",
            "message": (
                "仅具备主键契约和可校验不可变版本的人工数据集可开启版本后自动灌入；"
                f"当前不满足：{reason}"
            ),
        })


def _validate_link_version_automation_policy(
    db: Session, dataset_ids: set[str | None],
) -> None:
    """A link subscription may mix reviewed curated and governed manual inputs."""
    from app.models.v2.dataset import Dataset

    manual_ids: list[str] = []
    for dataset_id in dataset_ids - {None}:
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is None:
            raise HTTPException(404, f"Link mapping dataset not found: {dataset_id}")
        if dataset.kind != "curated":
            manual_ids.append(dataset_id)
    if not manual_ids:
        raise HTTPException(409, detail={
            "code": "manual_version_automation_not_applicable",
            "message": "关系映射没有人工数据依赖；Curated 数据请使用审核通过触发链路。",
        })
    for dataset_id in manual_ids:
        _validate_version_automation_policy(db, dataset_id)


def _reject_reserved_mapping_keys(value: Optional[dict], field_name: str) -> None:
    """System lineage keys are write-only for the mapping runtime."""
    reserved = sorted(
        str(key) for key in (value or {}) if str(key).startswith("__"))
    if reserved:
        raise HTTPException(422, detail={
            "code": "reserved_mapping_keys",
            "message": f"{field_name} 包含平台保留键，客户端不得写入",
            "keys": reserved,
        })


def _validate_user_field_mapping(value: dict, ignored_fields: list[str]) -> None:
    """字段必须一一对应；忽略列通过独立显式契约表达。"""
    ignored = [str(item).strip() for item in ignored_fields if str(item).strip()]
    if len(ignored) != len(set(ignored)):
        raise HTTPException(422, detail={
            "code": "duplicate_ignored_fields",
            "message": "ignored_fields 包含重复字段",
        })
    overlap = sorted(set(value) & set(ignored))
    if overlap:
        raise HTTPException(422, detail={
            "code": "mapped_and_ignored_fields",
            "message": "同一源字段不能同时映射和忽略",
            "fields": overlap,
        })
    targets = [str(target).strip() for target in value.values() if str(target).strip()]
    duplicates = sorted({target for target in targets if targets.count(target) > 1})
    if duplicates:
        raise HTTPException(422, detail={
            "code": "duplicate_mapping_targets",
            "message": "字段映射必须一一对应，多个源字段不能写入同一目标属性",
            "targets": duplicates,
        })


def _assert_ignored_fields_do_not_hide_identity(
    ignored_fields: list[str], declared_primary_key: str,
) -> None:
    from app.data_channel.datasets.lake_gate import split_pk

    hidden_identity = sorted(set(ignored_fields) & set(split_pk(declared_primary_key)))
    if hidden_identity:
        raise HTTPException(422, detail={
            "code": "primary_key_cannot_be_ignored",
            "message": "资产主键是实例身份契约，不能在本体映射中忽略",
            "fields": hidden_identity,
        })


def _canonical_primary_key(db: Session, dataset_id: str) -> str:
    """Return the asset-lake identity contract for a mapping source.

    A mapping is a consumer of a Dataset, not an independent schema authority.
    Keeping this lookup at the API boundary prevents callers from changing object
    identity while the lake, review diff and merge paths still use another key.
    """
    from app.data_channel.datasets.lake_gate import split_pk
    from app.models.v2.dataset import Dataset

    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if dataset is None:
        raise HTTPException(404, detail={
            "code": "mapping_dataset_not_found",
            "message": f"映射数据集不存在或尚未迁入资产湖：{dataset_id}",
        })
    schema = dataset.schema_json if isinstance(dataset.schema_json, dict) else {}
    columns = split_pk(schema.get("primary_key"))
    if not columns:
        raise HTTPException(400, detail={
            "code": "primary_key_required",
            "message": f"数据集「{dataset.name}」尚未声明主键契约，无法创建本体映射。"
                       "请先在数据资产湖维护主键。",
        })
    if len(columns) != len(set(columns)):
        raise HTTPException(400, detail={
            "code": "invalid_primary_key_contract",
            "message": f"数据集「{dataset.name}」的复合主键包含重复列，请先修复资产契约。",
        })
    return ",".join(columns)


def _assert_client_primary_key_matches(
        supplied: str | None, declared: str, dataset_id: str) -> None:
    """Accept the legacy request field only as an assertion, never an override."""
    if supplied is None:
        return
    from app.data_channel.datasets.lake_gate import split_pk

    normalized = ",".join(split_pk(supplied))
    if normalized != declared:
        raise HTTPException(400, detail={
            "code": "primary_key_contract_mismatch",
            "message": "映射主键必须与资产湖已声明主键完全一致，客户端不能覆盖数据身份契约。",
            "dataset_id": dataset_id,
            "declared_primary_key": declared,
            "supplied_primary_key": normalized,
        })


@router.post("/{ontology_id}/mappings")
def create_mapping(ontology_id: str, body: CreateMappingRequest, db: Session = Depends(get_db)):
    from app.services.v2.mapping.mapping_service import MappingService
    _require_draft_ontology(db, ontology_id)
    target_type = _validate_target_type(db, ontology_id, body.target_object_type_id)
    _reject_reserved_mapping_keys(body.field_mapping, "field_mapping")

    declared_pk = _canonical_primary_key(db, body.curated_dataset_id)
    _assert_client_primary_key_matches(
        body.primary_key_column, declared_pk, body.curated_dataset_id)
    _validate_user_field_mapping(body.field_mapping or {}, body.ignored_fields)
    _assert_ignored_fields_do_not_hide_identity(body.ignored_fields, declared_pk)
    _assert_mapping_types_compatible(
        db, body.curated_dataset_id, target_type, body.field_mapping or {})

    svc = MappingService(db)
    field_mapping = dict(body.field_mapping or {})
    if body.ignored_fields:
        field_mapping["__ignored_fields__"] = sorted(set(body.ignored_fields))
    if body.property_mappings:
        field_mapping["__properties__"] = body.property_mappings
    if body.auto_apply_on_review:
        field_mapping["__auto_apply_on_review__"] = True
    if body.auto_apply_on_version:
        _validate_version_automation_policy(db, body.curated_dataset_id)
        field_mapping["__auto_apply_on_version__"] = True
    client_definition = {
        "entity_class": body.entity_class,
        "field_mapping": dict(body.field_mapping or {}),
        "ignored_fields": sorted(set(body.ignored_fields)),
        "auto_apply_on_review": bool(body.auto_apply_on_review),
        "auto_apply_on_version": bool(body.auto_apply_on_version),
        "target_object_type_id": body.target_object_type_id,
    }
    field_mapping["__client_definition__"] = client_definition
    from app.models.v2.mapping import OntologyMapping
    identity_query = db.query(OntologyMapping).filter(
        OntologyMapping.ontology_id == ontology_id,
        OntologyMapping.curated_dataset_id == body.curated_dataset_id,
    )
    identity_query = (
        identity_query.filter(
            OntologyMapping.target_object_type_id == body.target_object_type_id)
        if body.target_object_type_id
        else identity_query.filter(
            OntologyMapping.target_object_type_id.is_(None),
            OntologyMapping.entity_class == body.entity_class)
    )
    existing = identity_query.first()
    if existing is not None:
        existing_map = dict(existing.field_mapping or {})
        existing_user = {
            key: value for key, value in existing_map.items()
            if not str(key).startswith("__")
        }
        candidate_user = {
            key: value for key, value in field_mapping.items()
            if not str(key).startswith("__")
        }
        same_definition = (
            existing_map.get("__client_definition__") == client_definition
        ) or (
            existing.entity_class == body.entity_class
            and existing_user == candidate_user
            and sorted(existing_map.get("__ignored_fields__") or [])
            == sorted(field_mapping.get("__ignored_fields__") or [])
            and bool(existing_map.get("__auto_apply_on_review__"))
            == bool(field_mapping.get("__auto_apply_on_review__"))
            and bool(existing_map.get("__auto_apply_on_version__"))
            == bool(field_mapping.get("__auto_apply_on_version__"))
        )
        if same_definition:
            return {
                "mapping_id": existing.id, "status": existing.status,
                "idempotent_replay": True,
            }
        raise HTTPException(409, detail={
            "code": "object_mapping_already_exists",
            "message": "该数据集到目标对象的映射已存在；请维护现有映射，不要重复创建。",
            "mapping_id": existing.id,
        })
    mapping = svc.create_mapping(
        ontology_id=ontology_id,
        curated_dataset_id=body.curated_dataset_id,
        entity_class=body.entity_class,
        field_mapping=field_mapping,
        primary_key_column=declared_pk,
        confidence=body.confidence,
        target_object_type_id=body.target_object_type_id,
    )
    return {"mapping_id": mapping.id, "status": mapping.status}


@router.put("/{ontology_id}/mappings/{mapping_id}")
def update_mapping(ontology_id: str, mapping_id: str, body: UpdateMappingRequest,
                   db: Session = Depends(get_db)):
    """映射维护：结构和版本化自动触发策略均通过 draft 发布。"""
    provided = body.model_fields_set
    structural_fields = {
        "entity_class", "field_mapping", "ignored_fields",
        "primary_key_column", "target_object_type_id",
    }
    locked_project = None
    if provided & structural_fields:
        _require_draft_ontology(db, ontology_id)
    else:
        locked_project = _lock_ontology(db, ontology_id)
    from app.models.v2.mapping import OntologyMapping
    m = db.query(OntologyMapping).filter(
        OntologyMapping.id == mapping_id,
        OntologyMapping.ontology_id == ontology_id).first()
    if not m:
        raise HTTPException(404, "Mapping not found")
    policy_fields = {
        "auto_apply_on_review", "auto_apply_on_version",
    } & provided
    if locked_project is not None and locked_project.current_release_id:
        current_policy = {
            "auto_apply_on_review": bool(
                (m.field_mapping or {}).get("__auto_apply_on_review__")),
            "auto_apply_on_version": bool(
                (m.field_mapping or {}).get("__auto_apply_on_version__")),
        }
        requested_policy = {
            key: getattr(body, key)
            for key in policy_fields
            if getattr(body, key) is not None
        }
        changed_fields = sorted(
            key for key, value in requested_policy.items()
            if bool(value) != current_policy[key]
        )
        if changed_fields:
            raise HTTPException(409, detail={
                "code": "mapping_policy_requires_versioned_draft",
                "message": (
                    "当前发布映射的自动触发策略属于版本化行为，不能直接修改。"
                    "请新建本体草稿，完成试跑后再发布。"
                ),
                "fields": changed_fields,
                "current_release_id": locked_project.current_release_id,
            })
        # A repeated live request with exactly the released value is a true
        # no-op. Returning before client-definition bookkeeping prevents an
        # idempotent call from drifting away from the immutable release scope.
        return {
            "mapping_id": m.id,
            "status": m.status,
            "target_object_type_id": m.target_object_type_id,
            **current_policy,
            "idempotent_replay": True,
        }
    declared_pk = _canonical_primary_key(db, m.curated_dataset_id)
    _assert_client_primary_key_matches(
        body.primary_key_column, declared_pk, m.curated_dataset_id)
    candidate_target_type_id = (
        body.target_object_type_id
        if "target_object_type_id" in provided else m.target_object_type_id
    )
    target_type = _validate_target_type(
        db, ontology_id, candidate_target_type_id)
    fm = dict(m.field_mapping or {})
    previous_pk = fm.get("__primary_key__")
    if body.field_mapping is not None:
        _reject_reserved_mapping_keys(body.field_mapping, "field_mapping")
        # Preserve runtime-owned keys; user payloads containing them are rejected.
        sys_keys = {k: v for k, v in fm.items() if k.startswith("__")}
        fm = {**sys_keys, **body.field_mapping}
    effective_user_mapping = (
        body.field_mapping if body.field_mapping is not None
        else {k: v for k, v in fm.items() if not str(k).startswith("__")}
    )
    effective_ignored = (
        body.ignored_fields if body.ignored_fields is not None
        else list(fm.get("__ignored_fields__") or [])
    )
    _validate_user_field_mapping(effective_user_mapping, effective_ignored)
    _assert_ignored_fields_do_not_hide_identity(effective_ignored, declared_pk)
    _assert_mapping_types_compatible(
        db, m.curated_dataset_id, target_type, effective_user_mapping)
    m.target_object_type_id = candidate_target_type_id
    if body.entity_class is not None:
        m.entity_class = body.entity_class
    if body.ignored_fields is not None:
        if effective_ignored:
            fm["__ignored_fields__"] = sorted(set(effective_ignored))
        else:
            fm.pop("__ignored_fields__", None)
    # Always repair/read the canonical lake contract. ``primary_key_column`` is
    # retained in the request schema only for old clients and acts as an assert.
    fm["__primary_key__"] = declared_pk
    fm["__pk_source__"] = "lake"
    if body.auto_apply_on_review is not None:
        if body.auto_apply_on_review:
            fm["__auto_apply_on_review__"] = True
        else:
            fm.pop("__auto_apply_on_review__", None)
    if body.auto_apply_on_version is not None:
        if body.auto_apply_on_version:
            _validate_version_automation_policy(db, m.curated_dataset_id)
            fm["__auto_apply_on_version__"] = True
        else:
            fm.pop("__auto_apply_on_version__", None)
    if {
        "entity_class", "field_mapping", "ignored_fields",
        "target_object_type_id", "auto_apply_on_review", "auto_apply_on_version",
    } & provided:
        fm["__client_definition__"] = {
            "entity_class": m.entity_class,
            "field_mapping": {
                key: value for key, value in fm.items()
                if not str(key).startswith("__")
            },
            "ignored_fields": sorted(fm.get("__ignored_fields__") or []),
            "auto_apply_on_review": bool(fm.get("__auto_apply_on_review__")),
            "auto_apply_on_version": bool(fm.get("__auto_apply_on_version__")),
            "target_object_type_id": m.target_object_type_id,
        }
    projection_changed = bool({
        "entity_class", "field_mapping", "ignored_fields", "primary_key_column",
        "target_object_type_id",
    } & provided) or previous_pk != declared_pk
    if projection_changed:
        # Any definition change invalidates the previous apply attestation.  The
        # old marker must never be reused by the release gate for new semantics.
        for key in list(fm):
            if key.startswith("__applied_") or key == "__last_apply_error__":
                fm.pop(key, None)
        m.status = "draft"
    m.field_mapping = fm
    db.commit(); db.refresh(m)
    return {
        "mapping_id": m.id,
        "status": m.status,
        "target_object_type_id": m.target_object_type_id,
        "auto_apply_on_review": bool(fm.get("__auto_apply_on_review__")),
        "auto_apply_on_version": bool(fm.get("__auto_apply_on_version__")),
    }


@router.delete("/{ontology_id}/mappings/{mapping_id}", status_code=204)
def delete_mapping(ontology_id: str, mapping_id: str, db: Session = Depends(get_db)):
    """删除映射并撤销其当前态投影；不可变事实历史通过墓碑保留。"""
    _require_draft_ontology(db, ontology_id)
    from app.models.v2.mapping import OntologyMapping
    from app.services.v2.mapping.mapping_service import MappingApplyError, MappingService
    m = db.query(OntologyMapping).filter(
        OntologyMapping.id == mapping_id,
        OntologyMapping.ontology_id == ontology_id).first()
    if not m:
        raise HTTPException(404, "Mapping not found")
    svc = MappingService(db)
    try:
        stale_ids = svc.remove_mapping_projection(m)
        db.delete(m)
        db.commit()
    except MappingApplyError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    # Neo4j is a rebuildable read projection and is reconciled after truth commits.
    svc._delete_neo4j_entities(ontology_id, stale_ids)


@router.get("/{ontology_id}/mappings")
def list_mappings(ontology_id: str, db: Session = Depends(get_db)):
    from app.services.v2.mapping.mapping_service import MappingService
    from app.models.v2.dataset import Dataset
    from app.models.v2.curated import CuratedDataset
    from app.models.ontology_formal import ObjectType
    svc = MappingService(db)
    mappings = svc.get_mappings(ontology_id)
    # 绑定/同名解析结果预载：让前端直接显示"灌到哪个对象实体"
    ot_by_id = {o.id: o for o in db.query(ObjectType).filter(
        ObjectType.ontology_id == ontology_id).all()}
    ot_by_name = {}
    for o in ot_by_id.values():
        ot_by_name.setdefault(o.name, o)
        if o.display_name:
            ot_by_name.setdefault(o.display_name, o)
    result = []
    for m in mappings:
        dataset_name = None
        dataset_kind = None
        dataset_source = None
        row_count = None
        if m.curated_dataset_id:
            ds = db.query(Dataset).filter(Dataset.id == m.curated_dataset_id).first()
            if ds:
                dataset_name = ds.name
                dataset_kind = ds.kind
                schema = ds.schema_json if isinstance(ds.schema_json, dict) else {}
                dataset_source = schema.get("origin") or (
                    "sync" if ds.source_connection_id else "manual")
            else:
                cd = db.query(CuratedDataset).filter(CuratedDataset.id == m.curated_dataset_id).first()
                if cd:
                    dataset_name = cd.name
            from app.models.v2.dataset import DatasetVersion
            ver = db.query(DatasetVersion).filter(
                DatasetVersion.dataset_id == m.curated_dataset_id
            ).order_by(DatasetVersion.version_no.desc()).first()
            if ver:
                row_count = ver.rowcount
        # 解析该映射会灌到哪个对象实体（显式绑定 → 按名匹配 → 数据新建）
        bound_ot = ot_by_id.get(m.target_object_type_id) if m.target_object_type_id else None
        matched_ot = bound_ot or ot_by_name.get(m.entity_class)
        if bound_ot is not None:
            binding_mode = "bound"        # 人工绑定
        elif matched_ot is not None:
            binding_mode = "name_match"   # 自动按名复用
        else:
            binding_mode = "auto_create"  # 由数据生成新类型
        result.append({
            "id": m.id,
            "curated_dataset_id": m.curated_dataset_id,
            "dataset_name": dataset_name,
            "row_count": row_count,
            "entity_class": m.entity_class,
            "field_mapping": m.field_mapping,
            "property_mappings": (m.field_mapping or {}).get("__properties__", []),
            "status": m.status,
            "confidence": m.confidence,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "target_object_type_id": m.target_object_type_id,
            "binding_mode": binding_mode,
            "resolved_object_type": ({
                "id": matched_ot.id,
                "name": matched_ot.name,
                "display_name": matched_ot.display_name,
            } if matched_ot else None),
            "auto_apply_on_review": bool((m.field_mapping or {}).get("__auto_apply_on_review__")),
            "auto_apply_on_version": bool((m.field_mapping or {}).get("__auto_apply_on_version__")),
            "dataset_kind": dataset_kind,
            "dataset_source": dataset_source,
        })
    return result


@router.post("/{ontology_id}/mappings/{mapping_id}/apply")
def apply_mapping(ontology_id: str, mapping_id: str, data: list[dict], db: Session = Depends(get_db)):
    """已禁用的原始数据旁路。

    正式 Apply 必须从映射绑定的、可追溯的 DatasetVersion 读取；否则请求方可
    用任意 JSON 冒充资产湖数据，审批、checksum 与版本血缘全部失效。
    """
    from app.models.v2.mapping import OntologyMapping
    mapping = db.query(OntologyMapping).filter(
        OntologyMapping.id == mapping_id,
        OntologyMapping.ontology_id == ontology_id).first()
    if not mapping:
        raise HTTPException(404, "Mapping not found in this ontology")
    raise HTTPException(
        409,
        "禁止直接提交原始数据执行映射；请使用 apply-from-dataset，"
        "由服务端读取已绑定且通过校验的数据版本")


@router.post("/{ontology_id}/mappings/{mapping_id}/apply-from-dataset")
def apply_mapping_from_dataset(ontology_id: str, mapping_id: str, db: Session = Depends(get_db)):
    from app.models.v2.mapping import OntologyMapping
    from app.services.v2.mapping.mapping_service import (
        MappingApplyError, MappingReleaseScopeError, MappingSourceError,
        MappingService)

    mapping = db.query(OntologyMapping).filter(
        OntologyMapping.id == mapping_id,
        OntologyMapping.ontology_id == ontology_id,
    ).first()
    if not mapping:
        raise HTTPException(404, "Mapping not found")
    if not mapping.curated_dataset_id:
        raise HTTPException(400, "Mapping has no curated_dataset_id")

    svc = MappingService(db)
    try:
        result = svc.build_all(ontology_id, require_approved=True)
        result["trigger_mapping_id"] = mapping_id
        return result
    except MappingSourceError as e:
        raise HTTPException(422, str(e))
    except MappingReleaseScopeError as e:
        raise HTTPException(409, detail={
            "code": "mapping_not_in_current_release",
            "message": str(e),
        })
    except MappingApplyError as e:
        raise HTTPException(500, str(e))


@router.post("/{ontology_id}/mappings/build-all")
def build_all_mappings(ontology_id: str, db: Session = Depends(get_db)):
    from app.services.v2.mapping.mapping_service import (
        MappingApplyError, MappingReleaseScopeError, MappingSourceError,
        MappingService)
    from app.models.v2.mapping import OntologyLinkMapping
    svc = MappingService(db)
    try:
        result = svc.build_all(ontology_id, require_approved=True)
        active_links = db.query(OntologyLinkMapping).filter(
            OntologyLinkMapping.ontology_id == ontology_id,
            OntologyLinkMapping.status == "active",
        ).count()
        inferred_links = db.query(OntologyLinkMapping).filter(
            OntologyLinkMapping.ontology_id == ontology_id,
            OntologyLinkMapping.status == "inferred",
        ).count()
        result["link_mappings_configured"] = active_links
        result["link_mappings_inferred"] = inferred_links
        return result
    except MappingSourceError as e:
        raise HTTPException(422, detail=str(e))
    except MappingReleaseScopeError as e:
        raise HTTPException(409, detail={
            "code": "mapping_not_in_current_release",
            "message": str(e),
        })
    except MappingApplyError as e:
        raise HTTPException(500, detail=str(e))
    except Exception as e:
        raise HTTPException(500, detail=str(e))


class LinkMappingCreate(BaseModel):
    src_dataset_id: str
    tgt_dataset_id: str
    relation_type: str
    src_key: str
    tgt_key: str
    # —— 胖关系（连接表 + 边属性）——
    # link_type_id: 绑定手绘 LinkType，令边属性名对齐其 properties schema（为空则按 relation_type 名匹配/自建）
    link_type_id: str | None = None
    # edge_dataset_id: 连接表/关系数据集。有值 → 胖关系（src_key/tgt_key/field_mapping 列均在连接表内）
    edge_dataset_id: str | None = None
    # field_mapping: {边属性名: 连接表列名}
    field_mapping: dict = {}
    auto_apply_on_version: bool = False


@router.post("/{ontology_id}/link-mappings")
def create_link_mapping(ontology_id: str, body: LinkMappingCreate, db: Session = Depends(get_db)):
    from app.models.v2.mapping import OntologyLinkMapping, OntologyMapping
    from app.models.ontology_formal import LinkType
    from app.data_channel.datasets.lake_gate import split_pk
    from app.services.v2.dataset_service import DatasetService
    _require_draft_ontology(db, ontology_id)
    _reject_reserved_mapping_keys(body.field_mapping, "field_mapping")
    if body.auto_apply_on_version:
        _validate_link_version_automation_policy(db, {
            body.src_dataset_id, body.tgt_dataset_id, body.edge_dataset_id,
        })
    link_type = None

    src_pk = split_pk(_canonical_primary_key(db, body.src_dataset_id))
    tgt_pk = split_pk(_canonical_primary_key(db, body.tgt_dataset_id))
    if len(src_pk) != 1 or len(tgt_pk) != 1:
        raise HTTPException(400, detail={
            "code": "composite_endpoint_fk_not_supported",
            "message": (
                "关系连接表当前只支持单列端点主键；复合主键必须先提供多列外键映射，"
                "平台不会把逗号拼接列名当成真实字段后静默生成 0 条关系。"
            ),
            "source_primary_key": src_pk,
            "target_primary_key": tgt_pk,
        })
    if body.link_type_id:
        link_type = db.query(LinkType).filter(
            LinkType.id == body.link_type_id,
            LinkType.ontology_id == ontology_id,
        ).first()
        if link_type is None:
            raise HTTPException(422, "绑定的 LinkType 不存在")
        # 一个 ObjectType 可以有多条候选数据映射。端点校验必须查询“对象 +
        # 本次所选数据集”的精确组合；先按对象 ``first()`` 再比较数据集会因
        # 查询顺序误拒绝同一对象的其他合法映射。
        src_mapping = db.query(OntologyMapping).filter(
            OntologyMapping.ontology_id == ontology_id,
            OntologyMapping.target_object_type_id == link_type.source_object_type_id,
            OntologyMapping.curated_dataset_id == body.src_dataset_id,
        ).first()
        tgt_mapping = db.query(OntologyMapping).filter(
            OntologyMapping.ontology_id == ontology_id,
            OntologyMapping.target_object_type_id == link_type.target_object_type_id,
            OntologyMapping.curated_dataset_id == body.tgt_dataset_id,
        ).first()
        if src_mapping is None or tgt_mapping is None:
            src_candidates = [row[0] for row in db.query(
                OntologyMapping.curated_dataset_id,
            ).filter(
                OntologyMapping.ontology_id == ontology_id,
                OntologyMapping.target_object_type_id == link_type.source_object_type_id,
            ).all()]
            tgt_candidates = [row[0] for row in db.query(
                OntologyMapping.curated_dataset_id,
            ).filter(
                OntologyMapping.ontology_id == ontology_id,
                OntologyMapping.target_object_type_id == link_type.target_object_type_id,
            ).all()]
            if not src_candidates or not tgt_candidates:
                raise HTTPException(409, detail={
                    "code": "link_endpoint_mapping_required",
                    "message": "请先分别为该关系的源对象和目标对象建立显式数据映射。",
                })
            raise HTTPException(409, detail={
                "code": "link_endpoint_dataset_mismatch",
                "message": "关系端点数据集必须与所选 LinkType 两端对象的映射完全一致。",
                "expected_src_dataset_ids": src_candidates,
                "expected_tgt_dataset_ids": tgt_candidates,
            })

    _assert_link_mapping_types_compatible(
        db,
        src_dataset_id=body.src_dataset_id,
        tgt_dataset_id=body.tgt_dataset_id,
        edge_dataset_id=body.edge_dataset_id,
        src_key=body.src_key,
        tgt_key=body.tgt_key,
        link_type=link_type,
        field_mapping=body.field_mapping or {},
    )

    existing_query = db.query(OntologyLinkMapping).filter(
        OntologyLinkMapping.ontology_id == ontology_id)
    if body.link_type_id:
        existing_query = existing_query.filter(
            OntologyLinkMapping.link_type_id == body.link_type_id)
    else:
        existing_query = existing_query.filter(
            OntologyLinkMapping.link_type_id.is_(None),
            OntologyLinkMapping.src_dataset_id == body.src_dataset_id,
            OntologyLinkMapping.tgt_dataset_id == body.tgt_dataset_id,
            OntologyLinkMapping.relation_type == body.relation_type,
        )
    existing_link = existing_query.first()
    if existing_link is not None:
        same_definition = (
            existing_link.src_dataset_id == body.src_dataset_id
            and existing_link.tgt_dataset_id == body.tgt_dataset_id
            and existing_link.edge_dataset_id == body.edge_dataset_id
            and existing_link.relation_type == body.relation_type
            and existing_link.src_key == body.src_key
            and existing_link.tgt_key == body.tgt_key
            and {
                key: value for key, value in dict(existing_link.field_mapping or {}).items()
                if not str(key).startswith("__")
            } == dict(body.field_mapping or {})
            and bool((existing_link.field_mapping or {}).get("__auto_apply_on_version__"))
            == bool(body.auto_apply_on_version)
        )
        if same_definition:
            return {
                "link_mapping_id": existing_link.id,
                "relation_type": existing_link.relation_type,
                "edge_dataset_id": existing_link.edge_dataset_id,
                "edge_properties": [
                    key for key in (existing_link.field_mapping or {})
                    if not str(key).startswith("__")
                ],
                "idempotent_replay": True,
            }
        raise HTTPException(409, detail={
            "code": "link_mapping_already_exists",
            "message": "该 LinkType 已存在关系映射；请维护现有映射，不要重复创建。",
            "link_mapping_id": existing_link.id,
        })

    svc = DatasetService(db)
    try:
        src_rows = svc.preview(body.src_dataset_id, None, limit=10000)
        tgt_rows = svc.preview(body.tgt_dataset_id, None, limit=10000)
    except Exception as e:
        raise HTTPException(400, f"Failed to preview datasets for link mapping: {e}")
    if not src_rows:
        raise HTTPException(400, "Source dataset has no rows")
    if not tgt_rows:
        raise HTTPException(400, "Target dataset has no rows")

    if body.edge_dataset_id:
        # —— 连接表胖关系：src_key/tgt_key 及 field_mapping 的列都在连接表内 ——
        try:
            edge_rows = svc.preview(body.edge_dataset_id, None, limit=10000)
        except Exception as e:
            raise HTTPException(400, f"Failed to preview edge dataset for link mapping: {e}")
        if not edge_rows:
            raise HTTPException(400, "Edge (junction) dataset has no rows")
        edge_cols = set(edge_rows[0].keys())
        if body.src_key not in edge_cols:
            raise HTTPException(400, f"Source FK '{body.src_key}' not found in edge dataset")
        if body.tgt_key not in edge_cols:
            raise HTTPException(400, f"Target FK '{body.tgt_key}' not found in edge dataset")
        missing = [c for c in (body.field_mapping or {}).values() if c not in edge_cols]
        if missing:
            raise HTTPException(400, f"Edge property columns not found in edge dataset: {missing}")
        # 两端外键只允许命中端点的 canonical 主键，不能跨所有列做模糊交集。
        src_all = {str(r.get(src_pk[0], "")).strip() for r in src_rows}
        tgt_all = {str(r.get(tgt_pk[0], "")).strip() for r in tgt_rows}
        match_count = sum(
            1 for er in edge_rows
            if str(er.get(body.src_key, "")).strip() in src_all
            and str(er.get(body.tgt_key, "")).strip() in tgt_all
        )
        if match_count == 0:
            raise HTTPException(400, "连接表两端外键未命中任何端点实体；请检查 FK 列选择")
    else:
        # —— 直连外键瘦关系（原行为不变）——
        if body.src_key not in src_rows[0]:
            raise HTTPException(400, f"Source key '{body.src_key}' not found in source dataset")
        if body.tgt_key not in tgt_rows[0]:
            raise HTTPException(400, f"Target key '{body.tgt_key}' not found in target dataset")
        tgt_values = {str(row.get(body.tgt_key, "")).strip() for row in tgt_rows if row.get(body.tgt_key)}
        match_count = sum(1 for row in src_rows if str(row.get(body.src_key, "")).strip() in tgt_values)
        if match_count == 0:
            raise HTTPException(400, "Link mapping produced 0 matches; choose different source/target keys")

    lm = OntologyLinkMapping(
        ontology_id=ontology_id,
        src_dataset_id=body.src_dataset_id,
        tgt_dataset_id=body.tgt_dataset_id,
        relation_type=body.relation_type,
        src_key=body.src_key,
        tgt_key=body.tgt_key,
        link_type_id=body.link_type_id,
        edge_dataset_id=body.edge_dataset_id,
        field_mapping={
            **(body.field_mapping or {}),
            **({"__auto_apply_on_version__": True}
               if body.auto_apply_on_version else {}),
        },
        status="active",
    )
    db.add(lm)
    db.commit()
    db.refresh(lm)
    return {"link_mapping_id": lm.id, "relation_type": lm.relation_type,
            "match_count": match_count, "edge_dataset_id": lm.edge_dataset_id,
            "edge_properties": list((body.field_mapping or {}).keys())}


@router.get("/{ontology_id}/link-mappings")
def list_link_mappings(ontology_id: str, db: Session = Depends(get_db)):
    from app.models.v2.mapping import OntologyLinkMapping
    links = db.query(OntologyLinkMapping).filter(
        OntologyLinkMapping.ontology_id == ontology_id
    ).all()
    return [{
        "id": l.id, "src_dataset_id": l.src_dataset_id, "tgt_dataset_id": l.tgt_dataset_id,
        "relation_type": l.relation_type, "src_key": l.src_key, "tgt_key": l.tgt_key,
        "status": l.status,
        "link_type_id": l.link_type_id, "edge_dataset_id": l.edge_dataset_id,
        "field_mapping": {
            key: value for key, value in (l.field_mapping or {}).items()
            if not str(key).startswith("__")
        },
        "auto_apply_on_version": bool(
            (l.field_mapping or {}).get("__auto_apply_on_version__")),
        "is_fat": bool(l.edge_dataset_id),
    } for l in links]


class LinkMappingPolicyUpdate(BaseModel):
    auto_apply_on_version: bool


@router.put("/{ontology_id}/link-mappings/{link_mapping_id}/automation")
def update_link_mapping_automation(
    ontology_id: str, link_mapping_id: str, body: LinkMappingPolicyUpdate,
    db: Session = Depends(get_db),
):
    """Update only the operational subscription; safe for published ontologies."""
    _lock_ontology(db, ontology_id)
    from app.models.v2.mapping import OntologyLinkMapping
    mapping = db.query(OntologyLinkMapping).filter(
        OntologyLinkMapping.id == link_mapping_id,
        OntologyLinkMapping.ontology_id == ontology_id,
    ).first()
    if mapping is None:
        raise HTTPException(404, "Link mapping not found")
    if body.auto_apply_on_version:
        _validate_link_version_automation_policy(db, {
            mapping.src_dataset_id, mapping.tgt_dataset_id,
            mapping.edge_dataset_id,
        })
    field_mapping = dict(mapping.field_mapping or {})
    if body.auto_apply_on_version:
        field_mapping["__auto_apply_on_version__"] = True
    else:
        field_mapping.pop("__auto_apply_on_version__", None)
    mapping.field_mapping = field_mapping
    db.commit()
    return {
        "link_mapping_id": mapping.id,
        "auto_apply_on_version": body.auto_apply_on_version,
    }


@router.delete("/{ontology_id}/link-mappings/{link_mapping_id}", status_code=204)
def delete_link_mapping(ontology_id: str, link_mapping_id: str, db: Session = Depends(get_db)):
    """删除关系映射并撤销当前态边；Link Fact 历史以墓碑保留。"""
    _require_draft_ontology(db, ontology_id)
    from app.models.v2.mapping import OntologyLinkMapping
    from app.services.v2.mapping.mapping_service import MappingApplyError, MappingService
    lm = db.query(OntologyLinkMapping).filter(
        OntologyLinkMapping.id == link_mapping_id,
        OntologyLinkMapping.ontology_id == ontology_id,
    ).first()
    if not lm:
        raise HTTPException(404, "Link mapping not found")
    svc = MappingService(db)
    try:
        svc.remove_link_mapping_projection(lm)
        db.delete(lm)
        db.commit()
    except MappingApplyError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    svc._rebuild_neo4j_projection(ontology_id)
    return None
