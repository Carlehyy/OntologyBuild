"""
正规本体模型 API (Formal Ontology) — /api/v2/formal/ontologies/{ontology_id}/...

平台核心：本体建模 + 数据采集落地。
所有响应统一包裹 {"data": ...}，与前端 apiClient 解包逻辑一致。
"""
import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, and_, cast, false, func, or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from typing import Optional

from app.deps import get_db, get_current_user, require_admin
from app.ontologies.access import ontology_access_guard
from app.models.ontology import OntologyProject
from app.models.ontology_version import OntologyVersion
from app.models.ontology_formal import (
    ObjectType, LinkType, ActionType, OntologyFunction,
    ObjectInstance, LinkInstance, ActionExecutionLog,
)
from app.models.ontology_formal import PropertyFact
from app.ontologies.formal_modeling.facts import (
    record_property_facts, record_link_fact, record_object_tombstone,
    record_decision_fact, fact_order_clause,
)
from app.ontologies.formal_modeling.derived import recompute_instance_derived
from app.ontologies.formal_modeling.validation import (
    validate_model, validate_instance_contract, validate_link_instance_contract,
)
from app.ontologies.versions.evolution_service import complete_snapshot
from app.ontologies.release_context import current_release_context
from app.schemas import ontology_formal as S
from app.services.formal.action_engine import execute_action
from app.services.formal.function_engine import test_function, compute_object_set_aggregates
from app.config import settings

router = APIRouter(dependencies=[Depends(ontology_access_guard)])
logger = logging.getLogger(__name__)


def _require_ontology(
        db: Session, ontology_id: str, *, for_update: bool = False) -> OntologyProject:
    query = db.query(OntologyProject).filter(OntologyProject.id == ontology_id)
    if for_update:
        query = query.with_for_update()
    p = query.first()
    if not p:
        raise HTTPException(404, "Ontology not found")
    return p


def _ok(data):
    return {"data": data}


def _naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalize persisted timestamps before comparing SQLite/Postgres rows."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _current_release_view(
        db: Session, project: OntologyProject) -> tuple[OntologyVersion, dict]:
    """Resolve the immutable release that owns the detail-page runtime view.

    ``current_release_id`` is the authority.  The ordered fallback only keeps
    legacy rows readable when their pointer was never backfilled; it must not
    replace a valid pointer by guessing from a version number.
    """
    release = None
    if project.current_release_id:
        release = db.query(OntologyVersion).filter(
            OntologyVersion.id == project.current_release_id,
            OntologyVersion.ontology_id == project.id,
            OntologyVersion.node_kind == "release",
        ).first()
    if release is None:
        release = db.query(OntologyVersion).filter(
            OntologyVersion.ontology_id == project.id,
            OntologyVersion.node_kind == "release",
        ).order_by(
            OntologyVersion.published_at.desc(),
            OntologyVersion.created_at.desc(),
        ).first()
    if release is None:
        raise HTTPException(409, detail={
            "code": "published_release_missing",
            "message": "本体还没有可展示的发布版本",
        })
    return release, complete_snapshot(release.snapshot_formal)


def _release_fact_query(
        db: Session,
        ontology_id: str,
        release: OntologyVersion,
        snapshot: dict,
):
    """Facts produced by, or after, the selected current release only."""
    object_type_ids = {
        str(item.get("id")) for item in snapshot["objectTypes"] if item.get("id")
    }
    link_type_ids = {
        str(item.get("id")) for item in snapshot["linkTypes"] if item.get("id")
    }
    sentinel_ids = {
        str(item.get("id")) for item in snapshot["sentinels"] if item.get("id")
    }
    action_ids = {
        str(item.get("id")) for item in snapshot["actions"] if item.get("id")
    }
    log_ids = {
        item[0] for item in db.query(ActionExecutionLog.id).filter(
            ActionExecutionLog.ontology_id == ontology_id,
            ActionExecutionLog.ontology_version == release.version_number,
            ActionExecutionLog.action_id.in_(action_ids),
        ).all()
    } if action_ids else set()

    subjects = []
    schema_ids = object_type_ids | link_type_ids
    if schema_ids:
        subjects.append(PropertyFact.object_type_id.in_(schema_ids))
    if log_ids:
        subjects.append(and_(
            PropertyFact.kind == "decision",
            PropertyFact.instance_id.in_(log_ids),
        ))
    if sentinel_ids:
        subjects.append(and_(
            PropertyFact.kind == "absence",
            PropertyFact.instance_id.in_(sentinel_ids),
        ))

    query = db.query(PropertyFact).filter(PropertyFact.ontology_id == ontology_id)
    query = query.filter(or_(*subjects) if subjects else false())
    published_at = _naive_utc(release.published_at or release.created_at)
    release_source = f"ontology-release://{release.id}"
    if published_at is not None:
        query = query.filter(or_(
            PropertyFact.source == release_source,
            PropertyFact.recorded_at >= published_at,
        ))
    else:
        query = query.filter(PropertyFact.source == release_source)
    return query


def _approval_instance_label(instance: ObjectInstance, object_type: Optional[ObjectType]) -> str:
    """为审批待办选择稳定、可读的业务标签，不把整份 properties 暴露给界面。"""
    properties = instance.properties if isinstance(instance.properties, dict) else {}
    candidates: list[str] = []
    primary_key = getattr(object_type, "primary_key", None)
    if primary_key:
        candidates.append(primary_key)
        for prop in (getattr(object_type, "properties", None) or []):
            if not isinstance(prop, dict):
                continue
            prop_id, prop_name = prop.get("id"), prop.get("name")
            if prop_id == primary_key and prop_name:
                candidates.append(prop_name)
            elif prop_name == primary_key and prop_id:
                candidates.append(prop_id)
    candidates.extend(("name", "title", "label", "code", "month", "report_month"))

    value = None
    for key in dict.fromkeys(candidates):
        candidate = properties.get(key)
        if candidate is not None and not isinstance(candidate, (dict, list)) and str(candidate).strip():
            value = str(candidate).strip()
            break
    if value is None and instance.external_id:
        value = str(instance.external_id)
    if value is None:
        value = instance.id[:10]

    type_name = None
    if object_type is not None:
        type_name = object_type.display_name or object_type.name
    return f"{type_name} · {value}" if type_name else value


def _raise_validation_failed(errors: list[dict], message: str = "运行契约校验未通过") -> None:
    if errors:
        raise HTTPException(422, detail={
            "code": "validation_failed",
            "message": f"{message}（{len(errors)} 个错误）",
            "errors": errors,
        })


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
        q(ObjectType), q(LinkType), q(ActionType), q(OntologyFunction),
        q(ObjectInstance), q(LinkInstance),
    )


def _orm_view(obj, updates: Optional[dict] = None):
    """生成无 ORM 副作用的候选视图，避免 422 后脏对象留在复用 Session 中。"""
    data = {column.key: getattr(obj, column.key) for column in obj.__table__.columns}
    data.update(updates or {})
    return SimpleNamespace(**data)


# ============ 保存共用辅助（全量 PUT 与增量 PATCH 复用） ============

FIELDS_OBJECT_TYPE = ["name", "display_name", "description", "icon", "color", "primary_key",
                      "properties", "interfaces", "position_x", "position_y"]
FIELDS_FUNCTION = ["name", "display_name", "description", "function_type", "language",
                   "target_object_type_id", "target_action_id", "parameters", "return_type",
                   "body", "cache_strategy", "cache_ttl", "enabled"]
FIELDS_ACTION = ["name", "display_name", "description", "object_type_id", "parameters",
                 "rules", "validation_function_id", "requires_approval"]
FIELDS_LINK_TYPE = ["name", "display_name", "description", "source_object_type_id",
                    "target_object_type_id", "cardinality", "source_role", "target_role", "properties"]
FIELDS_INSTANCE = ["object_type_id", "properties", "computed", "source", "external_id"]
FIELDS_LINK_INSTANCE = ["link_type_id", "source_object_id", "target_object_id", "properties"]


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
            result[seen[key]] = prop  # 后写覆盖前值
        else:
            seen[key] = len(result)
            result.append(prop)
    return result


def _upsert_items(db: Session, ontology_id: str, model, items, fields) -> set[str]:
    """按 id upsert，返回涉及的 id 集合（不删除未提及的记录）。"""
    existing = {x.id: x for x in db.query(model).filter(model.ontology_id == ontology_id).all()}
    keep_ids: set[str] = set()
    for item in items:
        data = item.model_dump(exclude_none=False)
        item_id = data.pop("id", None)
        if "properties" in data:
            data["properties"] = _dedup_properties(data["properties"])
        payload = {k: data[k] for k in fields if k in data}
        if item_id and item_id in existing:
            obj = existing[item_id]
            # 采集溯源保护：编辑器保存不得清洗实例的 source/external_id ——
            # 空值不覆盖；已有非 manual 出处（collector/import/action）不被降级为 manual，
            # 否则去重键被毁、下次采集必产生重复实例。
            if model is ObjectInstance:
                if payload.get("source") in (None, "") or (
                        payload.get("source") == "manual"
                        and (obj.source or "manual") != "manual"):
                    payload.pop("source", None)
                if payload.get("external_id") in (None, ""):
                    payload.pop("external_id", None)
            for k, v in payload.items():
                setattr(obj, k, v)
            keep_ids.add(item_id)
        else:
            kwargs = dict(payload)
            if item_id:
                kwargs["id"] = item_id
            obj = model(ontology_id=ontology_id, **kwargs)
            db.add(obj)
            db.flush()
            keep_ids.add(obj.id)
    return keep_ids


