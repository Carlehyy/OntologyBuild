"""Schema authoring, full-model persistence, and schema CRUD services.

The legacy HTTP router re-exports the private helpers in this module so older
imports and monkeypatch targets remain valid while all implementation lives in
the canonical formal-modeling package.
"""
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.deps import get_current_user, get_db
from app.models.ontology import OntologyProject
from app.models.ontology_formal import (
    ActionExecutionLog,
    ActionType,
    LinkInstance,
    LinkType,
    ObjectInstance,
    ObjectType,
    OntologyFunction,
)
from app.models.ontology_version import OntologyVersion
from app.ontologies.formal_modeling.derived import recompute_instance_derived
from app.ontologies.formal_modeling.facts import (
    record_link_fact,
    record_object_presence,
    record_object_tombstone,
    record_property_facts,
)
from app.ontologies.formal_modeling.runtime_support import (
    _ok,
    _orm_view,
    _raise_validation_failed,
    _require_ontology,
)
from app.ontologies.formal_modeling.validation import validate_model
from app.schemas import ontology_formal as S


# Keep the historical logger identity so existing log filters and assertions do
# not change merely because the implementation moved out of the router module.
logger = logging.getLogger("app.ontologies.formal_modeling.router")


def _require_schema_draft(project: OntologyProject) -> None:
    if (project.status or "") == "published":
        raise HTTPException(
            409,
            "已发布本体的模式层不可直接修改。请先通过版本接口撤回为草稿，修改后重新发布。",
        )


def _reject_direct_runtime_data_write() -> None:
    if settings.environment == "production":
        raise HTTPException(
            403,
            "生产本体数据只允许由已审批的数据资产湖 Mapping 写入；直接实例写入已禁用。",
        )


def _runtime_state(db: Session, ontology_id: str):
    """读取当前正规本体完整视图，供所有旁路 CRUD 复用同一校验契约。"""

    def q(model):
        return db.query(model).filter(model.ontology_id == ontology_id).all()

    return (
        q(ObjectType),
        q(LinkType),
        q(ActionType),
        q(OntologyFunction),
        q(ObjectInstance),
        q(LinkInstance),
    )


# ============ 保存共用辅助（全量 PUT 与增量 PATCH 复用） ============

FIELDS_OBJECT_TYPE = [
    "name",
    "display_name",
    "description",
    "icon",
    "color",
    "primary_key",
    "properties",
    "interfaces",
    "position_x",
    "position_y",
]
FIELDS_FUNCTION = [
    "name",
    "display_name",
    "description",
    "function_type",
    "language",
    "target_object_type_id",
    "target_action_id",
    "parameters",
    "return_type",
    "body",
    "cache_strategy",
    "cache_ttl",
    "enabled",
]
FIELDS_ACTION = [
    "name",
    "display_name",
    "description",
    "object_type_id",
    "parameters",
    "rules",
    "validation_function_id",
    "requires_approval",
]
FIELDS_LINK_TYPE = [
    "name",
    "display_name",
    "description",
    "source_object_type_id",
    "target_object_type_id",
    "cardinality",
    "source_role",
    "target_role",
    "properties",
]
FIELDS_INSTANCE = [
    "object_type_id",
    "properties",
    "computed",
    "source",
    "external_id",
]
FIELDS_LINK_INSTANCE = [
    "link_type_id",
    "source_object_id",
    "target_object_id",
    "properties",
]


def _dedup_properties(value):
    """对 properties 列表按 name 去重，保留最后一次出现（后写覆盖），并保持顺序。

    非列表 / 元素无 name 时原样返回，保证向后兼容。
    """
    if not isinstance(value, list):
        return value
    seen: dict[str, int] = {}
    result: list = []
    for prop in value:
        key = prop.get("name") if isinstance(prop, dict) else None
        if key is None:
            result.append(prop)
            continue
        if key in seen:
            result[seen[key]] = prop
        else:
            seen[key] = len(result)
            result.append(prop)
    return result


_dedup_properties_override: ContextVar[Optional[Callable]] = ContextVar(
    "formal_schema_dedup_properties_override",
    default=None,
)


