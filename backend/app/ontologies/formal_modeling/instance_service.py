"""Release-fenced instance browser, CRUD, and fact-history services."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from functools import wraps
import inspect
from typing import Callable, Optional

from fastapi import HTTPException
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.orm import Session

from app.models.ontology_formal import (
    LinkInstance,
    LinkType,
    ObjectInstance,
    ObjectType,
    PropertyFact,
)
from app.models.ontology_version import OntologyVersion
from app.models.v2.dataset import Dataset
from app.ontologies.formal_modeling.derived import recompute_instance_derived
from app.ontologies.formal_modeling.facts import (
    fact_order_clause,
    record_link_fact,
    record_object_presence,
    record_object_tombstone,
    record_property_facts,
)
from app.ontologies.formal_modeling.legacy_projection import (
    LegacyProjectionAdoptionError,
    adopt_legacy_projection,
    assess_legacy_projection,
)
from app.ontologies.formal_modeling.runtime_support import (
    _current_release_view,
    _fact_to_dict,
    _ok,
    _orm_view,
    _raise_validation_failed,
    _require_ontology,
)
from app.ontologies.formal_modeling.schema_authoring_service import (
    _reject_direct_runtime_data_write,
)
from app.ontologies.formal_modeling.validation import (
    validate_instance_contract,
    validate_link_instance_contract,
)
from app.ontologies.release_context import (
    current_release_context,
    runtime_release_identity,
)
from app.schemas import ontology_formal as S


def _projection_locked_writer(func):
    """Fence a canonical instance writer before it takes the project row."""
    signature = inspect.signature(func)

    @wraps(func)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        from app.ontologies.runtime_fence import _ontology_build_lock

        with _ontology_build_lock(
            bound.arguments["db"],
            bound.arguments["ontology_id"],
        ):
            return func(*args, **kwargs)

    return wrapped


def _commit_graph_mutation(
    db: Session,
    ontology_id: str,
    *,
    refresh=None,
) -> None:
    """Commit Formal truth with a durable fence, then validate Neo4j."""
    from app.ontologies.projection_state import (
        ProjectionRebuildError,
        mark_projecting,
        rebuild_after_commit,
    )

    mark_projecting(db, ontology_id)
    db.commit()
    if refresh is not None:
        db.refresh(refresh)
    try:
        rebuild_after_commit(db, ontology_id)
    except ProjectionRebuildError as exc:
        raise HTTPException(503, detail={
            "code": "ontology_projection_failed",
            "message": (
                "正规本体数据已提交，但 Neo4j 图投影失败；"
                "图读取已阻断，请执行图修复"
            ),
            "ontology_id": ontology_id,
        }) from exc

def list_instances(
        ontology_id: str,
        object_type_id: Optional[str],
        expected_release_id: Optional[str],
        db: Session):
    context = current_release_context(
        db, ontology_id, expected_release_id=expected_release_id)
    q = db.query(ObjectInstance).filter(ObjectInstance.ontology_id == ontology_id)
    if expected_release_id is not None or (context.project.status or "") == "published":
        q = q.filter(ObjectInstance.ontology_release_id == context.id)
    if object_type_id:
        q = q.filter(ObjectInstance.object_type_id == object_type_id)
    items = q.order_by(ObjectInstance.created_at.desc()).all()
    return _ok([S.ObjectInstanceOut.model_validate(x).model_dump(by_alias=True) for x in items])


def _release_catalog_item(items: list[dict], item_id: str, kind: str) -> dict:
    item = next(
        (candidate for candidate in items if str(candidate.get("id")) == item_id),
        None,
    )
    if item is None:
        raise HTTPException(404, detail={
            "code": "release_type_not_found",
            "message": f"当前发布版本中不存在该{kind}",
        })
    return item


def _instance_browser_release(release: OntologyVersion) -> dict:
    return {
        "id": release.id,
        "version": release.version_number,
        "publishedAt": release.published_at.isoformat() if release.published_at else None,
    }


def _instance_summary(instance: Optional[ObjectInstance], object_types: list[dict]) -> Optional[dict]:
    if instance is None:
        return None
    object_type = next(
        (item for item in object_types
         if str(item.get("id")) == instance.object_type_id),
        {},
    )
    properties = instance.properties or {}
    primary_key = object_type.get("primaryKey")
    schema_properties = object_type.get("properties") or []
    primary_property = next(
        (item for item in schema_properties
         if item.get("id") == primary_key or item.get("name") == primary_key),
        None,
    )
    candidates = []
    if primary_property and primary_property.get("name"):
        candidates.append(primary_property["name"])
    candidates.extend(["name", "title", "label", "displayName", "id"])
    label = next(
        (properties.get(name) for name in candidates
         if properties.get(name) not in (None, "")),
        instance.external_id or instance.id,
    )
    return {
        "id": instance.id,
        "objectTypeId": instance.object_type_id,
        "label": str(label),
        "externalId": instance.external_id,
    }


def _mapping_value(mapping: dict, camel: str, snake: str):
    """Read both current camelCase snapshots and legacy snake_case snapshots."""
    return mapping.get(camel) if camel in mapping else mapping.get(snake)


def _mapping_matches_object_type(mapping: dict, object_type: dict) -> bool:
    target_id = _mapping_value(
        mapping, "targetObjectTypeId", "target_object_type_id")
    if target_id:
        return str(target_id) == str(object_type.get("id"))
    entity_class = _mapping_value(mapping, "entityClass", "entity_class")
    if not entity_class:
        return False
    candidate = str(entity_class).strip().casefold()
    return candidate in {
        str(object_type.get(field) or "").strip().casefold()
        for field in ("id", "name", "displayName")
    }


def _mapping_matches_link_type(mapping: dict, link_type: dict) -> bool:
    target_id = _mapping_value(mapping, "linkTypeId", "link_type_id")
    if target_id:
        return str(target_id) == str(link_type.get("id"))
    relation_type = _mapping_value(mapping, "relationType", "relation_type")
    if not relation_type:
        return False
    candidate = str(relation_type).strip().casefold()
    return candidate in {
        str(link_type.get(field) or "").strip().casefold()
        for field in ("id", "name", "displayName")
    }


def _release_dataset_associations(db: Session, snapshot: dict) -> tuple[dict, dict]:
    """Build dataset lineage owned by the immutable release snapshot."""
    object_dataset_roles: dict[str, dict[str, set[str]]] = {}
    for object_type in snapshot["objectTypes"]:
        object_type_id = str(object_type.get("id") or "")
        if not object_type_id:
            continue
        roles: dict[str, set[str]] = {}
        for mapping in snapshot["mappings"]:
            if not _mapping_matches_object_type(mapping, object_type):
                continue
            dataset_id = _mapping_value(
                mapping, "curatedDatasetId", "curated_dataset_id")
            if dataset_id:
                roles.setdefault(str(dataset_id), set()).add("实体数据")
        object_dataset_roles[object_type_id] = roles

    link_dataset_roles: dict[str, dict[str, set[str]]] = {}
    link_role_fields = (
        ("srcDatasetId", "src_dataset_id", "源实体数据"),
        ("tgtDatasetId", "tgt_dataset_id", "目标实体数据"),
        ("edgeDatasetId", "edge_dataset_id", "关系数据"),
    )
    for link_type in snapshot["linkTypes"]:
        link_type_id = str(link_type.get("id") or "")
        if not link_type_id:
            continue
        roles: dict[str, set[str]] = {}
        for mapping in snapshot["linkMappings"]:
            if not _mapping_matches_link_type(mapping, link_type):
                continue
            for camel, snake, role in link_role_fields:
                dataset_id = _mapping_value(mapping, camel, snake)
                if dataset_id:
                    roles.setdefault(str(dataset_id), set()).add(role)
        link_dataset_roles[link_type_id] = roles

    dataset_ids = {
        dataset_id
        for type_roles in (*object_dataset_roles.values(), *link_dataset_roles.values())
        for dataset_id in type_roles
    }
    datasets = db.query(Dataset).filter(Dataset.id.in_(dataset_ids)).all() \
        if dataset_ids else []
    dataset_by_id = {str(item.id): item for item in datasets}

    def serialize(roles: dict[str, set[str]]) -> list[dict]:
        result = []
        for dataset_id, dataset_roles in roles.items():
            dataset = dataset_by_id.get(dataset_id)
            result.append({
                "id": dataset_id,
                "name": dataset.name if dataset else "数据集已不可用",
                "kind": dataset.kind if dataset else None,
                "roles": sorted(dataset_roles),
                "available": dataset is not None,
            })
        return sorted(result, key=lambda item: (item["name"], item["id"]))

    return (
        {type_id: serialize(roles) for type_id, roles in object_dataset_roles.items()},
        {type_id: serialize(roles) for type_id, roles in link_dataset_roles.items()},
    )


def instance_browser_catalog(ontology_id: str, db: Session):
    """Published schema tree plus counts from its current runtime projection."""
    context = current_release_context(db, ontology_id)
    release, snapshot = context.release, context.snapshot
    object_type_ids = {
        str(item.get("id")) for item in snapshot["objectTypes"] if item.get("id")
    }
    link_type_ids = {
        str(item.get("id")) for item in snapshot["linkTypes"] if item.get("id")
    }
    object_counts = dict(db.query(
        ObjectInstance.object_type_id, func.count(ObjectInstance.id),
    ).filter(
        ObjectInstance.ontology_id == ontology_id,
        ObjectInstance.ontology_release_id == release.id,
        ObjectInstance.object_type_id.in_(object_type_ids),
    ).group_by(ObjectInstance.object_type_id).all()) if object_type_ids else {}
    link_counts = dict(db.query(
        LinkInstance.link_type_id, func.count(LinkInstance.id),
    ).filter(
        LinkInstance.ontology_id == ontology_id,
        LinkInstance.ontology_release_id == release.id,
        LinkInstance.link_type_id.in_(link_type_ids),
    ).group_by(LinkInstance.link_type_id).all()) if link_type_ids else {}
    object_datasets, link_datasets = _release_dataset_associations(db, snapshot)
    legacy_projection = assess_legacy_projection(db, context)

    return _ok({
        "release": _instance_browser_release(release),
        "objectTypes": [
            {
                **item,
                "instanceCount": object_counts.get(str(item.get("id")), 0),
                "associatedDatasets": object_datasets.get(str(item.get("id")), []),
            }
            for item in snapshot["objectTypes"]
        ],
        "linkTypes": [
            {
                **item,
                "instanceCount": link_counts.get(str(item.get("id")), 0),
                "associatedDatasets": link_datasets.get(str(item.get("id")), []),
            }
            for item in snapshot["linkTypes"]
        ],
        "legacyProjection": legacy_projection.payload(),
    })


def instance_browser_adopt_legacy(
        ontology_id: str,
        body: S.AdoptLegacyProjectionRequest,
        db: Session,
        current_user):
    """Explicit admin-only repair; never weakens release-scoped reads."""
    _require_ontology(db, ontology_id, for_update=True)
    context = current_release_context(
        db, ontology_id, expected_release_id=body.expectedReleaseId)
    try:
        result = adopt_legacy_projection(
            db,
            context,
            expected_object_instances=body.expectedObjectInstances,
            expected_link_instances=body.expectedLinkInstances,
            actor=current_user,
        )
        db.commit()
    except LegacyProjectionAdoptionError as exc:
        db.rollback()
        raise HTTPException(409, detail={
            "code": exc.code,
            "message": exc.message,
            "legacyProjection": exc.assessment.payload(),
        }) from exc
    except Exception:
        db.rollback()
        raise
    return _ok(result)


def _json_value_match(column, term: str, param: str, db: Session):
    """关键字只匹配 JSON 文档的“值”，不匹配键名。

    用户搜的是表格里看得见的属性值；整段 JSON 文本匹配会让键名
    （如 order_id）也命中，产生“搜什么键名都全量命中”的噪声。
    PostgreSQL / SQLite 分别下发到各自的 JSON 值展开函数；其他方言
    回退为整段文本匹配（宽松但不错杀）。``param`` 是本次查询内唯一的
    bindparam 名，调用方负责区分同一语句里的多个值域条件。
    """
    pattern = f"%{term}%"
    dialect = db.get_bind().dialect.name
    table_name = column.class_.__table__.name
    column_name = column.property.columns[0].name
    if dialect == "postgresql":
        return text(
            f'EXISTS (SELECT 1 FROM json_each_text("{table_name}"."{column_name}")'
            f' AS jv("key", "value") WHERE jv."value" ILIKE :{param})'
        ).bindparams(**{param: pattern})
    if dialect == "sqlite":
        return text(
            f'EXISTS (SELECT 1 FROM json_each("{table_name}"."{column_name}")'
            f' WHERE lower(CAST(json_each."value" AS TEXT)) LIKE lower(:{param}))'
        ).bindparams(**{param: pattern})
    return cast(column, String).ilike(pattern)


def instance_browser_objects(
        ontology_id: str,
        object_type_id: str,
        page: int,
        page_size: int,
        keyword: Optional[str],
        db: Session):
    project = _require_ontology(db, ontology_id)
    release, snapshot = _current_release_view(db, project)
    _release_catalog_item(
        snapshot["objectTypes"], object_type_id, "对象实体",
    )
    query = db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology_id,
        ObjectInstance.ontology_release_id == release.id,
        ObjectInstance.object_type_id == object_type_id,
    )
    term = (keyword or "").strip()
    if term:
        pattern = f"%{term}%"
        query = query.filter(or_(
            ObjectInstance.id.ilike(pattern),
            ObjectInstance.external_id.ilike(pattern),
            ObjectInstance.source.ilike(pattern),
            _json_value_match(ObjectInstance.properties, term, "obj_prop_kw", db),
            _json_value_match(ObjectInstance.computed, term, "obj_comp_kw", db),
        ))
    total = query.count()
    items = query.order_by(
        ObjectInstance.updated_at.desc(), ObjectInstance.id.asc(),
    ).offset((page - 1) * page_size).limit(page_size).all()
    return _ok({
        "release": _instance_browser_release(release),
        "objectTypeId": object_type_id,
        "items": [
            S.ObjectInstanceOut.model_validate(item).model_dump(by_alias=True)
            for item in items
        ],
        "total": total,
        "page": page,
        "pageSize": page_size,
    })


def instance_browser_links(
        ontology_id: str,
        link_type_id: str,
        page: int,
        page_size: int,
        keyword: Optional[str],
        db: Session):
    project = _require_ontology(db, ontology_id)
    release, snapshot = _current_release_view(db, project)
    _release_catalog_item(snapshot["linkTypes"], link_type_id, "实体关系")
    query = db.query(LinkInstance).filter(
        LinkInstance.ontology_id == ontology_id,
        LinkInstance.ontology_release_id == release.id,
        LinkInstance.link_type_id == link_type_id,
    )
    term = (keyword or "").strip()
    if term:
        pattern = f"%{term}%"
        # 表格里端点显示的是业务标签（主键/名称等属性值或 external_id），
        # 用户按标签搜索时必须能命中，因此先解析出命中的端点实例再回过滤。
        endpoint_hits = select(ObjectInstance.id).where(
            ObjectInstance.ontology_id == ontology_id,
            ObjectInstance.ontology_release_id == release.id,
            or_(
                ObjectInstance.external_id.ilike(pattern),
                _json_value_match(ObjectInstance.properties, term, "ep_prop_kw", db),
            ),
        )
        query = query.filter(or_(
            LinkInstance.id.ilike(pattern),
            LinkInstance.source_object_id.ilike(pattern),
            LinkInstance.target_object_id.ilike(pattern),
            _json_value_match(LinkInstance.properties, term, "link_prop_kw", db),
            LinkInstance.source_object_id.in_(endpoint_hits),
            LinkInstance.target_object_id.in_(endpoint_hits),
        ))
    total = query.count()
    items = query.order_by(
        LinkInstance.created_at.desc(), LinkInstance.id.asc(),
    ).offset((page - 1) * page_size).limit(page_size).all()
    endpoint_ids = {
        endpoint_id for item in items
        for endpoint_id in (item.source_object_id, item.target_object_id)
    }
    endpoints = (db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology_id,
        ObjectInstance.ontology_release_id == release.id,
        ObjectInstance.id.in_(endpoint_ids),
    ).all()) if endpoint_ids else []
    endpoint_by_id = {item.id: item for item in endpoints}
    serialized = []
    for item in items:
        payload = S.LinkInstanceOut.model_validate(item).model_dump(by_alias=True)
        payload["sourceRelationId"] = item.source_relation_id
        payload["sourceObject"] = _instance_summary(
            endpoint_by_id.get(item.source_object_id), snapshot["objectTypes"])
        payload["targetObject"] = _instance_summary(
            endpoint_by_id.get(item.target_object_id), snapshot["objectTypes"])
        serialized.append(payload)
    return _ok({
        "release": _instance_browser_release(release),
        "linkTypeId": link_type_id,
        "items": serialized,
        "total": total,
        "page": page,
        "pageSize": page_size,
    })


@_projection_locked_writer
def create_instance(
        ontology_id: str,
        body: S.ObjectInstanceCreate,
        db: Session,
        current_user,
        *,
        reject_runtime_write_fn: Optional[Callable] = None):
    (reject_runtime_write_fn or _reject_direct_runtime_data_write)()
    project = _require_ontology(db, ontology_id, for_update=True)
    data = body.model_dump(exclude_none=True)
    data["id"] = data.get("id") or str(uuid.uuid4())
    release_identity = runtime_release_identity(db, ontology_id)
    data["ontology_release_id"] = (
        release_identity.id
        if release_identity is not None and (project.status or "") == "published"
        else None)
    obj = ObjectInstance(ontology_id=ontology_id, **data)
    object_types = db.query(ObjectType).filter(ObjectType.ontology_id == ontology_id).all()
    instances = db.query(ObjectInstance).filter(ObjectInstance.ontology_id == ontology_id).all()
    errors = validate_instance_contract(object_types, [*instances, obj], validate_ids={obj.id})
    _raise_validation_failed(errors, "实例创建被拒绝")
    db.add(obj); db.flush()
    record_object_presence(
        db,
        ontology_id=ontology_id,
        instance_id=obj.id,
        object_type_id=obj.object_type_id,
        source=obj.source or "manual",
        actor_id=getattr(current_user, "id", None),
        ontology_release_id=obj.ontology_release_id,
    )
    created = record_property_facts(
        db, ontology_id=ontology_id, instance_id=obj.id,
        object_type_id=obj.object_type_id,
        old_props=None, new_props=obj.properties or {},
        source=obj.source or "manual", actor_id=getattr(current_user, "id", None),
    )
    recompute_instance_derived(db, ontology_id=ontology_id, instance=obj, trigger_facts=created)
    project.updated_at = datetime.now(timezone.utc)
    _commit_graph_mutation(db, ontology_id, refresh=obj)
    return _ok(S.ObjectInstanceOut.model_validate(obj).model_dump(by_alias=True))


@_projection_locked_writer
def update_instance(
        ontology_id: str,
        instance_id: str,
        body: S.ObjectInstanceUpdate,
        db: Session,
        current_user,
        *,
        reject_runtime_write_fn: Optional[Callable] = None):
    (reject_runtime_write_fn or _reject_direct_runtime_data_write)()
    project = _require_ontology(db, ontology_id, for_update=True)
    obj = db.query(ObjectInstance).filter(ObjectInstance.id == instance_id,
                                          ObjectInstance.ontology_id == ontology_id).first()
    if not obj:
        raise HTTPException(404, "Not found")
    old_props = dict(obj.properties or {})
    updates = body.model_dump(exclude_unset=True)
    candidate = _orm_view(obj, updates)
    object_types = db.query(ObjectType).filter(ObjectType.ontology_id == ontology_id).all()
    instances = db.query(ObjectInstance).filter(ObjectInstance.ontology_id == ontology_id).all()
    merged_instances = [candidate if item.id == instance_id else item for item in instances]
    errors = validate_instance_contract(object_types, merged_instances, validate_ids={instance_id})
    _raise_validation_failed(errors, "实例更新被拒绝")
    for k, v in updates.items():
        setattr(obj, k, v)
    created = record_property_facts(
        db, ontology_id=ontology_id, instance_id=obj.id,
        object_type_id=obj.object_type_id,
        old_props=old_props, new_props=obj.properties or {},
        # 出处随实例真实来源：采集器/导入回写已存在实例时不再失真为 manual
        source=obj.source or "manual", actor_id=getattr(current_user, "id", None),
    )
    if created:
        recompute_instance_derived(db, ontology_id=ontology_id, instance=obj, trigger_facts=created)
    project.updated_at = datetime.now(timezone.utc)
    _commit_graph_mutation(db, ontology_id, refresh=obj)
    return _ok(S.ObjectInstanceOut.model_validate(obj).model_dump(by_alias=True))


def list_instance_facts(
        ontology_id: str,
        instance_id: str,
        property_name: Optional[str],
        limit: int,
        db: Session):
    """实例的属性级变更历史（Fact 溯源层），按时间倒序。"""
    _require_ontology(db, ontology_id)
    q = db.query(PropertyFact).filter(
        PropertyFact.ontology_id == ontology_id,
        PropertyFact.instance_id == instance_id,
    )
    if property_name:
        q = q.filter(PropertyFact.property_name == property_name)
    items = q.order_by(*fact_order_clause()).limit(limit).all()
    return _ok([_fact_to_dict(f) for f in items])


def instance_as_of(
        ontology_id: str,
        instance_id: str,
        t: str,
        db: Session):
    """时态回放：时刻 T 的实例投影 = recorded_at ≤ T 且未被 T 前事实 supersede 的
    每个属性最新事实。含存在性（墓碑事实之后视为已删除）。t 为 ISO 时间串。"""
    _require_ontology(db, ontology_id)
    try:
        cutoff = datetime.fromisoformat(t.replace("Z", "+00:00"))
        if cutoff.tzinfo is not None:
            cutoff = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(422, "t 必须是 ISO 8601 时间串")

    rows = (db.query(PropertyFact)
            .filter(PropertyFact.ontology_id == ontology_id,
                    PropertyFact.instance_id == instance_id,
                    PropertyFact.recorded_at <= cutoff)
            .order_by(*fact_order_clause())
            .all())
    # 每条同 kind/属性链是线性的（supersedes 单链），≤T 的最新事实即当时值。
    # object.exists 与业务属性名 "exists" 必须分开，否则新增的对象存在性事实
    # 会遮蔽一个完全合法的同名业务属性。
    latest: dict[tuple[str, str], PropertyFact] = {}
    for r in rows:
        latest.setdefault((r.kind or "property", r.property_name), r)

    existence_fact = latest.get(("object", "exists"))
    # Legacy instances can predate the Fact stream, so absence of an existence
    # fact normally means "present".  The one unambiguous exception is a chain
    # whose first Fact is an explicit creation after the requested cutoff.
    exists = True
    if existence_fact is None:
        first_existence = (
            db.query(PropertyFact)
            .filter(
                PropertyFact.ontology_id == ontology_id,
                PropertyFact.instance_id == instance_id,
                PropertyFact.kind == "object",
                PropertyFact.property_name == "exists",
            )
            .order_by(
                PropertyFact.recorded_at.asc(),
                PropertyFact.seq.asc(),
                PropertyFact.id.asc(),
            )
            .first()
        )
        if first_existence is not None and bool((first_existence.value or {}).get("v")):
            exists = False
    props: dict = {}
    computed: dict = {}
    detail: dict = {}
    for (_, name), f in latest.items():
        fact_value = f.value or {}
        v = fact_value.get("v")
        if f.kind == "object" and name == "exists":
            exists = bool(v)
            continue
        if f.kind == "decision":
            continue
        bucket = computed if f.kind == "derived" else props
        if fact_value.get("present", True) is False:
            bucket.pop(name, None)
            detail[name] = _fact_to_dict(f)
            continue
        bucket[name] = v
        detail[name] = _fact_to_dict(f)
    return _ok({
        "instanceId": instance_id, "asOf": t, "exists": exists,
        "properties": props, "computed": computed, "facts": detail,
        "totalFacts": len(rows),
    })


@_projection_locked_writer
def delete_instance(
        ontology_id: str,
        instance_id: str,
        db: Session,
        current_user,
        *,
        reject_runtime_write_fn: Optional[Callable] = None):
    (reject_runtime_write_fn or _reject_direct_runtime_data_write)()
    project = _require_ontology(db, ontology_id, for_update=True)
    obj = db.query(ObjectInstance).filter(ObjectInstance.id == instance_id,
                                          ObjectInstance.ontology_id == ontology_id).first()
    if not obj:
        raise HTTPException(404, "Not found")
    related = db.query(LinkInstance).filter(
        LinkInstance.ontology_id == ontology_id,
        ((LinkInstance.source_object_id == instance_id)
         | (LinkInstance.target_object_id == instance_id)),
    ).all()
    if related:
        _raise_validation_failed([{
            "code": "instance_in_use",
            "kind": "objectInstance",
            "name": "",
            "id": instance_id,
            "message": f"实例仍被 {len(related)} 条链接引用，请先删除相关链接",
        }], "实例删除被拒绝")
    # 墓碑事实：投影删除后，事实流仍能回答"它存在过、何时被谁删除"
    record_object_tombstone(
        db, ontology_id=ontology_id, instance_id=obj.id,
        object_type_id=obj.object_type_id, source="manual",
        actor_id=getattr(current_user, "id", None))
    project.updated_at = datetime.now(timezone.utc)
    db.delete(obj)
    _commit_graph_mutation(db, ontology_id)


# ============================================================
#  Link Instances
# ============================================================
def list_link_instances(
        ontology_id: str,
        expected_release_id: Optional[str],
        db: Session):
    context = current_release_context(
        db, ontology_id, expected_release_id=expected_release_id)
    query = db.query(LinkInstance).filter(LinkInstance.ontology_id == ontology_id)
    if expected_release_id is not None or (context.project.status or "") == "published":
        query = query.filter(LinkInstance.ontology_release_id == context.id)
    items = query.all()
    return _ok([S.LinkInstanceOut.model_validate(x).model_dump(by_alias=True) for x in items])


@_projection_locked_writer
def create_link_instance(
        ontology_id: str,
        body: S.LinkInstanceCreate,
        db: Session,
        current_user,
        *,
        reject_runtime_write_fn: Optional[Callable] = None):
    (reject_runtime_write_fn or _reject_direct_runtime_data_write)()
    project = _require_ontology(db, ontology_id, for_update=True)
    release_identity = runtime_release_identity(db, ontology_id)
    obj = LinkInstance(id=str(uuid.uuid4()), ontology_id=ontology_id,
                       ontology_release_id=(
                           release_identity.id
                           if release_identity and (project.status or "") == "published"
                           else None),
                       **body.model_dump(exclude_none=True))
    link_types = db.query(LinkType).filter(LinkType.ontology_id == ontology_id).all()
    instances = db.query(ObjectInstance).filter(ObjectInstance.ontology_id == ontology_id).all()
    links = db.query(LinkInstance).filter(LinkInstance.ontology_id == ontology_id).all()
    errors = validate_link_instance_contract(
        link_types, instances, [*links, obj], validate_ids={obj.id})
    _raise_validation_failed(errors, "链接实例创建被拒绝")
    db.add(obj); db.flush()
    # 链接存在性也是事实（对齐演示：assigned_to(CA1234, A5).exists = true）
    record_link_fact(db, ontology_id=ontology_id, link_instance_id=obj.id,
                     link_type_id=obj.link_type_id, exists=True,
                     source="manual", actor_id=getattr(current_user, "id", None))
    project.updated_at = datetime.now(timezone.utc)
    _commit_graph_mutation(db, ontology_id, refresh=obj)
    return _ok(S.LinkInstanceOut.model_validate(obj).model_dump(by_alias=True))


@_projection_locked_writer
def delete_link_instance(
        ontology_id: str,
        link_id: str,
        db: Session,
        current_user,
        *,
        reject_runtime_write_fn: Optional[Callable] = None):
    (reject_runtime_write_fn or _reject_direct_runtime_data_write)()
    project = _require_ontology(db, ontology_id, for_update=True)
    obj = db.query(LinkInstance).filter(LinkInstance.id == link_id,
                                        LinkInstance.ontology_id == ontology_id).first()
    if not obj:
        raise HTTPException(404, "Not found")
    record_link_fact(db, ontology_id=ontology_id, link_instance_id=obj.id,
                     link_type_id=obj.link_type_id, exists=False,
                     source="manual", actor_id=getattr(current_user, "id", None))
    project.updated_at = datetime.now(timezone.utc)
    db.delete(obj)
    _commit_graph_mutation(db, ontology_id)