def _scrub_orphan_data(db: Session, ontology_id: str, actor_id: Optional[str] = None) -> dict:
    """实例层孤儿清理（安全网）：类型已不存在的实例、引用悬挂的链接实例。

    增量保存时前端应显式提交级联删除；此处兜底防止漏发造成孤儿数据。
    被清理的实例/链接同步写入墓碑/存在性事实——投影删了，历史真理流仍完整。
    """
    ot_ids = {x.id for x in db.query(ObjectType.id).filter(ObjectType.ontology_id == ontology_id)}
    lt_ids = {x.id for x in db.query(LinkType.id).filter(LinkType.ontology_id == ontology_id)}
    n_inst = n_li = 0
    for inst in db.query(ObjectInstance).filter(ObjectInstance.ontology_id == ontology_id).all():
        if inst.object_type_id not in ot_ids:
            record_object_tombstone(
                db, ontology_id=ontology_id, instance_id=inst.id,
                object_type_id=inst.object_type_id, source="editor-save:cascade",
                actor_id=actor_id)
            db.delete(inst)
            n_inst += 1
    remaining = {x.id for x in db.query(ObjectInstance.id).filter(ObjectInstance.ontology_id == ontology_id)}
    for li in db.query(LinkInstance).filter(LinkInstance.ontology_id == ontology_id).all():
        if (li.link_type_id not in lt_ids
                or li.source_object_id not in remaining
                or li.target_object_id not in remaining):
            record_link_fact(
                db, ontology_id=ontology_id, link_instance_id=li.id,
                link_type_id=li.link_type_id, exists=False,
                source="editor-save:cascade", actor_id=actor_id)
            db.delete(li)
            n_li += 1
    return {"instances": n_inst, "linkInstances": n_li}


def _revision_of(p: OntologyProject) -> str:
    """本体的乐观并发修订戳（updated_at 归一为 UTC-naive ISO 串）。

    客户端在 GET /full 拿到后原样带回，服务端字符串精确比对——
    归一化保证同一时刻在 aware/naive 两种存储形态下产出相同的串。
    """
    dt = p.updated_at or p.created_at
    if dt is None:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


# ============================================================
#  Full Ontology — 一次性加载（供图谱编辑页）
# ============================================================
@router.get("/{ontology_id}/full")
def get_full_ontology(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = _require_ontology(db, ontology_id)

    def q(model):
        return db.query(model).filter(model.ontology_id == ontology_id).all()

    # 当前发布版的画布布局独立于模型快照保存。运行投影读取时覆盖展示坐标，
    # 但不把纯视觉调整伪装成一次模式层发布。
    canvas_layout: dict = {}
    if p.current_release_id:
        release = db.query(OntologyVersion).filter(
            OntologyVersion.id == p.current_release_id,
            OntologyVersion.ontology_id == ontology_id,
        ).first()
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
        id=p.id, name=p.name, description=p.description, version=p.version or "1.0.0",
        revision=_revision_of(p),
        object_types=object_types,
        link_types=[S.LinkTypeOut.model_validate(x) for x in q(LinkType)],
        actions=[S.ActionTypeOut.model_validate(x) for x in q(ActionType)],
        functions=[S.FunctionOut.model_validate(x) for x in q(OntologyFunction)],
        instances=[S.ObjectInstanceOut.model_validate(x) for x in q(ObjectInstance)],
        link_instances=[S.LinkInstanceOut.model_validate(x) for x in q(LinkInstance)],
        execution_logs=[S.ActionLogOut.model_validate(x) for x in
                        db.query(ActionExecutionLog)
                          .filter(ActionExecutionLog.ontology_id == ontology_id)
                          .order_by(ActionExecutionLog.executed_at.desc()).limit(200).all()],
    )
    return _ok(out.model_dump(by_alias=True))