def _upsert_items(
    db: Session,
    ontology_id: str,
    model,
    items,
    fields,
) -> set[str]:
    """按 id upsert，返回涉及的 id 集合（不删除未提及的记录）。"""
    existing = {
        x.id: x
        for x in db.query(model).filter(model.ontology_id == ontology_id).all()
    }
    keep_ids: set[str] = set()
    for item in items:
        data = item.model_dump(exclude_none=False)
        item_id = data.pop("id", None)
        if "properties" in data:
            dedup_properties_fn = (
                _dedup_properties_override.get() or _dedup_properties
            )
            data["properties"] = dedup_properties_fn(data["properties"])
        payload = {k: data[k] for k in fields if k in data}
        if item_id and item_id in existing:
            obj = existing[item_id]
            # 采集溯源保护：编辑器保存不得清洗实例的 source/external_id ——
            # 空值不覆盖；已有非 manual 出处（collector/import/action）不被降级为 manual，
            # 否则去重键被毁、下次采集必产生重复实例。
            if model is ObjectInstance:
                if payload.get("source") in (None, "") or (
                    payload.get("source") == "manual"
                    and (obj.source or "manual") != "manual"
                ):
                    payload.pop("source", None)
                if payload.get("external_id") in (None, ""):
                    payload.pop("external_id", None)
            for key, value in payload.items():
                setattr(obj, key, value)
            keep_ids.add(item_id)
        else:
            kwargs = dict(payload)
            if item_id:
                kwargs["id"] = item_id
            obj = model(ontology_id=ontology_id, **kwargs)
            db.add(obj)
            db.flush()
            # Full/delta payload ids are optional. Keep the request model
            # aligned with the persisted row so the Fact pass below can record
            # presence and properties for server-generated instance ids.
            if not item_id:
                item.id = obj.id
            keep_ids.add(obj.id)
    return keep_ids


def _scrub_orphan_data(
    db: Session,
    ontology_id: str,
    actor_id: Optional[str] = None,
) -> dict:
    """实例层孤儿清理（安全网）：类型已不存在的实例、引用悬挂的链接实例。

    增量保存时前端应显式提交级联删除；此处兜底防止漏发造成孤儿数据。
    被清理的实例/链接同步写入墓碑/存在性事实——投影删了，历史真理流仍完整。
    """
    ot_ids = {
        x.id
        for x in db.query(ObjectType.id).filter(
            ObjectType.ontology_id == ontology_id
        )
    }
    lt_ids = {
        x.id
        for x in db.query(LinkType.id).filter(
            LinkType.ontology_id == ontology_id
        )
    }
    n_inst = n_li = 0
    for inst in (
        db.query(ObjectInstance)
        .filter(ObjectInstance.ontology_id == ontology_id)
        .all()
    ):
        if inst.object_type_id not in ot_ids:
            record_object_tombstone(
                db,
                ontology_id=ontology_id,
                instance_id=inst.id,
                object_type_id=inst.object_type_id,
                source="editor-save:cascade",
                actor_id=actor_id,
            )
            db.delete(inst)
            n_inst += 1
    remaining = {
        x.id
        for x in db.query(ObjectInstance.id).filter(
            ObjectInstance.ontology_id == ontology_id
        )
    }
    for link in (
        db.query(LinkInstance)
        .filter(LinkInstance.ontology_id == ontology_id)
        .all()
    ):
        if (
            link.link_type_id not in lt_ids
            or link.source_object_id not in remaining
            or link.target_object_id not in remaining
        ):
            record_link_fact(
                db,
                ontology_id=ontology_id,
                link_instance_id=link.id,
                link_type_id=link.link_type_id,
                exists=False,
                source="editor-save:cascade",
                actor_id=actor_id,
            )
            db.delete(link)
            n_li += 1
    return {"instances": n_inst, "linkInstances": n_li}


def _revision_of(p: OntologyProject) -> str:
    """本体的乐观并发修订戳（updated_at 归一为 UTC-naive ISO 串）。

    客户端在 GET /full 拿到后原样带回，服务端字符串精确比对——
    归一化保证同一时刻在 aware/naive 两种存储形态下产出相同的串。
    """
    value = p.updated_at or p.created_at
    if value is None:
        return ""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat()


def get_full_ontology(
    ontology_id: str,
    db: Session,
    *,
    require_ontology_fn: Callable = _require_ontology,
    revision_of_fn: Callable = _revision_of,
    ok_fn: Callable = _ok,
):
    project = require_ontology_fn(db, ontology_id)

    def q(model):
        return db.query(model).filter(model.ontology_id == ontology_id).all()

    # 当前发布版的画布布局独立于模型快照保存。运行投影读取时覆盖展示坐标，
    # 但不把纯视觉调整伪装成一次模式层发布。
    canvas_layout: dict = {}
    if project.current_release_id:
        release = (
            db.query(OntologyVersion)
            .filter(
                OntologyVersion.id == project.current_release_id,
                OntologyVersion.ontology_id == ontology_id,
            )
            .first()
        )
        if release and isinstance(release.canvas_layout, dict):
            canvas_layout = release.canvas_layout
    object_types = []
    for row in q(ObjectType):
        item = S.ObjectTypeOut.model_validate(row)
        position = canvas_layout.get(row.id)
        if isinstance(position, dict) and "x" in position and "y" in position:
            item.position_x = float(position["x"])
            item.position_y = float(position["y"])
        object_types.append(item)

    out = S.FullOntologyOut(
        id=project.id,
        name=project.name,
        description=project.description,
        version=project.version or "1.0.0",
        revision=revision_of_fn(project),
        object_types=object_types,
        link_types=[
            S.LinkTypeOut.model_validate(item) for item in q(LinkType)
        ],
        actions=[
            S.ActionTypeOut.model_validate(item) for item in q(ActionType)
        ],
        functions=[
            S.FunctionOut.model_validate(item)
            for item in q(OntologyFunction)
        ],
        instances=[
            S.ObjectInstanceOut.model_validate(item)
            for item in q(ObjectInstance)
        ],
        link_instances=[
            S.LinkInstanceOut.model_validate(item)
            for item in q(LinkInstance)
        ],
        execution_logs=[
            S.ActionLogOut.model_validate(item)
            for item in (
                db.query(ActionExecutionLog)
                .filter(ActionExecutionLog.ontology_id == ontology_id)
                .order_by(ActionExecutionLog.executed_at.desc())
                .limit(200)
                .all()
            )
        ],
    )
    return ok_fn(out.model_dump(by_alias=True))


def save_full_ontology(
    ontology_id: str,
    body: S.SaveFullOntologyRequest,
    db: Session,
    current_user,
    *,
    require_ontology_fn: Callable = _require_ontology,
    require_schema_draft_fn: Callable = _require_schema_draft,
    revision_of_fn: Callable = _revision_of,
    upsert_items_fn: Callable = _upsert_items,
    dedup_properties_fn: Callable = _dedup_properties,
    scrub_dangling_references_fn: Optional[Callable] = None,
    raise_validation_failed_fn: Callable = _raise_validation_failed,
    validate_model_fn: Callable = validate_model,
    get_full_ontology_fn: Optional[Callable] = None,
):
    """用前端图谱编辑页的当前状态整体替换该本体下的建模 + 实例数据。

    采用 upsert + 清理：保留 body 中带 id 的记录（按 id upsert），
    删除库中存在但 body 未提供的记录。执行日志不在此处理。
    """
    project = require_ontology_fn(db, ontology_id, for_update=True)
    require_schema_draft_fn(project)
    # 本请求随后会同步调用 run_for_save 评估哨兵——抑制 CDC 后台线程的并行评估，
    # 否则两边同时做边沿差分，动作可能被重复执行
    from app.ontologies.sentinels.cdc import SUPPRESS_KEY

    db.info[SUPPRESS_KEY] = True

    # 乐观并发检测：客户端携带加载时的 revision，与当前不一致说明
    # 期间有其他会话保存过 —— 拒绝写入，避免"后保存者静默覆盖"
    if body.base_revision is not None:
        current = revision_of_fn(project)
        if body.base_revision != current:
            raise HTTPException(
                409,
                detail={
                    "code": "conflict",
                    "message": "该本体已被其他会话修改，请重新加载后再保存",
                    "currentRevision": current,
                },
            )

    # 服务端强制校验：模式层与运行层硬错误统一以 422 拒绝。
    errors = validate_model_fn(
        body.object_types,
        body.link_types,
        body.actions,
        body.functions,
        body.instances,
        body.link_instances,
    )
    raise_validation_failed_fn(errors, "模型校验未通过，已拒绝保存")

    # 同步本体元信息（可选字段）
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.version is not None and body.version != project.version:
        raise HTTPException(409, "版本号由发布流程管理，full-save 不能直接修改")

    actor = getattr(current_user, "id", None)

    def upsert(model, items, fields):
        if upsert_items_fn is _upsert_items:
            token = _dedup_properties_override.set(dedup_properties_fn)
            try:
                return upsert_items_fn(
                    db,
                    ontology_id,
                    model,
                    items,
                    fields,
                )
            finally:
                _dedup_properties_override.reset(token)
        # Historical monkeypatch targets implement the original five-argument
        # contract. Do not force new compatibility-only keywords onto them.
        return upsert_items_fn(db, ontology_id, model, items, fields)

    def sync(model, items, fields):
        """按 id upsert，并删除不在 items 内的旧记录（全量替换语义）。"""
        keep_ids = upsert(model, items, fields)
        for obj in (
            db.query(model).filter(model.ontology_id == ontology_id).all()
        ):
            if obj.id not in keep_ids:
                db.delete(obj)

    # 顺序很重要：先建对象类型/接口/函数/动作，再链接，再实例/链接实例
    sync(ObjectType, body.object_types, FIELDS_OBJECT_TYPE)
    sync(OntologyFunction, body.functions, FIELDS_FUNCTION)
    sync(ActionType, body.actions, FIELDS_ACTION)
    sync(LinkType, body.link_types, FIELDS_LINK_TYPE)
    # Fact 溯源：在实例投影被覆盖前抓取旧属性/链接快照，保存后逐条追加事实
    prev_instances = {
        item.id: item
        for item in (
            db.query(ObjectInstance)
            .filter(ObjectInstance.ontology_id == ontology_id)
            .all()
        )
    }
    prev_instance_props = {
        instance_id: dict(item.properties or {})
        for instance_id, item in prev_instances.items()
    }
    prev_links = {
        item.id: item.link_type_id
        for item in (
            db.query(LinkInstance)
            .filter(LinkInstance.ontology_id == ontology_id)
            .all()
        )
    }

    sync(ObjectInstance, body.instances, FIELDS_INSTANCE)
    sync(LinkInstance, body.link_instances, FIELDS_LINK_INSTANCE)

    # 被整体保存移除的实例 → 墓碑事实（先于属性事实，保证回放语义完整）
    kept_ids = {instance.id for instance in body.instances if instance.id}
    for gone_id, gone in prev_instances.items():
        if gone_id not in kept_ids:
            record_object_tombstone(
                db,
                ontology_id=ontology_id,
                instance_id=gone_id,
                object_type_id=gone.object_type_id,
                source="editor-save",
                actor_id=actor,
            )

    # 链接存在性事实：diff 新旧链接集合（Link 也是 Fact）
    cur_links = {
        item.id: item.link_type_id
        for item in (
            db.query(LinkInstance)
            .filter(LinkInstance.ontology_id == ontology_id)
            .all()
        )
    }
    for link_id, link_type_id in cur_links.items():
        if link_id not in prev_links:
            record_link_fact(
                db,
                ontology_id=ontology_id,
                link_instance_id=link_id,
                link_type_id=link_type_id,
                exists=True,
                source="editor-save",
                actor_id=actor,
            )
    for link_id, link_type_id in prev_links.items():
        if link_id not in cur_links:
            record_link_fact(
                db,
                ontology_id=ontology_id,
                link_instance_id=link_id,
                link_type_id=link_type_id,
                exists=False,
                source="editor-save",
                actor_id=actor,
            )

    for instance in body.instances:
        if not instance.id:
            continue
        if instance.id not in prev_instances:
            record_object_presence(
                db,
                ontology_id=ontology_id,
                instance_id=instance.id,
                object_type_id=instance.object_type_id,
                source="editor-save",
                actor_id=actor,
            )
        created = record_property_facts(
            db,
            ontology_id=ontology_id,
            instance_id=instance.id,
            object_type_id=instance.object_type_id,
            old_props=prev_instance_props.get(instance.id),
            new_props=instance.properties or {},
            source="editor-save",
            actor_id=actor,
        )
        # 存储属性变了 → 该实例的派生属性自动重算（写投影 + 追加 derived 事实）
        if created:
            row = (
                db.query(ObjectInstance)
                .filter(
                    ObjectInstance.id == instance.id,
                    ObjectInstance.ontology_id == ontology_id,
                )
                .first()
            )
            if row:
                recompute_instance_derived(
                    db,
                    ontology_id=ontology_id,
                    instance=row,
                    trigger_facts=created,
                )

    # 显式推进修订戳：即使元信息字段无变化，fo_* 数据的保存也必须产生新 revision
    project.updated_at = datetime.now(timezone.utc)

    # 1) 先提交核心保存 —— 保证用户的建模改动一定落库
    db.commit()

    # 2) 引用完整性清理为"尽力而为":即便它出错,也绝不回滚/阻断已成功的核心保存
    scrub_fn = (
        scrub_dangling_references_fn or _scrub_dangling_references
    )
    try:
        changed = scrub_fn(db, ontology_id)
        if any(changed.values()):
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        logger.warning("引用完整性清理失败,已跳过(不影响保存)", exc_info=True)

    # 3) 保存即"变化到达"：触发哨兵评估（尽力而为，失败不影响保存）。
    #    编辑器内的实例变更只有保存时才落库，不在这里评估的话哨兵对编辑器是"聋"的。
    sentinel_summary = None
    try:
        from app.ontologies.sentinels.engine import run_for_save

        sentinel_summary = run_for_save(db, ontology_id)
    except Exception:
        db.rollback()
        logger.warning("保存后哨兵评估失败,已跳过(不影响保存)", exc_info=True)

    if get_full_ontology_fn is None:
        response = get_full_ontology(
            ontology_id,
            db,
            require_ontology_fn=require_ontology_fn,
            revision_of_fn=revision_of_fn,
        )
    else:
        response = get_full_ontology_fn(ontology_id, db)
    if sentinel_summary is not None:
        response["data"]["sentinelSummary"] = {
            "evaluated": sentinel_summary.get("evaluated", 0),
            "fired": sentinel_summary.get("fired", 0),
        }
    return response