# ============================================================
#  Full Ontology — 一次性回写（图谱编辑页保存）
# ============================================================
@router.put("/{ontology_id}/full")
def save_full_ontology(ontology_id: str, body: S.SaveFullOntologyRequest,
                       db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """用前端图谱编辑页的当前状态整体替换该本体下的建模 + 实例数据。

    采用 upsert + 清理：保留 body 中带 id 的记录（按 id upsert），
    删除库中存在但 body 未提供的记录。执行日志不在此处理。
    """
    p = _require_ontology(db, ontology_id, for_update=True)
    _require_schema_draft(p)
    # 本请求随后会同步调用 run_for_save 评估哨兵——抑制 CDC 后台线程的并行评估，
    # 否则两边同时做边沿差分，动作可能被重复执行
    from app.ontologies.sentinels.cdc import SUPPRESS_KEY
    db.info[SUPPRESS_KEY] = True

    # 乐观并发检测：客户端携带加载时的 revision，与当前不一致说明
    # 期间有其他会话保存过 —— 拒绝写入，避免"后保存者静默覆盖"
    if body.base_revision is not None:
        current = _revision_of(p)
        if body.base_revision != current:
            raise HTTPException(409, detail={
                "code": "conflict",
                "message": "该本体已被其他会话修改，请重新加载后再保存",
                "currentRevision": current,
            })

    # 服务端强制校验：模式层与运行层硬错误统一以 422 拒绝。
    errors = validate_model(body.object_types, body.link_types, body.actions,
                            body.functions, body.instances, body.link_instances)
    _raise_validation_failed(errors, "模型校验未通过，已拒绝保存")

    # 同步本体元信息（可选字段）
    if body.name is not None:
        p.name = body.name
    if body.description is not None:
        p.description = body.description
    if body.version is not None and body.version != p.version:
        raise HTTPException(409, "版本号由发布流程管理，full-save 不能直接修改")

    actor = getattr(current_user, "id", None)

    def _sync(model, items, fields):
        """按 id upsert，并删除不在 items 内的旧记录（全量替换语义）。"""
        keep_ids = _upsert_items(db, ontology_id, model, items, fields)
        for obj in db.query(model).filter(model.ontology_id == ontology_id).all():
            if obj.id not in keep_ids:
                db.delete(obj)

    # 顺序很重要：先建对象类型/接口/函数/动作，再链接，再实例/链接实例
    _sync(ObjectType, body.object_types, FIELDS_OBJECT_TYPE)
    _sync(OntologyFunction, body.functions, FIELDS_FUNCTION)
    _sync(ActionType, body.actions, FIELDS_ACTION)
    _sync(LinkType, body.link_types, FIELDS_LINK_TYPE)
    # Fact 溯源：在实例投影被覆盖前抓取旧属性/链接快照，保存后逐条追加事实
    prev_instances = {
        x.id: x for x in
        db.query(ObjectInstance).filter(ObjectInstance.ontology_id == ontology_id).all()
    }
    prev_instance_props = {iid: dict(x.properties or {}) for iid, x in prev_instances.items()}
    prev_links = {
        x.id: x.link_type_id for x in
        db.query(LinkInstance).filter(LinkInstance.ontology_id == ontology_id).all()
    }

    _sync(ObjectInstance, body.instances, FIELDS_INSTANCE)
    _sync(LinkInstance, body.link_instances, FIELDS_LINK_INSTANCE)

    # 被整体保存移除的实例 → 墓碑事实（先于属性事实，保证回放语义完整）
    kept_ids = {inst.id for inst in body.instances if inst.id}
    for gone_id, gone in prev_instances.items():
        if gone_id not in kept_ids:
            record_object_tombstone(
                db, ontology_id=ontology_id, instance_id=gone_id,
                object_type_id=gone.object_type_id, source="editor-save", actor_id=actor)

    # 链接存在性事实：diff 新旧链接集合（Link 也是 Fact）
    cur_links = {
        x.id: x.link_type_id for x in
        db.query(LinkInstance).filter(LinkInstance.ontology_id == ontology_id).all()
    }
    for lid, lt in cur_links.items():
        if lid not in prev_links:
            record_link_fact(db, ontology_id=ontology_id, link_instance_id=lid,
                             link_type_id=lt, exists=True, source="editor-save", actor_id=actor)
    for lid, lt in prev_links.items():
        if lid not in cur_links:
            record_link_fact(db, ontology_id=ontology_id, link_instance_id=lid,
                             link_type_id=lt, exists=False, source="editor-save", actor_id=actor)

    facts_added = 0
    for inst in body.instances:
        if not inst.id:
            continue  # 无 id 的新实例由 _sync 生成 id，此处无法关联，跳过（编辑器始终带 id）
        created = record_property_facts(
            db,
            ontology_id=ontology_id,
            instance_id=inst.id,
            object_type_id=inst.object_type_id,
            old_props=prev_instance_props.get(inst.id),
            new_props=inst.properties or {},
            source="editor-save",
            actor_id=actor,
        )
        facts_added += len(created)
        # 存储属性变了 → 该实例的派生属性自动重算（写投影 + 追加 derived 事实）
        if created:
            row = db.query(ObjectInstance).filter(
                ObjectInstance.id == inst.id,
                ObjectInstance.ontology_id == ontology_id).first()
            if row:
                recompute_instance_derived(
                    db, ontology_id=ontology_id, instance=row, trigger_facts=created)

    # 显式推进修订戳：即使元信息字段无变化，fo_* 数据的保存也必须产生新 revision
    p.updated_at = datetime.now(timezone.utc)

    # 1) 先提交核心保存 —— 保证用户的建模改动一定落库
    db.commit()

    # 2) 引用完整性清理为"尽力而为":即便它出错,也绝不回滚/阻断已成功的核心保存
    try:
        changed = _scrub_dangling_references(db, ontology_id)
        if any(changed.values()):
            db.commit()
        else:
            db.rollback()  # 无改动,释放本次只读查询占用
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

    resp = get_full_ontology(ontology_id, db)
    if sentinel_summary is not None:
        resp["data"]["sentinelSummary"] = {
            "evaluated": sentinel_summary.get("evaluated", 0),
            "fired": sentinel_summary.get("fired", 0),
        }
    return resp


# ============================================================
#  Full Ontology — 增量回写（图谱编辑页 delta 保存）
# ============================================================
@router.patch("/{ontology_id}/full")
def patch_full_ontology(ontology_id: str, body: S.PatchOntologyRequest,
                        db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """只应用自上次保存以来的变更/删除，机制与 PUT /full 完全等价：
    并发检测 → 合并视图强制校验 → 应用 delta → 属性事实 + 派生重算 →
    孤儿清理 → 哨兵评估。负载 O(变更) 而非 O(全模型)。
    """
    p = _require_ontology(db, ontology_id, for_update=True)
    _require_schema_draft(p)
    from app.ontologies.sentinels.cdc import SUPPRESS_KEY
    db.info[SUPPRESS_KEY] = True

    if body.base_revision is not None:
        current = _revision_of(p)
        if body.base_revision != current:
            raise HTTPException(409, detail={
                "code": "conflict",
                "message": "该本体已被其他会话修改，请重新加载后再保存",
                "currentRevision": current,
            })

    up, dele = body.upserts, body.deletes

    # 合并视图（现库 − 删除 + upsert）上做与全量保存相同的强制校验
    def merged(model, upserts, delete_ids):
        cur = {x.id: x for x in db.query(model).filter(model.ontology_id == ontology_id).all()}
        for did in delete_ids:
            cur.pop(did, None)
        out: dict = dict(cur)
        for i, u in enumerate(upserts):
            out[u.id or f"__new_{i}"] = u
        return list(out.values())

    errors = validate_model(
        merged(ObjectType, up.object_types, dele.object_types),
        merged(LinkType, up.link_types, dele.link_types),
        merged(ActionType, up.actions, dele.actions),
        merged(OntologyFunction, up.functions, dele.functions),
        merged(ObjectInstance, up.instances, dele.instances),
        merged(LinkInstance, up.link_instances, dele.link_instances),
    )
    _raise_validation_failed(errors, "模型校验未通过，已拒绝保存")

    # 元信息
    if body.name is not None:
        p.name = body.name
    if body.description is not None:
        p.description = body.description
    if body.version is not None and body.version != p.version:
        raise HTTPException(409, "版本号由发布流程管理，PATCH 不能直接修改")

    actor = getattr(current_user, "id", None)

    # 旧属性快照（仅本次 upsert 的实例），供事实追加
    up_inst_ids = [i.id for i in up.instances if i.id]
    prev_props = {}
    if up_inst_ids:
        prev_props = {
            x.id: dict(x.properties or {})
            for x in db.query(ObjectInstance).filter(
                ObjectInstance.ontology_id == ontology_id,
                ObjectInstance.id.in_(up_inst_ids)).all()
        }

    # 删除（前端级联删除已展开为显式 id 列表）——先写墓碑/存在性事实再删投影
    if dele.link_instances:
        for li in db.query(LinkInstance).filter(
                LinkInstance.ontology_id == ontology_id,
                LinkInstance.id.in_(dele.link_instances)).all():
            record_link_fact(db, ontology_id=ontology_id, link_instance_id=li.id,
                             link_type_id=li.link_type_id, exists=False,
                             source="editor-save", actor_id=actor)
    if dele.instances:
        for inst in db.query(ObjectInstance).filter(
                ObjectInstance.ontology_id == ontology_id,
                ObjectInstance.id.in_(dele.instances)).all():
            record_object_tombstone(db, ontology_id=ontology_id, instance_id=inst.id,
                                    object_type_id=inst.object_type_id,
                                    source="editor-save", actor_id=actor)

    def _delete(model, ids):
        if ids:
            db.query(model).filter(model.ontology_id == ontology_id,
                                   model.id.in_(ids)).delete(synchronize_session=False)
    _delete(LinkInstance, dele.link_instances)
    _delete(ObjectInstance, dele.instances)
    _delete(LinkType, dele.link_types)
    _delete(ActionType, dele.actions)
    _delete(OntologyFunction, dele.functions)
    _delete(ObjectType, dele.object_types)

    # 链接快照（供 diff 出新建链接的存在性事实）
    prev_link_ids = {x.id for x in db.query(LinkInstance.id).filter(
        LinkInstance.ontology_id == ontology_id)}

    # upsert（与全量保存相同的顺序）
    _upsert_items(db, ontology_id, ObjectType, up.object_types, FIELDS_OBJECT_TYPE)
    _upsert_items(db, ontology_id, OntologyFunction, up.functions, FIELDS_FUNCTION)
    _upsert_items(db, ontology_id, ActionType, up.actions, FIELDS_ACTION)
    _upsert_items(db, ontology_id, LinkType, up.link_types, FIELDS_LINK_TYPE)
    _upsert_items(db, ontology_id, ObjectInstance, up.instances, FIELDS_INSTANCE)
    _upsert_items(db, ontology_id, LinkInstance, up.link_instances, FIELDS_LINK_INSTANCE)

    # 新建链接 → 存在性事实（Link 也是 Fact）
    for li in db.query(LinkInstance).filter(LinkInstance.ontology_id == ontology_id).all():
        if li.id not in prev_link_ids:
            record_link_fact(db, ontology_id=ontology_id, link_instance_id=li.id,
                             link_type_id=li.link_type_id, exists=True,
                             source="editor-save", actor_id=actor)

    # 孤儿清理安全网（前端漏发级联删除时兜底）
    pruned = _scrub_orphan_data(db, ontology_id, actor_id=actor)
    if any(pruned.values()):
        logger.info("增量保存孤儿清理: %s (ontology=%s)", pruned, ontology_id)

    # 属性事实 + 派生重算（仅涉及本次 upsert 且仍存在的实例——
    # 实例可能已被上方级联/孤儿清理删除，跳过以免产生指向空实例的孤儿事实）
    for inst in up.instances:
        if not inst.id:
            continue
        row = db.query(ObjectInstance).filter(
            ObjectInstance.id == inst.id,
            ObjectInstance.ontology_id == ontology_id).first()
        if row is None:
            continue
        created = record_property_facts(
            db, ontology_id=ontology_id, instance_id=inst.id,
            object_type_id=inst.object_type_id,
            old_props=prev_props.get(inst.id),
            new_props=inst.properties or {},
            source="editor-save",
            actor_id=actor,
        )
        if created:
            recompute_instance_derived(
                db, ontology_id=ontology_id, instance=row, trigger_facts=created)

    p.updated_at = datetime.now(timezone.utc)
    db.commit()

    # 引用完整性清理（尽力而为，同 PUT）
    try:
        changed = _scrub_dangling_references(db, ontology_id)
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
    p = _require_ontology(db, ontology_id)
    updated_instances = []
    if up_inst_ids:
        updated_instances = [
            S.ObjectInstanceOut.model_validate(x).model_dump(by_alias=True)
            for x in db.query(ObjectInstance).filter(
                ObjectInstance.ontology_id == ontology_id,
                ObjectInstance.id.in_(up_inst_ids)).all()
        ]
    data = {"revision": _revision_of(p), "instances": updated_instances}
    if sentinel_summary is not None:
        data["sentinelSummary"] = {
            "evaluated": sentinel_summary.get("evaluated", 0),
            "fired": sentinel_summary.get("fired", 0),
        }
    return _ok(data)


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
    ot_ids = {x.id for x in db.query(ObjectType.id).filter(ObjectType.ontology_id == ontology_id)}
    lt_ids = {x.id for x in db.query(LinkType.id).filter(LinkType.ontology_id == ontology_id)}
    fn_ids = {x.id for x in db.query(OntologyFunction.id).filter(OntologyFunction.ontology_id == ontology_id)}
    act_ids = {x.id for x in db.query(ActionType.id).filter(ActionType.ontology_id == ontology_id)}
    counts = {"action_rule_refs": 0, "validation_fn_refs": 0,
              "sentinel_actions": 0, "sentinel_bindings": 0, "sentinel_links": 0}

    def _blank_if_dangling(cfg: dict, key: str, valid: set) -> bool:
        val = cfg.get(key)
        if val and val not in valid:
            cfg[key] = ""
            return True
        return False

    # —— 1. 动作 rules + validation_function_id ——
    for act in db.query(ActionType).filter(ActionType.ontology_id == ontology_id):
        dirty = False
        rules = act.rules or []
        for rule in rules:
            cfg = rule.get("config") if isinstance(rule, dict) else None
            if not isinstance(cfg, dict):
                continue
            if _blank_if_dangling(cfg, "targetObjectTypeId", ot_ids):
                counts["action_rule_refs"] += 1; dirty = True
            if _blank_if_dangling(cfg, "linkTypeId", lt_ids):
                counts["action_rule_refs"] += 1; dirty = True
            if _blank_if_dangling(cfg, "functionId", fn_ids):
                counts["action_rule_refs"] += 1; dirty = True
            for m in cfg.get("propertyMappings", []) or []:
                if isinstance(m, dict) and _blank_if_dangling(m, "functionId", fn_ids):
                    counts["action_rule_refs"] += 1; dirty = True
        if dirty:
            flag_modified(act, "rules")  # JSON 原地修改需显式标脏,否则不落库

        if act.validation_function_id and act.validation_function_id not in fn_ids:
            act.validation_function_id = None
            counts["validation_fn_refs"] += 1

    # —— 2. 哨兵(跨层)——
    try:
        from app.models.sentinel import Sentinel
    except Exception:
        Sentinel = None
    if Sentinel is not None:
        for s in db.query(Sentinel).filter(Sentinel.ontology_id == ontology_id):
            # 动作引用
            aids = s.action_ids or []
            kept_aids = [a for a in aids if a in act_ids]
            if len(kept_aids) != len(aids):
                counts["sentinel_actions"] += len(aids) - len(kept_aids)
                s.action_ids = kept_aids
            # 绑定的对象类型引用
            binds = s.bindings or []
            kept_binds = [b for b in binds if b.get("objectTypeId") in ot_ids]
            if len(kept_binds) != len(binds):
                counts["sentinel_bindings"] += len(binds) - len(kept_binds)
                s.bindings = kept_binds
                # primary_alias 若指向被删绑定则重置为首个存活绑定
                if s.primary_alias not in {b.get("alias") for b in kept_binds}:
                    s.primary_alias = kept_binds[0]["alias"] if kept_binds else None
            # 关系约束的链接类型引用
            lks = s.links or []
            kept_lks = [l for l in lks if l.get("linkTypeId") in lt_ids]
            if len(kept_lks) != len(lks):
                counts["sentinel_links"] += len(lks) - len(kept_lks)
                s.links = kept_lks

    return counts


# ============================================================
#  Generic CRUD factory
# ============================================================
def _crud(model, create_schema, update_schema, out_schema, name: str):
    sub = APIRouter()
    state_index = {
        ObjectType: 0, LinkType: 1, ActionType: 2, OntologyFunction: 3,
    }

    @sub.get(f"/{{ontology_id}}/{name}")
    def list_items(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
        _require_ontology(db, ontology_id)
        items = db.query(model).filter(model.ontology_id == ontology_id).all()
        return _ok([out_schema.model_validate(x).model_dump(by_alias=True) for x in items])

    @sub.post(f"/{{ontology_id}}/{name}", status_code=201)
    def create_item(ontology_id: str, body: create_schema, db: Session = Depends(get_db), _=Depends(get_current_user)):  # type: ignore
        project = _require_ontology(db, ontology_id, for_update=True)
        _require_schema_draft(project)
        data = body.model_dump(exclude_none=True)
        obj = model(id=str(uuid.uuid4()), ontology_id=ontology_id, **data)
        state = list(_runtime_state(db, ontology_id))
        state[state_index[model]] = [*state[state_index[model]], obj]
        _raise_validation_failed(validate_model(*state), f"{name} 创建被拒绝")
        project.updated_at = datetime.now(timezone.utc)
        db.add(obj); db.commit(); db.refresh(obj)
        return _ok(out_schema.model_validate(obj).model_dump(by_alias=True))

    @sub.get(f"/{{ontology_id}}/{name}/{{item_id}}")
    def get_item(ontology_id: str, item_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
        obj = db.query(model).filter(model.id == item_id, model.ontology_id == ontology_id).first()
        if not obj:
            raise HTTPException(404, "Not found")
        return _ok(out_schema.model_validate(obj).model_dump(by_alias=True))

    @sub.put(f"/{{ontology_id}}/{name}/{{item_id}}")
    def update_item(ontology_id: str, item_id: str, body: update_schema, db: Session = Depends(get_db), _=Depends(get_current_user)):  # type: ignore
        project = _require_ontology(db, ontology_id, for_update=True)
        _require_schema_draft(project)
        obj = db.query(model).filter(model.id == item_id, model.ontology_id == ontology_id).first()
        if not obj:
            raise HTTPException(404, "Not found")
        updates = body.model_dump(exclude_unset=True)
        candidate = _orm_view(obj, updates)
        state = list(_runtime_state(db, ontology_id))
        state[state_index[model]] = [
            candidate if item.id == item_id else item
            for item in state[state_index[model]]
        ]
        _raise_validation_failed(validate_model(*state), f"{name} 更新被拒绝")
        for k, v in updates.items():
            setattr(obj, k, v)
        project.updated_at = datetime.now(timezone.utc)
        db.commit(); db.refresh(obj)
        return _ok(out_schema.model_validate(obj).model_dump(by_alias=True))

    @sub.delete(f"/{{ontology_id}}/{name}/{{item_id}}", status_code=204)
    def delete_item(ontology_id: str, item_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
        project = _require_ontology(db, ontology_id, for_update=True)
        _require_schema_draft(project)
        obj = db.query(model).filter(model.id == item_id, model.ontology_id == ontology_id).first()
        if not obj:
            raise HTTPException(404, "Not found")
        state = list(_runtime_state(db, ontology_id))
        state[state_index[model]] = [
            item for item in state[state_index[model]] if item.id != item_id
        ]
        _raise_validation_failed(validate_model(*state), f"{name} 删除会造成悬挂引用，已拒绝")
        project.updated_at = datetime.now(timezone.utc)
        db.delete(obj); db.commit()

    return sub


router.include_router(_crud(ObjectType, S.ObjectTypeCreate, S.ObjectTypeUpdate, S.ObjectTypeOut, "object-types"))
router.include_router(_crud(LinkType, S.LinkTypeCreate, S.LinkTypeUpdate, S.LinkTypeOut, "link-types"))
router.include_router(_crud(ActionType, S.ActionTypeCreate, S.ActionTypeUpdate, S.ActionTypeOut, "actions"))
router.include_router(_crud(OntologyFunction, S.FunctionCreate, S.FunctionUpdate, S.FunctionOut, "functions"))


# ============================================================
#  Object Instances (数据采集落地点 — 自定义以支持 external_id 去重)
# ============================================================
@router.get("/{ontology_id}/instances")
def list_instances(ontology_id: str, object_type_id: Optional[str] = None,
                   db: Session = Depends(get_db), _=Depends(get_current_user)):
    _require_ontology(db, ontology_id)
    q = db.query(ObjectInstance).filter(ObjectInstance.ontology_id == ontology_id)
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


@router.get("/{ontology_id}/instance-browser/catalog")
def instance_browser_catalog(
        ontology_id: str,
        db: Session = Depends(get_db),
        _=Depends(get_current_user)):
    """Published schema tree plus counts from its current runtime projection."""
    project = _require_ontology(db, ontology_id)
    release, snapshot = _current_release_view(db, project)
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
        ObjectInstance.object_type_id.in_(object_type_ids),
    ).group_by(ObjectInstance.object_type_id).all()) if object_type_ids else {}
    link_counts = dict(db.query(
        LinkInstance.link_type_id, func.count(LinkInstance.id),
    ).filter(
        LinkInstance.ontology_id == ontology_id,
        LinkInstance.link_type_id.in_(link_type_ids),
    ).group_by(LinkInstance.link_type_id).all()) if link_type_ids else {}

    return _ok({
        "release": _instance_browser_release(release),
        "objectTypes": [
            {**item, "instanceCount": object_counts.get(str(item.get("id")), 0)}
            for item in snapshot["objectTypes"]
        ],
        "linkTypes": [
            {**item, "instanceCount": link_counts.get(str(item.get("id")), 0)}
            for item in snapshot["linkTypes"]
        ],
    })


@router.get("/{ontology_id}/instance-browser/objects")
def instance_browser_objects(
        ontology_id: str,
        object_type_id: str = Query(..., min_length=1),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        keyword: Optional[str] = Query(None, max_length=200),
        db: Session = Depends(get_db),
        _=Depends(get_current_user)):
    project = _require_ontology(db, ontology_id)
    release, snapshot = _current_release_view(db, project)
    _release_catalog_item(
        snapshot["objectTypes"], object_type_id, "对象实体",
    )
    query = db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology_id,
        ObjectInstance.object_type_id == object_type_id,
    )
    term = (keyword or "").strip()
    if term:
        pattern = f"%{term}%"
        query = query.filter(or_(
            ObjectInstance.id.ilike(pattern),
            ObjectInstance.external_id.ilike(pattern),
            ObjectInstance.source.ilike(pattern),
            cast(ObjectInstance.properties, String).ilike(pattern),
            cast(ObjectInstance.computed, String).ilike(pattern),
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


@router.get("/{ontology_id}/instance-browser/links")
def instance_browser_links(
        ontology_id: str,
        link_type_id: str = Query(..., min_length=1),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        keyword: Optional[str] = Query(None, max_length=200),
        db: Session = Depends(get_db),
        _=Depends(get_current_user)):
    project = _require_ontology(db, ontology_id)
    release, snapshot = _current_release_view(db, project)
    _release_catalog_item(snapshot["linkTypes"], link_type_id, "实体关系")
    query = db.query(LinkInstance).filter(
        LinkInstance.ontology_id == ontology_id,
        LinkInstance.link_type_id == link_type_id,
    )
    term = (keyword or "").strip()
    if term:
        pattern = f"%{term}%"
        query = query.filter(or_(
            LinkInstance.id.ilike(pattern),
            LinkInstance.source_object_id.ilike(pattern),
            LinkInstance.target_object_id.ilike(pattern),
            cast(LinkInstance.properties, String).ilike(pattern),
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


@router.post("/{ontology_id}/instances", status_code=201)
def create_instance(ontology_id: str, body: S.ObjectInstanceCreate,
                    db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _reject_direct_runtime_data_write()
    project = _require_ontology(db, ontology_id, for_update=True)
    data = body.model_dump(exclude_none=True)
    data["id"] = data.get("id") or str(uuid.uuid4())
    obj = ObjectInstance(ontology_id=ontology_id, **data)
    object_types = db.query(ObjectType).filter(ObjectType.ontology_id == ontology_id).all()
    instances = db.query(ObjectInstance).filter(ObjectInstance.ontology_id == ontology_id).all()
    errors = validate_instance_contract(object_types, [*instances, obj], validate_ids={obj.id})
    _raise_validation_failed(errors, "实例创建被拒绝")
    db.add(obj); db.flush()
    created = record_property_facts(
        db, ontology_id=ontology_id, instance_id=obj.id,
        object_type_id=obj.object_type_id,
        old_props=None, new_props=obj.properties or {},
        source=obj.source or "manual", actor_id=getattr(current_user, "id", None),
    )
    recompute_instance_derived(db, ontology_id=ontology_id, instance=obj, trigger_facts=created)
    project.updated_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(obj)
    return _ok(S.ObjectInstanceOut.model_validate(obj).model_dump(by_alias=True))


@router.put("/{ontology_id}/instances/{instance_id}")
def update_instance(ontology_id: str, instance_id: str, body: S.ObjectInstanceUpdate,
                    db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _reject_direct_runtime_data_write()
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
    db.commit(); db.refresh(obj)
    return _ok(S.ObjectInstanceOut.model_validate(obj).model_dump(by_alias=True))


@router.get("/{ontology_id}/instances/{instance_id}/facts")
def list_instance_facts(ontology_id: str, instance_id: str,
                        property_name: Optional[str] = None, limit: int = 200,
                        db: Session = Depends(get_db), _=Depends(get_current_user)):
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


def _fact_to_dict(f: PropertyFact) -> dict:
    return {
        "id": f.id,
        "instanceId": f.instance_id,
        "propertyName": f.property_name,
        "value": (f.value or {}).get("v"),
        "kind": f.kind or "property",
        "source": f.source,
        "actorId": f.actor_id,
        "causedBy": f.caused_by,
        "supersedesId": f.supersedes_id,
        "derivedFrom": f.derived_from or [],
        "confidence": f.confidence,
        "ontologyVersion": f.ontology_version,
        "seq": f.seq,
        "recordedAt": f.recorded_at.isoformat() if f.recorded_at else None,
    }


@router.get("/{ontology_id}/instances/{instance_id}/as-of")
def instance_as_of(ontology_id: str, instance_id: str, t: str,
                   db: Session = Depends(get_db), _=Depends(get_current_user)):
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
    # 每属性链是线性的（supersedes 单链），≤T 的最新事实即当时值
    latest: dict[str, PropertyFact] = {}
    for r in rows:
        latest.setdefault(r.property_name, r)

    exists = True
    props: dict = {}
    computed: dict = {}
    detail: dict = {}
    for name, f in latest.items():
        v = (f.value or {}).get("v")
        if f.kind == "object" and name == "exists":
            exists = bool(v)
            continue
        if f.kind == "decision":
            continue
        bucket = computed if f.kind == "derived" else props
        bucket[name] = v
        detail[name] = _fact_to_dict(f)
    return _ok({
        "instanceId": instance_id, "asOf": t, "exists": exists,
        "properties": props, "computed": computed, "facts": detail,
        "totalFacts": len(rows),
    })


@router.delete("/{ontology_id}/instances/{instance_id}", status_code=204)
def delete_instance(ontology_id: str, instance_id: str,
                    db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _reject_direct_runtime_data_write()
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
    db.delete(obj); db.commit()


# ============================================================
#  Link Instances
# ============================================================
@router.get("/{ontology_id}/link-instances")
def list_link_instances(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    _require_ontology(db, ontology_id)
    items = db.query(LinkInstance).filter(LinkInstance.ontology_id == ontology_id).all()
    return _ok([S.LinkInstanceOut.model_validate(x).model_dump(by_alias=True) for x in items])


@router.post("/{ontology_id}/link-instances", status_code=201)
def create_link_instance(ontology_id: str, body: S.LinkInstanceCreate,
                         db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _reject_direct_runtime_data_write()
    project = _require_ontology(db, ontology_id, for_update=True)
    obj = LinkInstance(id=str(uuid.uuid4()), ontology_id=ontology_id,
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
    db.commit(); db.refresh(obj)
    return _ok(S.LinkInstanceOut.model_validate(obj).model_dump(by_alias=True))


@router.delete("/{ontology_id}/link-instances/{link_id}", status_code=204)
def delete_link_instance(ontology_id: str, link_id: str,
                         db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _reject_direct_runtime_data_write()
    project = _require_ontology(db, ontology_id, for_update=True)
    obj = db.query(LinkInstance).filter(LinkInstance.id == link_id,
                                        LinkInstance.ontology_id == ontology_id).first()
    if not obj:
        raise HTTPException(404, "Not found")
    record_link_fact(db, ontology_id=ontology_id, link_instance_id=obj.id,
                     link_type_id=obj.link_type_id, exists=False,
                     source="manual", actor_id=getattr(current_user, "id", None))
    project.updated_at = datetime.now(timezone.utc)
    db.delete(obj); db.commit()


# ============================================================
#  Runtime: Run Action / Test Function / HITL 审批
# ============================================================
@router.post("/{ontology_id}/run-action")
def run_action(ontology_id: str, body: S.RunActionRequest,
               db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require_ontology(db, ontology_id, for_update=True)
    log = execute_action(db, ontology_id, body,
                         actor_id=getattr(current_user, "id", None))
    return _ok(log)


@router.get("/{ontology_id}/pending-actions")
def list_pending_actions(ontology_id: str, release_id: Optional[str] = None,
                         current_release_only: bool = False,
                         db: Session = Depends(get_db), _=Depends(get_current_user)):
    """待审批动作（requires_approval 的动作真实执行先落 pending，等人拍板）。"""
    project = _require_ontology(db, ontology_id)
    release_snapshot = None
    release_version = None
    if release_id:
        release = current_release_context(
            db, ontology_id, expected_release_id=release_id)
        release_snapshot = release.snapshot
        release_version = release.version
    elif current_release_only:
        release_row, release_snapshot = _current_release_view(db, project)
        release_version = release_row.version_number
    query = db.query(ActionExecutionLog).filter(
        ActionExecutionLog.ontology_id == ontology_id,
        ActionExecutionLog.status == "pending",
        ActionExecutionLog.dry_run == False,  # noqa: E712
    )
    released_actions_by_id = {}
    if release_snapshot is not None:
        released_actions_by_id = {
            str(item["id"]): item
            for item in release_snapshot["actions"] if item.get("id")
        }
        action_ids = set(released_actions_by_id)
        if not action_ids:
            return _ok([])
        query = query.filter(
            ActionExecutionLog.ontology_version == release_version,
            ActionExecutionLog.action_id.in_(action_ids),
        )
    items = query.order_by(
        ActionExecutionLog.executed_at.desc()).limit(100).all()
    instance_ids = {item.object_instance_id for item in items if item.object_instance_id}
    instances = (db.query(ObjectInstance)
                 .filter(ObjectInstance.ontology_id == ontology_id,
                         ObjectInstance.id.in_(instance_ids)).all()) if instance_ids else []
    instances_by_id = {item.id: item for item in instances}
    object_type_ids = {item.object_type_id for item in items if item.object_type_id}
    object_type_ids.update(item.object_type_id for item in instances if item.object_type_id)
    if release_snapshot is not None:
        object_types = [SimpleNamespace(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or ""),
            display_name=item.get("displayName") or item.get("display_name"),
            primary_key=item.get("primaryKey") or item.get("primary_key"),
            properties=item.get("properties") or [],
        ) for item in release_snapshot["objectTypes"]
            if str(item.get("id") or "") in object_type_ids]
    else:
        object_types = (db.query(ObjectType)
                        .filter(ObjectType.ontology_id == ontology_id,
                                ObjectType.id.in_(object_type_ids)).all()) if object_type_ids else []
    object_types_by_id = {item.id: item for item in object_types}

    result = []
    for item in items:
        instance = instances_by_id.get(item.object_instance_id)
        object_type = object_types_by_id.get(
            instance.object_type_id if instance is not None else item.object_type_id)
        payload = S.ActionLogOut.model_validate(item).model_dump(by_alias=True)
        payload.update({
            "actionName": (
                released_actions_by_id.get(item.action_id, {}).get("displayName")
                or released_actions_by_id.get(item.action_id, {}).get("name")
                or payload.get("actionName")
            ),
            "objectTypeName": ((object_type.display_name or object_type.name)
                               if object_type is not None else None),
            "objectInstanceLabel": (_approval_instance_label(instance, object_type)
                                    if instance is not None else None),
            "triggerSource": ("sentinel" if item.sentinel_match_state_id
                              else "manual" if item.actor_id else "system"),
        })
        result.append(payload)
    return _ok(result)


@router.post("/{ontology_id}/action-logs/{log_id}/decide")
def decide_pending_action(ontology_id: str, log_id: str, body: S.DecisionRequest,
                          db: Session = Depends(get_db),
                          current_user=Depends(require_admin)):
    """HITL 决策：批准 → 写决策事实并真正执行（执行产生的事实 caused_by=决策事实）；
    拒绝 → 同样写决策事实（拒绝也要溯源，供将来分析"哪些建议被人否决了"）。"""
    project = _require_ontology(db, ontology_id, for_update=True)
    log = db.query(ActionExecutionLog).filter(
        ActionExecutionLog.id == log_id,
        ActionExecutionLog.ontology_id == ontology_id).first()
    if not log:
        raise HTTPException(404, "执行记录不存在")
    if log.status != "pending":
        raise HTTPException(409, f"该记录已处理（当前状态: {log.status}），不能重复决策")
    decision = (body.decision or "").lower()
    if decision not in ("approved", "rejected"):
        raise HTTPException(422, "decision 必须是 approved 或 rejected")
    release = (
        current_release_context(
            db, ontology_id, expected_release_id=body.release_id)
        if body.release_id else None
    )
    current_version = release.version if release is not None else project.version
    if release is not None:
        if not log.ontology_version or log.ontology_version != current_version:
            raise HTTPException(
                409,
                f"该动作不属于当前发布本体 {current_version}，跨版本审批已拒绝",
            )
    elif decision == "approved":
        if not log.ontology_version:
            raise HTTPException(409, "该待办动作缺少本体版本血缘，不能安全批准；可选择拒绝")
        if log.ontology_version != current_version:
            raise HTTPException(
                409,
                f"该动作属于本体 {log.ontology_version}，当前为 {current_version}；"
                "跨版本审批已拒绝",
            )

    uid = getattr(current_user, "id", None)
    uname = getattr(current_user, "username", None) or getattr(current_user, "email", None) or uid
    # 决策事实：无论批准/拒绝都进入事实流（kind=decision）
    fact = record_decision_fact(
        db, ontology_id=ontology_id, action_log_id=log.id,
        decision=decision.upper(), source=f"user://{uname}",
        actor_id=uid, reason=body.reason,
        ontology_version=log.ontology_version)

    log.decided_by = uid
    log.decided_at = datetime.now(timezone.utc)
    log.decision_reason = body.reason

    if decision == "rejected":
        log.status = "rejected"
        # Rejection is terminal for this approval proposal but not for the
        # business edge.  Release the unique key/claim so a later evaluation
        # can create one new, auditable attempt rather than replaying rejection.
        state_id = log.sentinel_match_state_id
        log.idempotency_key = None
        db.commit(); db.refresh(log)
        if state_id:
            from app.services.sentinel.evaluator import reject_sentinel_match_claim
            reject_sentinel_match_claim(db, ontology_id, state_id)
        return _ok(S.ActionLogOut.model_validate(log).model_dump(by_alias=True))

    # 批准：以原参数真正执行；执行事实的因果指针指向决策事实（对齐 caused_by=f010 语义）
    from types import SimpleNamespace
    exec_body = SimpleNamespace(action_id=log.action_id, parameters=log.parameters or {},
                                target_instance_id=log.object_instance_id, dry_run=False,
                                sentinel_match_state_id=log.sentinel_match_state_id)
    sentinel_token = None
    if log.sentinel_match_state_id:
        from app.services.sentinel.evaluator import in_sentinel_run
        sentinel_token = in_sentinel_run.set(True)
    try:
        result = execute_action(db, ontology_id, exec_body, actor_id=uid,
                                caused_by_fact=fact.id, skip_approval=True)
    finally:
        if sentinel_token is not None:
            in_sentinel_run.reset(sentinel_token)
    log.status = "approved"
    log.related_log_id = result.get("id")
    state_id = log.sentinel_match_state_id
    execution_succeeded = result.get("status") == "success"
    if not execution_succeeded:
        # Human approval and technical execution are separate facts.  A failed
        # execution must not permanently own the step's idempotency key.
        log.idempotency_key = None
    db.commit(); db.refresh(log)
    sentinel_resume = None
    if state_id:
        if execution_succeeded:
            from app.services.sentinel.evaluator import resume_sentinel_match_claim
            sentinel_resume = resume_sentinel_match_claim(db, ontology_id, state_id)
        else:
            from app.services.sentinel.evaluator import fail_sentinel_match_claim
            sentinel_resume = fail_sentinel_match_claim(db, ontology_id, state_id)
    return _ok({
        "pendingLog": S.ActionLogOut.model_validate(log).model_dump(by_alias=True),
        "executionLog": result,
        "decisionFactId": fact.id,
        "sentinelResume": sentinel_resume,
    })


@router.post("/{ontology_id}/test-function")
def run_test_function(ontology_id: str, body: S.TestFunctionRequest,
                      db: Session = Depends(get_db), _=Depends(get_current_user)):
    _require_ontology(db, ontology_id)
    result = test_function(db, ontology_id, body)
    return _ok(result)


@router.get("/{ontology_id}/object-types/{object_type_id}/aggregates")
def object_set_aggregates(ontology_id: str, object_type_id: str,
                          db: Session = Depends(get_db), _=Depends(get_current_user)):
    """某对象类型的集合指标 —— 跑该类型下所有 object_set 函数并返回结果。"""
    _require_ontology(db, ontology_id)
    return _ok(compute_object_set_aggregates(db, ontology_id, object_type_id))


# ============================================================
#  本体驾驶舱（Overview）— 详情页一屏看懂：模型 / 数据 / 运行 / 事实流 / 健康
# ============================================================
@router.get("/{ontology_id}/overview")
def ontology_overview(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Latest-release dashboard backed by the immutable release snapshot.

    Schema/configuration counts come from ``current_release_id`` rather than
    mutable runtime tables.  Runtime projections and telemetry are then
    constrained to identifiers/version lineage owned by that release.
    """
    from datetime import timedelta
    project = _require_ontology(db, ontology_id)
    release, snapshot = _current_release_view(db, project)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window_start = (now - timedelta(days=6)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    release_started_at = _naive_utc(release.published_at or release.created_at)
    release_window_start = max(
        [value for value in (window_start, release_started_at) if value is not None]
    )
    runtime_days = [
        {
            "date": (window_start + timedelta(days=offset)).date().isoformat(),
            "firings": {"fired": 0, "error": 0},
            "actionRuns": {"success": 0, "failed": 0},
        }
        for offset in range(7)
    ]
    runtime_by_date = {item["date"]: item for item in runtime_days}

    # —— 发布模型：不可变 current release 快照是唯一口径 ——
    object_types = snapshot["objectTypes"]
    link_types = snapshot["linkTypes"]
    actions = snapshot["actions"]
    functions = snapshot["functions"]
    snapshot_sentinels = snapshot["sentinels"]
    object_type_ids = {
        str(item.get("id")) for item in object_types if item.get("id")
    }
    link_type_ids = {
        str(item.get("id")) for item in link_types if item.get("id")
    }
    sentinel_ids = {
        str(item.get("id")) for item in snapshot_sentinels if item.get("id")
    }
    action_ids = {
        str(item.get("id")) for item in actions if item.get("id")
    }
    try:
        from app.models.sentinel import Sentinel, SentinelFiring
        live_sentinels = db.query(Sentinel).filter(
            Sentinel.ontology_id == ontology_id,
            Sentinel.id.in_(sentinel_ids),
        ).all() if sentinel_ids else []
        live_sentinel_by_id = {item.id: item for item in live_sentinels}
        firings_7d = (db.query(SentinelFiring).filter(
            SentinelFiring.ontology_id == ontology_id,
            SentinelFiring.sentinel_id.in_(sentinel_ids),
            SentinelFiring.created_at >= release_window_start,
            SentinelFiring.created_at <= now,
        ).all()) if sentinel_ids else []
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("Overview 哨兵统计失败,已降级为空统计(ontology=%s)", ontology_id, exc_info=True)
        live_sentinel_by_id, firings_7d = {}, []

    def sentinel_flag(item: dict, field: str, default: bool) -> bool:
        live = live_sentinel_by_id.get(str(item.get("id")))
        return bool(getattr(live, field)) if live is not None else bool(item.get(field, default))

    # —— 当前运行投影：只接收发布结构中仍然存在的类型 ——
    instances = (db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology_id,
        ObjectInstance.object_type_id.in_(object_type_ids),
    ).all()) if object_type_ids else []
    by_source: dict[str, int] = {}
    inst_by_type: dict[str, int] = {}
    for i in instances:
        by_source[i.source or "manual"] = by_source.get(i.source or "manual", 0) + 1
        inst_by_type[i.object_type_id] = inst_by_type.get(i.object_type_id, 0) + 1
    link_instances_n = (db.query(LinkInstance).filter(
        LinkInstance.ontology_id == ontology_id,
        LinkInstance.link_type_id.in_(link_type_ids),
    ).count()) if link_type_ids else 0

    # Mapping 定义属于发布结构；运行表中的 applied 版本戳不改变定义口径。
    mappings_stat = {"total": 0, "bound": 0, "nameMatch": 0, "autoCreate": 0, "autoApply": 0}
    ot_names = {
        str(value) for item in object_types
        for value in (item.get("name"), item.get("displayName")) if value
    }
    for mapping in snapshot["mappings"]:
        mappings_stat["total"] += 1
        if mapping.get("targetObjectTypeId"):
            mappings_stat["bound"] += 1
        elif mapping.get("entityClass") in ot_names:
            mappings_stat["nameMatch"] += 1
        else:
            mappings_stat["autoCreate"] += 1
        if (mapping.get("fieldMapping") or {}).get("__auto_apply_on_review__"):
            mappings_stat["autoApply"] += 1

    # —— 运行：动作日志显式带发布版本血缘 ——
    logs = (db.query(ActionExecutionLog).filter(
        ActionExecutionLog.ontology_id == ontology_id,
        ActionExecutionLog.ontology_version == release.version_number,
        ActionExecutionLog.action_id.in_(action_ids),
        ActionExecutionLog.dry_run == False).all()) if action_ids else []  # noqa: E712
    pending_n = sum(1 for l in logs if l.status == "pending")
    decided = sorted([l for l in logs if l.status in ("approved", "rejected")],
                     key=lambda l: (l.decided_at or l.executed_at), reverse=True)
    approved_n = sum(1 for l in decided if l.status == "approved")
    recent = decided[:20]
    recent_rate = (sum(1 for l in recent if l.status == "approved") / len(recent)) if recent else None
    runs_7d = [l for l in logs if l.executed_at
               and release_window_start <= l.executed_at <= now
               and l.status in ("success", "failed")]
    for firing in firings_7d:
        day = runtime_by_date.get(firing.created_at.date().isoformat())
        if day and firing.status in ("fired", "error"):
            day["firings"][firing.status] += 1
    for log in runs_7d:
        day = runtime_by_date.get(log.executed_at.date().isoformat())
        if day:
            day["actionRuns"][log.status] += 1

    # —— 事实流：仅统计当前发布产生/发布后追加且仍属于该结构的事实 ——
    facts_query = _release_fact_query(db, ontology_id, release, snapshot)
    facts_total = facts_query.count()
    by_kind: dict[str, int] = {}
    for kind, in facts_query.with_entities(PropertyFact.kind).all():
        by_kind[kind or "property"] = by_kind.get(kind or "property", 0) + 1

    # —— 健康检查（可操作的下一步建议）——
    health: list[dict] = []
    if not object_types:
        health.append({"level": "info", "message": "还没有对象实体",
                       "hint": "打开图谱编辑器开始建模，或在「数据映射」由数据生成类型"})
    no_pk = [
        item.get("displayName") or item.get("name") or str(item.get("id") or "")
        for item in object_types if not item.get("primaryKey")
    ]
    if no_pk:
        health.append({"level": "warn", "message": f"{len(no_pk)} 个对象实体未设主键：{', '.join(no_pk[:3])}{'…' if len(no_pk) > 3 else ''}",
                       "hint": "无主键会影响数据灌入去重与动作的实例定位"})
    if object_types and not instances:
        health.append({"level": "info", "message": "模型已就绪但还没有实例数据",
                       "hint": "到「数据映射」把 curated 数据灌进来"})
    if mappings_stat["autoCreate"] > 0:
        health.append({"level": "warn", "message": f"{mappings_stat['autoCreate']} 条映射未绑定对象实体（将由数据自建类型）",
                       "hint": "建议在映射维护里显式绑定，防止产生平行类型"})
    if snapshot_sentinels and all(
            sentinel_flag(item, "muted", False) for item in snapshot_sentinels):
        health.append({"level": "warn", "message": "所有哨兵都处于影子（静默）状态",
                       "hint": "确认规律无误后，在哨兵面板解除静默让治理真正生效"})
    if not snapshot_sentinels and instances:
        health.append({"level": "info", "message": "已有数据但还没有哨兵",
                       "hint": "在图谱编辑器建哨兵，让平台替你盯住状态变化"})
    err_firings = sum(1 for f in firings_7d if f.status == "error")
    if err_firings:
        health.append({"level": "warn", "message": f"近 7 天有 {err_firings} 次哨兵评估出错",
                       "hint": "查看运行历史的哨兵触发记录，多为条件表达式写错"})
    if pending_n > 0:
        health.append({"level": "action", "message": f"{pending_n} 个动作等待审批",
                       "hint": "到「治理与推演 → 治理驾驶舱」批准或拒绝"})

    return _ok({
        "release": {
            "id": release.id,
            "version": release.version_number,
            "publishedAt": release.published_at.isoformat() if release.published_at else None,
        },
        "model": {
            "objectTypes": len(object_types), "linkTypes": len(link_types),
            "actions": len(actions),
            "actionsRequiringApproval": sum(
                1 for item in actions if item.get("requiresApproval")),
            "functions": len(functions),
            "sentinels": {"total": len(snapshot_sentinels),
                          "enabled": sum(
                              1 for item in snapshot_sentinels
                              if sentinel_flag(item, "enabled", True)),
                          "muted": sum(
                              1 for item in snapshot_sentinels
                              if sentinel_flag(item, "muted", False))},
        },
        "data": {
            "instances": len(instances), "instancesBySource": by_source,
            "linkInstances": link_instances_n, "mappings": mappings_stat,
            "topTypes": sorted(
                [{"id": str(item.get("id") or ""),
                  "name": item.get("displayName") or item.get("name") or str(item.get("id") or ""),
                  "count": inst_by_type.get(str(item.get("id") or ""), 0)}
                 for item in object_types],
                key=lambda x: -x["count"])[:6],
        },
        "runtime": {
            "pendingApprovals": pending_n,
            "decisions": {"total": len(decided), "approved": approved_n,
                          "rejected": len(decided) - approved_n,
                          "recentApprovalRate": recent_rate},
            "firings7d": {"total": len(firings_7d),
                          "fired": sum(1 for f in firings_7d if f.status == "fired"),
                          "error": err_firings},
            "actionRuns7d": {"total": len(runs_7d),
                             "success": sum(1 for l in runs_7d if l.status == "success"),
                             "failed": sum(1 for l in runs_7d if l.status == "failed")},
            "daily7d": runtime_days,
        },
        "facts": {"total": facts_total, "byKind": by_kind},
        "health": health,
    })


@router.get("/{ontology_id}/facts/recent")
def recent_facts(ontology_id: str, limit: int = 30, kind: Optional[str] = None,
                 release_id: Optional[str] = None,
                 current_release_only: bool = False,
                 db: Session = Depends(get_db), _=Depends(get_current_user)):
    """本体级事实流（跨实例，时间倒序），带可读主体标签——治理页的时间线。"""
    project = _require_ontology(db, ontology_id)
    release_snapshot = None
    q = db.query(PropertyFact).filter(PropertyFact.ontology_id == ontology_id)
    if release_id:
        release = current_release_context(
            db, ontology_id, expected_release_id=release_id)
        q = q.filter(PropertyFact.ontology_version == release.version)
        release_snapshot = release.snapshot
    elif current_release_only:
        release_row, release_snapshot = _current_release_view(db, project)
        q = _release_fact_query(db, ontology_id, release_row, release_snapshot)
    if kind:
        q = q.filter(PropertyFact.kind == kind)
    items = q.order_by(*fact_order_clause()).limit(min(limit, 200)).all()

    # 主体标签解析：实例 → name/主键值；哨兵(absence) → 哨兵名；决策 → 动作名
    inst_ids = {f.instance_id for f in items}
    inst_map = {i.id: i for i in db.query(ObjectInstance).filter(
        ObjectInstance.id.in_(inst_ids)).all()} if inst_ids else {}
    if release_snapshot is not None:
        sent_map = {
            str(item["id"]): SimpleNamespace(
                display_name=item.get("displayName"), name=item.get("name"))
            for item in release_snapshot["sentinels"] if item.get("id")
        }
    else:
        try:
            from app.models.sentinel import Sentinel
            sent_map = {s.id: s for s in db.query(Sentinel).filter(
                Sentinel.id.in_(inst_ids)).all()} if inst_ids else {}
        except Exception:  # noqa: BLE001
            sent_map = {}
    log_map = {l.id: l for l in db.query(ActionExecutionLog).filter(
        ActionExecutionLog.id.in_(inst_ids)).all()} if inst_ids else {}
    if release_snapshot is not None:
        type_names = {
            str(item["id"]): item.get("displayName") or item.get("name") or ""
            for item in release_snapshot["objectTypes"] if item.get("id")
        }
        action_names = {
            str(item["id"]): item.get("displayName") or item.get("name") or ""
            for item in release_snapshot["actions"] if item.get("id")
        }
    else:
        type_names = {o.id: (o.display_name or o.name) for o in
                      db.query(ObjectType).filter(ObjectType.ontology_id == ontology_id).all()}
        action_names = {}

    def _label(f: PropertyFact) -> str:
        inst = inst_map.get(f.instance_id)
        if inst is not None:
            props = inst.properties or {}
            nm = props.get("name") or props.get("flight_no") or props.get("id")
            tn = type_names.get(inst.object_type_id, "")
            return f"{tn}·{nm}" if nm else (tn or f.instance_id[:8])
        s = sent_map.get(f.instance_id)
        if s is not None:
            return f"哨兵·{s.display_name or s.name}"
        l = log_map.get(f.instance_id)
        if l is not None:
            return f"动作·{action_names.get(l.action_id) or l.action_name or l.action_id[:8]}"
        return f.instance_id[:8]

    out = []
    for f in items:
        d = _fact_to_dict(f)
        d["subjectLabel"] = _label(f)
        out.append(d)
    return _ok(out)


# ============================================================
#  自治等级（Autonomy）— 哨兵×动作的人工批准率统计与晋升建议
# ============================================================
AUTONOMY_PROMOTE_MIN = 10      # 晋升所需最少近期决策数
AUTONOMY_PROMOTE_RATE = 0.95   # 晋升所需近期批准率
AUTONOMY_DEMOTE_FAILRATE = 0.2  # 自动执行失败率超过则建议降级


@router.get("/{ontology_id}/autonomy")
def autonomy_stats(ontology_id: str, release_id: Optional[str] = None,
                   db: Session = Depends(get_db), _=Depends(get_current_user)):
    """按动作统计 HITL 决策历史，给出自治等级与晋升/降级建议。

    等级模型（自治是挣来的，不是配出来的）：
      L0 影子   绑定的哨兵全部静默——只观察记录，不产生副作用
      L1 人审   requires_approval=True，每次真实执行等人拍板
      L2 自动   requires_approval=False，命中即执行
    晋升依据 = 近期人工批准率（人几乎总是点"批准"，说明规则已可信）。
    """
    _require_ontology(db, ontology_id)
    release = (
        current_release_context(
            db, ontology_id, expected_release_id=release_id)
        if release_id else None
    )
    logs_query = db.query(ActionExecutionLog).filter(
        ActionExecutionLog.ontology_id == ontology_id,
        ActionExecutionLog.dry_run == False,  # noqa: E712
    )
    if release is not None:
        actions = [SimpleNamespace(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or ""),
            display_name=item.get("displayName") or item.get("display_name"),
            requires_approval=bool(
                item.get("requiresApproval", item.get("requires_approval", False))),
        ) for item in release.snapshot["actions"] if item.get("id")]
        logs_query = logs_query.filter(
            ActionExecutionLog.ontology_version == release.version)
        try:
            sentinels = []
            for item in release.snapshot["sentinels"]:
                sentinel_id = str(item.get("id") or "")
                if not sentinel_id:
                    continue
                sentinels.append(SimpleNamespace(
                    id=sentinel_id,
                    name=str(item.get("name") or ""),
                    display_name=item.get("displayName") or item.get("name") or "",
                    action_ids=item.get("actionIds") or item.get("action_ids") or [],
                    muted=bool(item.get("muted", False)),
                    enabled=bool(item.get("enabled", True)),
                ))
        except Exception:  # noqa: BLE001
            sentinels = []
    else:
        actions = db.query(ActionType).filter(
            ActionType.ontology_id == ontology_id).all()
        try:
            from app.models.sentinel import Sentinel
            sentinels = db.query(Sentinel).filter(
                Sentinel.ontology_id == ontology_id).all()
        except Exception:  # noqa: BLE001
            sentinels = []
    logs = logs_query.all()

    by_action: dict[str, list] = {}
    for l in logs:
        by_action.setdefault(l.action_id, []).append(l)

    out = []
    for a in actions:
        ls = by_action.get(a.id, [])
        decided = [l for l in ls if l.status in ("approved", "rejected")]
        decided.sort(key=lambda l: (l.decided_at or l.executed_at), reverse=True)
        approved_n = sum(1 for l in decided if l.status == "approved")
        rejected_n = len(decided) - approved_n
        recent = decided[:20]
        recent_approved = sum(1 for l in recent if l.status == "approved")
        recent_rate = (recent_approved / len(recent)) if recent else None

        auto = [l for l in ls if l.decided_by is None and l.status in ("success", "failed")]
        auto_failed = sum(1 for l in auto if l.status == "failed")
        pending_n = sum(1 for l in ls if l.status == "pending")

        bound = [s for s in sentinels if a.id in (s.action_ids or [])]
        shadow = bool(bound) and all(bool(s.muted) for s in bound)

        if shadow:
            level = "L0"
        elif a.requires_approval:
            level = "L1"
        else:
            level = "L2"

        recommendation = None
        reason = None
        if level == "L1" and recent_rate is not None and len(recent) >= AUTONOMY_PROMOTE_MIN \
                and recent_rate >= AUTONOMY_PROMOTE_RATE:
            recommendation = "promote"
            reason = (f"近 {len(recent)} 次决策批准率 {recent_rate:.0%} ≥ "
                      f"{AUTONOMY_PROMOTE_RATE:.0%}，人几乎总在点批准——可晋升为自动执行")
        elif level == "L1" and recent_rate is not None and len(recent) >= 5 and recent_rate < 0.5:
            recommendation = "observe"
            reason = f"近 {len(recent)} 次决策批准率仅 {recent_rate:.0%}，建议回哨兵面板静默观察、修条件后再放行"
        elif level == "L2" and len(auto) >= AUTONOMY_PROMOTE_MIN \
                and (auto_failed / len(auto)) > AUTONOMY_DEMOTE_FAILRATE:
            recommendation = "demote"
            reason = (f"自动执行 {len(auto)} 次中失败 {auto_failed} 次"
                      f"（{auto_failed / len(auto):.0%}），建议加回人工审批闸门")

        out.append({
            "actionId": a.id,
            "actionName": a.display_name or a.name,
            "requiresApproval": bool(a.requires_approval),
            "level": level,
            "shadow": shadow,
            "sentinels": [{"id": s.id, "name": s.display_name or s.name,
                           "muted": bool(s.muted), "enabled": bool(s.enabled)} for s in bound],
            "decisions": {
                "approved": approved_n, "rejected": rejected_n, "total": len(decided),
                "approvalRate": (approved_n / len(decided)) if decided else None,
                "recentCount": len(recent), "recentApprovalRate": recent_rate,
            },
            "autoRuns": {"total": len(auto), "failed": auto_failed},
            "pending": pending_n,
            "recommendation": recommendation,
            "recommendationReason": reason,
            "thresholds": {"promoteMinDecisions": AUTONOMY_PROMOTE_MIN,
                           "promoteRate": AUTONOMY_PROMOTE_RATE},
        })
    return _ok(out)


# ============================================================
#  Execution Logs
# ============================================================
@router.get("/{ontology_id}/logs")
def list_logs(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    _require_ontology(db, ontology_id)
    items = (db.query(ActionExecutionLog)
             .filter(ActionExecutionLog.ontology_id == ontology_id)
             .order_by(ActionExecutionLog.executed_at.desc()).limit(200).all())
    return _ok([S.ActionLogOut.model_validate(x).model_dump(by_alias=True) for x in items])