def patch_full_ontology(
    ontology_id: str,
    body: S.PatchOntologyRequest,
    db: Session,
    current_user,
    *,
    require_ontology_fn: Callable = _require_ontology,
    require_schema_draft_fn: Callable = _require_schema_draft,
    revision_of_fn: Callable = _revision_of,
    upsert_items_fn: Callable = _upsert_items,
    dedup_properties_fn: Callable = _dedup_properties,
    scrub_orphan_data_fn: Callable = _scrub_orphan_data,
    scrub_dangling_references_fn: Optional[Callable] = None,
    raise_validation_failed_fn: Callable = _raise_validation_failed,
    validate_model_fn: Callable = validate_model,
    ok_fn: Callable = _ok,
):
    """只应用自上次保存以来的变更/删除，机制与 PUT /full 完全等价：
    并发检测 → 合并视图强制校验 → 应用 delta → 属性事实 + 派生重算 →
    孤儿清理 → 哨兵评估。负载 O(变更) 而非 O(全模型)。
    """
    project = require_ontology_fn(db, ontology_id, for_update=True)
    require_schema_draft_fn(project)
    from app.ontologies.sentinels.cdc import SUPPRESS_KEY

    db.info[SUPPRESS_KEY] = True

    if body.base_revision is not None:
        current = revision_of_fn(project)
        if body.base_revision != current:
            raise HTTPException(
                409,
                detail={
                    "code": "conflict",
                    "message": "该本体已被其他会话修改，请重新加载后再保存",
                    "currentRevision": current,
                },
            )

    upserts, deletes = body.upserts, body.deletes

    # 合并视图（现库 − 删除 + upsert）上做与全量保存相同的强制校验
    def merged(model, items, delete_ids):
        current = {
            item.id: item
            for item in (
                db.query(model)
                .filter(model.ontology_id == ontology_id)
                .all()
            )
        }
        for deleted_id in delete_ids:
            current.pop(deleted_id, None)
        output: dict = dict(current)
        for index, item in enumerate(items):
            output[item.id or f"__new_{index}"] = item
        return list(output.values())

    errors = validate_model_fn(
        merged(
            ObjectType,
            upserts.object_types,
            deletes.object_types,
        ),
        merged(LinkType, upserts.link_types, deletes.link_types),
        merged(ActionType, upserts.actions, deletes.actions),
        merged(
            OntologyFunction,
            upserts.functions,
            deletes.functions,
        ),
        merged(
            ObjectInstance,
            upserts.instances,
            deletes.instances,
        ),
        merged(
            LinkInstance,
            upserts.link_instances,
            deletes.link_instances,
        ),
    )
    raise_validation_failed_fn(errors, "模型校验未通过，已拒绝保存")

    # 元信息
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.version is not None and body.version != project.version:
        raise HTTPException(409, "版本号由发布流程管理，PATCH 不能直接修改")

    actor = getattr(current_user, "id", None)

    # 旧属性快照（仅本次 upsert 的实例），供事实追加
    upsert_instance_ids = [
        instance.id for instance in upserts.instances if instance.id
    ]
    previous_properties = {}
    if upsert_instance_ids:
        previous_properties = {
            item.id: dict(item.properties or {})
            for item in (
                db.query(ObjectInstance)
                .filter(
                    ObjectInstance.ontology_id == ontology_id,
                    ObjectInstance.id.in_(upsert_instance_ids),
                )
                .all()
            )
        }

    # 删除（前端级联删除已展开为显式 id 列表）——先写墓碑/存在性事实再删投影
    if deletes.link_instances:
        for link in (
            db.query(LinkInstance)
            .filter(
                LinkInstance.ontology_id == ontology_id,
                LinkInstance.id.in_(deletes.link_instances),
            )
            .all()
        ):
            record_link_fact(
                db,
                ontology_id=ontology_id,
                link_instance_id=link.id,
                link_type_id=link.link_type_id,
                exists=False,
                source="editor-save",
                actor_id=actor,
            )
    if deletes.instances:
        for instance in (
            db.query(ObjectInstance)
            .filter(
                ObjectInstance.ontology_id == ontology_id,
                ObjectInstance.id.in_(deletes.instances),
            )
            .all()
        ):
            record_object_tombstone(
                db,
                ontology_id=ontology_id,
                instance_id=instance.id,
                object_type_id=instance.object_type_id,
                source="editor-save",
                actor_id=actor,
            )

    def delete(model, item_ids):
        if item_ids:
            (
                db.query(model)
                .filter(
                    model.ontology_id == ontology_id,
                    model.id.in_(item_ids),
                )
                .delete(synchronize_session=False)
            )

    delete(LinkInstance, deletes.link_instances)
    delete(ObjectInstance, deletes.instances)
    delete(LinkType, deletes.link_types)
    delete(ActionType, deletes.actions)
    delete(OntologyFunction, deletes.functions)
    delete(ObjectType, deletes.object_types)

    # 链接快照（供 diff 出新建链接的存在性事实）
    previous_link_ids = {
        item.id
        for item in db.query(LinkInstance.id).filter(
            LinkInstance.ontology_id == ontology_id
        )
    }

    def upsert(model, items, fields):
        if upsert_items_fn is _upsert_items:
            token = _dedup_properties_override.set(dedup_properties_fn)
            try:
                return upsert_items_fn(
                    db,
                    ontology_id,
                    model,
                    items,
                    fields,
                )
            finally:
                _dedup_properties_override.reset(token)
        return upsert_items_fn(db, ontology_id, model, items, fields)

    # upsert（与全量保存相同的顺序）
    upsert(ObjectType, upserts.object_types, FIELDS_OBJECT_TYPE)
    upsert(
        OntologyFunction,
        upserts.functions,
        FIELDS_FUNCTION,
    )
    upsert(ActionType, upserts.actions, FIELDS_ACTION)
    upsert(LinkType, upserts.link_types, FIELDS_LINK_TYPE)
    upsert(ObjectInstance, upserts.instances, FIELDS_INSTANCE)
    upsert(
        LinkInstance,
        upserts.link_instances,
        FIELDS_LINK_INSTANCE,
    )

    # 新建链接 → 存在性事实（Link 也是 Fact）
    for link in (
        db.query(LinkInstance)
        .filter(LinkInstance.ontology_id == ontology_id)
        .all()
    ):
        if link.id not in previous_link_ids:
            record_link_fact(
                db,
                ontology_id=ontology_id,
                link_instance_id=link.id,
                link_type_id=link.link_type_id,
                exists=True,
                source="editor-save",
                actor_id=actor,
            )

    # 孤儿清理安全网（前端漏发级联删除时兜底）
    pruned = scrub_orphan_data_fn(db, ontology_id, actor_id=actor)
    if any(pruned.values()):
        logger.info(
            "增量保存孤儿清理: %s (ontology=%s)",
            pruned,
            ontology_id,
        )

    # 属性事实 + 派生重算（仅涉及本次 upsert 且仍存在的实例——
    # 实例可能已被上方级联/孤儿清理删除，跳过以免产生指向空实例的孤儿事实）
    for instance in upserts.instances:
        if not instance.id:
            continue
        row = (
            db.query(ObjectInstance)
            .filter(
                ObjectInstance.id == instance.id,
                ObjectInstance.ontology_id == ontology_id,
            )
            .first()
        )
        if row is None:
            continue
        if instance.id not in previous_properties:
            record_object_presence(
                db,
                ontology_id=ontology_id,
                instance_id=instance.id,
                object_type_id=instance.object_type_id,
                source="editor-save",
                actor_id=actor,
            )
        created = record_property_facts(
            db,
            ontology_id=ontology_id,
            instance_id=instance.id,
            object_type_id=instance.object_type_id,
            old_props=previous_properties.get(instance.id),
            new_props=instance.properties or {},
            source="editor-save",
            actor_id=actor,
        )
        if created:
            recompute_instance_derived(
                db,
                ontology_id=ontology_id,
                instance=row,
                trigger_facts=created,
            )

    project.updated_at = datetime.now(timezone.utc)
    db.commit()

    # 引用完整性清理（尽力而为，同 PUT）
    scrub_fn = (
        scrub_dangling_references_fn or _scrub_dangling_references
    )
    try:
        changed = scrub_fn(db, ontology_id)
        if any(changed.values()):
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        logger.warning("引用完整性清理失败,已跳过(不影响保存)", exc_info=True)

    # 哨兵评估（尽力而为，同 PUT）
    sentinel_summary = None
    try:
        from app.ontologies.sentinels.engine import run_for_save

        sentinel_summary = run_for_save(db, ontology_id)
    except Exception:
        db.rollback()
        logger.warning("保存后哨兵评估失败,已跳过(不影响保存)", exc_info=True)

    # 轻量响应：新 revision + 本次涉及实例的最新投影（含重算后的 computed）
    project = require_ontology_fn(db, ontology_id)
    updated_instances = []
    if upsert_instance_ids:
        updated_instances = [
            S.ObjectInstanceOut.model_validate(item).model_dump(
                by_alias=True
            )
            for item in (
                db.query(ObjectInstance)
                .filter(
                    ObjectInstance.ontology_id == ontology_id,
                    ObjectInstance.id.in_(upsert_instance_ids),
                )
                .all()
            )
        ]
    data = {
        "revision": revision_of_fn(project),
        "instances": updated_instances,
    }
    if sentinel_summary is not None:
        data["sentinelSummary"] = {
            "evaluated": sentinel_summary.get("evaluated", 0),
            "fired": sentinel_summary.get("fired", 0),
        }
    return ok_fn(data)


def _scrub_dangling_references(db: Session, ontology_id: str) -> dict:
    """引用完整性清理：把指向"已不存在实体"的引用清空 / 剔除。

    级联删除(deleteObjectType 等)已清掉直接引用方(关系/动作/函数/实例),
    但有三类引用藏得更深、不会被级联覆盖,在此统一兜底:
      1. 动作 rules(JSON)里的 targetObjectTypeId / linkTypeId / functionId
         以及动作级 validation_function_id —— 指向已删类型/关系/函数时置空。
      2. 哨兵(独立运行层)的 action_ids / bindings.objectTypeId / links.linkTypeId
         —— 指向已删实体时剔除。
      3. 属性按 name 的引用(函数表达式 / 条件)属表达式层,无法安全静态解析,
         不在此处理(由使用方负责),仅处理上述结构化的 id 引用。

    返回清理计数,便于日志/排查。
    """
    object_type_ids = {
        item.id
        for item in db.query(ObjectType.id).filter(
            ObjectType.ontology_id == ontology_id
        )
    }
    link_type_ids = {
        item.id
        for item in db.query(LinkType.id).filter(
            LinkType.ontology_id == ontology_id
        )
    }
    function_ids = {
        item.id
        for item in db.query(OntologyFunction.id).filter(
            OntologyFunction.ontology_id == ontology_id
        )
    }
    action_ids = {
        item.id
        for item in db.query(ActionType.id).filter(
            ActionType.ontology_id == ontology_id
        )
    }
    counts = {
        "action_rule_refs": 0,
        "validation_fn_refs": 0,
        "sentinel_actions": 0,
        "sentinel_bindings": 0,
        "sentinel_links": 0,
    }

    def blank_if_dangling(config: dict, key: str, valid: set) -> bool:
        value = config.get(key)
        if value and value not in valid:
            config[key] = ""
            return True
        return False

    # —— 1. 动作 rules + validation_function_id ——
    for action in db.query(ActionType).filter(
        ActionType.ontology_id == ontology_id
    ):
        dirty = False
        rules = action.rules or []
        for rule in rules:
            config = rule.get("config") if isinstance(rule, dict) else None
            if not isinstance(config, dict):
                continue
            if blank_if_dangling(
                config,
                "targetObjectTypeId",
                object_type_ids,
            ):
                counts["action_rule_refs"] += 1
                dirty = True
            if blank_if_dangling(config, "linkTypeId", link_type_ids):
                counts["action_rule_refs"] += 1
                dirty = True
            if blank_if_dangling(config, "functionId", function_ids):
                counts["action_rule_refs"] += 1
                dirty = True
            for mapping in config.get("propertyMappings", []) or []:
                if isinstance(mapping, dict) and blank_if_dangling(
                    mapping,
                    "functionId",
                    function_ids,
                ):
                    counts["action_rule_refs"] += 1
                    dirty = True
        if dirty:
            # JSON 原地修改需显式标脏,否则不落库
            flag_modified(action, "rules")

        if (
            action.validation_function_id
            and action.validation_function_id not in function_ids
        ):
            action.validation_function_id = None
            counts["validation_fn_refs"] += 1

    # —— 2. 哨兵(跨层)——
    try:
        from app.models.sentinel import Sentinel
    except Exception:
        Sentinel = None
    if Sentinel is not None:
        for sentinel in db.query(Sentinel).filter(
            Sentinel.ontology_id == ontology_id
        ):
            # 动作引用
            current_action_ids = sentinel.action_ids or []
            kept_action_ids = [
                action_id
                for action_id in current_action_ids
                if action_id in action_ids
            ]
            if len(kept_action_ids) != len(current_action_ids):
                counts["sentinel_actions"] += (
                    len(current_action_ids) - len(kept_action_ids)
                )
                sentinel.action_ids = kept_action_ids
            # 绑定的对象类型引用
            bindings = sentinel.bindings or []
            kept_bindings = [
                binding
                for binding in bindings
                if binding.get("objectTypeId") in object_type_ids
            ]
            if len(kept_bindings) != len(bindings):
                counts["sentinel_bindings"] += (
                    len(bindings) - len(kept_bindings)
                )
                sentinel.bindings = kept_bindings
                # primary_alias 若指向被删绑定则重置为首个存活绑定
                if sentinel.primary_alias not in {
                    binding.get("alias") for binding in kept_bindings
                }:
                    sentinel.primary_alias = (
                        kept_bindings[0]["alias"]
                        if kept_bindings
                        else None
                    )
            # 关系约束的链接类型引用
            links = sentinel.links or []
            kept_links = [
                link
                for link in links
                if link.get("linkTypeId") in link_type_ids
            ]
            if len(kept_links) != len(links):
                counts["sentinel_links"] += len(links) - len(kept_links)
                sentinel.links = kept_links

    return counts


# ============================================================
#  Generic CRUD factory
# ============================================================
def _crud(
    model,
    create_schema,
    update_schema,
    out_schema,
    name: str,
    *,
    compatibility_helpers: Optional[Callable[[], dict]] = None,
):
    sub = APIRouter()
    state_index = {
        ObjectType: 0,
        LinkType: 1,
        ActionType: 2,
        OntologyFunction: 3,
    }

    def helper(helper_name: str, fallback):
        if compatibility_helpers is None:
            return fallback
        return compatibility_helpers().get(helper_name, fallback)

    @sub.get(f"/{{ontology_id}}/{name}")
    def list_items(
        ontology_id: str,
        db: Session = Depends(get_db),
        _=Depends(get_current_user),
    ):
        helper("_require_ontology", _require_ontology)(db, ontology_id)
        items = (
            db.query(model)
            .filter(model.ontology_id == ontology_id)
            .all()
        )
        return helper("_ok", _ok)(
            [
                out_schema.model_validate(item).model_dump(by_alias=True)
                for item in items
            ]
        )

    @sub.post(f"/{{ontology_id}}/{name}", status_code=201)
    def create_item(
        ontology_id: str,
        body: create_schema,
        db: Session = Depends(get_db),
        _=Depends(get_current_user),
    ):  # type: ignore
        project = helper("_require_ontology", _require_ontology)(
            db,
            ontology_id,
            for_update=True,
        )
        helper("_require_schema_draft", _require_schema_draft)(project)
        data = body.model_dump(exclude_none=True)
        obj = model(
            id=str(uuid.uuid4()),
            ontology_id=ontology_id,
            **data,
        )
        state = list(
            helper("_runtime_state", _runtime_state)(db, ontology_id)
        )
        state[state_index[model]] = [
            *state[state_index[model]],
            obj,
        ]
        helper("_raise_validation_failed", _raise_validation_failed)(
            helper("validate_model", validate_model)(*state),
            f"{name} 创建被拒绝",
        )
        project.updated_at = datetime.now(timezone.utc)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return helper("_ok", _ok)(
            out_schema.model_validate(obj).model_dump(by_alias=True)
        )

    @sub.get(f"/{{ontology_id}}/{name}/{{item_id}}")
    def get_item(
        ontology_id: str,
        item_id: str,
        db: Session = Depends(get_db),
        _=Depends(get_current_user),
    ):
        obj = (
            db.query(model)
            .filter(
                model.id == item_id,
                model.ontology_id == ontology_id,
            )
            .first()
        )
        if not obj:
            raise HTTPException(404, "Not found")
        return helper("_ok", _ok)(
            out_schema.model_validate(obj).model_dump(by_alias=True)
        )

    @sub.put(f"/{{ontology_id}}/{name}/{{item_id}}")
    def update_item(
        ontology_id: str,
        item_id: str,
        body: update_schema,
        db: Session = Depends(get_db),
        _=Depends(get_current_user),
    ):  # type: ignore
        project = helper("_require_ontology", _require_ontology)(
            db,
            ontology_id,
            for_update=True,
        )
        helper("_require_schema_draft", _require_schema_draft)(project)
        obj = (
            db.query(model)
            .filter(
                model.id == item_id,
                model.ontology_id == ontology_id,
            )
            .first()
        )
        if not obj:
            raise HTTPException(404, "Not found")
        updates = body.model_dump(exclude_unset=True)
        candidate = helper("_orm_view", _orm_view)(obj, updates)
        state = list(
            helper("_runtime_state", _runtime_state)(db, ontology_id)
        )
        state[state_index[model]] = [
            candidate if item.id == item_id else item
            for item in state[state_index[model]]
        ]
        helper("_raise_validation_failed", _raise_validation_failed)(
            helper("validate_model", validate_model)(*state),
            f"{name} 更新被拒绝",
        )
        for key, value in updates.items():
            setattr(obj, key, value)
        project.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(obj)
        return helper("_ok", _ok)(
            out_schema.model_validate(obj).model_dump(by_alias=True)
        )

    @sub.delete(
        f"/{{ontology_id}}/{name}/{{item_id}}",
        status_code=204,
    )
    def delete_item(
        ontology_id: str,
        item_id: str,
        db: Session = Depends(get_db),
        _=Depends(get_current_user),
    ):
        project = helper("_require_ontology", _require_ontology)(
            db,
            ontology_id,
            for_update=True,
        )
        helper("_require_schema_draft", _require_schema_draft)(project)
        obj = (
            db.query(model)
            .filter(
                model.id == item_id,
                model.ontology_id == ontology_id,
            )
            .first()
        )
        if not obj:
            raise HTTPException(404, "Not found")
        state = list(
            helper("_runtime_state", _runtime_state)(db, ontology_id)
        )
        state[state_index[model]] = [
            item
            for item in state[state_index[model]]
            if item.id != item_id
        ]
        helper("_raise_validation_failed", _raise_validation_failed)(
            helper("validate_model", validate_model)(*state),
            f"{name} 删除会造成悬挂引用，已拒绝",
        )
        project.updated_at = datetime.now(timezone.utc)
        db.delete(obj)
        db.commit()

    return sub
