"""
正规本体投影层 (Formal Ontology Projection)
==========================================

把数据流水线已落地的旧扁平模型 (Entity / Relation) 投影成
Palantir Foundry 风格的正规本体 (ObjectType / ObjectInstance /
LinkType / LinkInstance)，让流水线与图谱编辑器共用同一套数据。

设计原则
--------
1. **非侵入**：不重写 mapping_service 复杂的映射/推断逻辑，只在
   ``build_all()`` 末尾追加一次投影 (Phase 5)。
2. **幂等**：用稳定 key 做 upsert——
   - ObjectType   按 (ontology_id, name=entity_class) 去重
   - ObjectInstance 按 (ontology_id, external_id=Entity.id) 去重
   - LinkType     按 (ontology_id, name, source/target object_type) 去重
   - LinkInstance 按确定性 id (link_type + src_instance + tgt_instance) 去重
3. **可追溯**：投影出来的实例 ``source="pipeline"``，
   ``external_id`` 指回原 Entity.id，便于回滚 / 重跑。

调用方式
--------
    from app.services.v2.mapping.formal_projection import project_to_formal_ontology
    summary = project_to_formal_ontology(db, ontology_id)
"""
from __future__ import annotations

import logging
import re
import uuid as _uuid
from typing import Any

logger = logging.getLogger(__name__)

# Entity.properties 中属于"系统/溯源"的键，不作为业务属性投影
_RESERVED_PROP_KEYS = {
    "id", "ontology_id", "source_id", "object_type",
    "source_row_count", "name", "name_cn", "name_en",
    "name_abbr", "display_name",
    "__mapping_ids__", "__business_properties__",
}

# entity_class → 一组默认图标 / 颜色（仅美观，无业务含义）
_DEFAULT_COLORS = ["indigo", "cyan", "violet", "purple", "yellow"]


def _stable_id(*parts: Any) -> str:
    """根据语义键生成确定性 UUID，保证多次投影幂等。"""
    raw = ":".join(str(p) for p in parts)
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, raw))


# 关系投影的内部记账键：仅供映射/去重使用，不应作为业务边属性落进 LinkInstance
_INTERNAL_LINK_PROP_KEYS = frozenset({
    "mapping_type", "src_key", "tgt_key", "cardinality",
    "__edge_key__", "__link_mapping_id__", "fk_column", "alt_column", "source",
})


def _infer_property_type(values: list[Any]) -> str:
    """根据样本值粗略推断 PropertyType（与前端 PropertyType 对齐）。"""
    non_null = [v for v in values if v not in (None, "")]
    if not non_null:
        return "string"
    sample = non_null[: min(len(non_null), 20)]

    def _is_bool(v: Any) -> bool:
        return isinstance(v, bool) or str(v).strip().lower() in ("true", "false", "是", "否")

    def _is_num(v: Any) -> bool:
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            return True
        try:
            float(str(v).replace(",", ""))
            return True
        except (ValueError, TypeError):
            return False

    def _is_datetime(v: Any) -> bool:
        s = str(v)
        return bool(re.match(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2})?", s))

    if all(isinstance(v, (list, tuple)) for v in sample):
        return "array"
    if all(isinstance(v, dict) for v in sample):
        return "object"
    if all(_is_bool(v) for v in sample):
        return "boolean"
    if all(_is_num(v) for v in sample):
        return "number"
    if all(_is_datetime(v) for v in sample):
        return "datetime"
    return "string"


def _pick_first(data: dict, *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _property_data_binding(
    prop_name: str, meta: dict, binding_context: dict | None,
) -> dict:
    source_column = _pick_first(
        meta,
        "sourceColumn", "source_column",
        "column", "column_name",
        "originalColumn", "original_column",
    )
    original_column = _pick_first(meta, "originalColumn", "original_column", "column", "column_name")
    inferred = not bool(source_column)

    binding = {
        "sourceColumn": str(source_column or prop_name),
        "inferred": inferred,
    }
    if original_column:
        binding["originalColumn"] = str(original_column)
    if binding_context:
        if binding_context.get("mapping_id"):
            binding["mappingId"] = binding_context["mapping_id"]
        if binding_context.get("curated_dataset_id"):
            binding["curatedDatasetId"] = binding_context["curated_dataset_id"]
        if binding_context.get("source_dataset_version_id"):
            binding["datasetVersionId"] = binding_context["source_dataset_version_id"]
        if binding_context.get("dataset_name"):
            binding["datasetName"] = binding_context["dataset_name"]
    return binding


def _coerce_props_to_type(props: dict, type_props: list[dict]) -> dict:
    """按类型的属性定义做值转换（CSV 来的全是字符串——number 属性不转数字，
    哨兵的数值条件、派生函数、动作校验会整体失灵）。声明过的类型转换失败
    必须阻止投影，不能把错误字符串悄悄塞进已发布本体。"""
    kind_by_name = {p.get("name"): (p.get("type") or "string")
                    for p in (type_props or []) if isinstance(p, dict)}
    out = dict(props)
    for k, v in props.items():
        t = kind_by_name.get(k)
        if v is None or not isinstance(v, str):
            continue
        s = v.strip()
        try:
            if t in ("number", "integer", "float"):
                if s == "":
                    continue
                f = float(s.replace(",", ""))
                out[k] = int(f) if f.is_integer() else f
            elif t == "boolean":
                low = s.lower()
                if low in ("true", "1", "yes", "是"):
                    out[k] = True
                elif low in ("false", "0", "no", "否"):
                    out[k] = False
                elif s:
                    raise ValueError(f"属性 {k} 的值 {v!r} 无法转换为 boolean")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"属性 {k} 的值 {v!r} 无法转换为 {t}") from exc
    return out


def _merge_properties(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """属性合并：已有定义（按 name 匹配）原样保留，只追加数据里新出现的属性。

    这是"人工绑定和维护"的护栏——用户在类型上手工加的 computed 属性、
    改过的类型/显示名/校验配置，不能被重跑投影的数据推断结果覆盖。
    """
    existing = [p for p in (existing or []) if isinstance(p, dict)]
    seen = {p.get("name") for p in existing if p.get("name")}
    out = list(existing)
    for p in (incoming or []):
        if isinstance(p, dict) and p.get("name") and p["name"] not in seen:
            out.append(p)
            seen.add(p["name"])
    return out


def _build_object_type_properties(
    entities: list[dict], pk_field: str | list[str], property_mappings: list[dict] | None,
    binding_context: dict | None = None,
) -> tuple[list[dict], str]:
    """
    从一批 Entity props 推导 ObjectType.properties (Property[])。

    返回 (properties, primary_key_property_id)。
    """
    # 收集所有出现过的业务属性键 + 样本值
    samples: dict[str, list[Any]] = {}
    for ent in entities:
        for k, v in ent.items():
            if k in _RESERVED_PROP_KEYS:
                continue
            samples.setdefault(k, []).append(v)

    # property_mappings 提供更准确的 displayName / type 提示
    meta_by_name: dict[str, dict] = {}
    for pm in (property_mappings or []):
        # property_mappings 元素形如 {"column": ..., "property": ..., "type": ..., "display_name": ...}
        prop_name = pm.get("property") or pm.get("name") or pm.get("column")
        if prop_name:
            meta_by_name[prop_name] = pm

    pk_fields = ([str(item) for item in pk_field if str(item)]
                 if isinstance(pk_field, list)
                 else [part.strip() for part in str(pk_field or "").split(",")
                       if part.strip()])
    pk_rank = {name: index + 1 for index, name in enumerate(pk_fields)}
    synthetic_composite = (
        "__composite_identity__" if len(pk_fields) > 1
        and "__composite_identity__" in samples else None)
    storage_pk_fields = [synthetic_composite] if synthetic_composite else pk_fields
    properties: list[dict] = []
    pk_prop_id = ""
    # 确保复合主键各分量按契约顺序优先出现
    ordered_keys = sorted(
        samples.keys(),
        key=lambda k: (
            (0, 0) if k == synthetic_composite
            else (1, pk_rank[k]) if k in pk_rank
            else (2, str(k))))
    for key in ordered_keys:
        pid = f"prop_{re.sub(r'[^A-Za-z0-9_]', '_', str(key))}"
        meta = meta_by_name.get(key, {})
        ptype = meta.get("type") or _infer_property_type(samples[key])
        if ptype not in ("string", "number", "boolean", "date", "datetime", "array", "object", "reference"):
            ptype = "string"
        prop = {
            "id": pid,
            "name": str(key),
            "displayName": meta.get("display_name") or meta.get("displayName") or str(key),
            "type": ptype,
            "required": bool(key in pk_rank or key in storage_pk_fields),
            "source": "stored",
            "dataBinding": _property_data_binding(str(key), meta, binding_context),
        }
        if key in pk_rank:
            prop["primaryKeyPart"] = pk_rank[key]
        if key == synthetic_composite:
            prop["technical"] = True
            prop["identityComponents"] = pk_fields
        properties.append(prop)
        if key in storage_pk_fields and not pk_prop_id:
            pk_prop_id = pid

    found_components = {p["name"] for p in properties if p.get("primaryKeyPart")}
    if pk_fields and found_components != set(pk_fields):
        missing = [field for field in pk_fields if field not in found_components]
        raise ValueError(f"正规本体投影缺少主键属性：{missing}")

    # 仅在没有上游身份契约时提供旧数据兼容推断；有契约却找不到列必须失败，
    # 不能悄悄把第一列变成另一套实例身份。
    if not pk_fields and not pk_prop_id and properties:
        _id_keywords = ("id", "code", "key", "编号", "编码", "no", "number")
        _name_keywords = ("name", "title", "名称", "标题")
        def _rank(p: dict) -> int:
            n = str(p.get("name", "")).lower()
            if any(k in n for k in _id_keywords):
                return 0
            if any(k in n for k in _name_keywords):
                return 1
            return 2
        best = min(properties, key=_rank)
        pk_prop_id = best["id"]
        best["required"] = True
    return properties, pk_prop_id


def project_to_formal_ontology(
    db,
    ontology_id: str,
    mapping_meta: dict | None = None,
) -> dict:
    """
    把已落地的 Entity / Relation 投影为正规本体。

    参数
    ----
    db            : SQLAlchemy session
    ontology_id   : 本体 id
    mapping_meta  : build_all() 产出的 per-mapping 元数据（可选，用于
                    取 pk_col / property_mappings 提升属性质量）；
                    不传时仅依据 Entity.properties 推断。

    返回投影统计 dict。
    """
    from app.models.entity import Entity
    from app.models.relation import Relation
    from app.models.ontology_formal import (
        ObjectType, ObjectInstance, LinkType, LinkInstance,
    )
    from app.models.ontology import OntologyProject

    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if project is None:
        raise ValueError(f"Ontology {ontology_id} not found")
    schema_locked = (project.status or "") == "published"

    summary = {
        "object_types": 0,
        "object_instances": 0,
        "link_types": 0,
        "link_instances": 0,
        "skipped_relations": 0,
        "removed_link_instances": 0,
    }

    entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    if not entities:
        logger.info("投影跳过：本体 %s 无 Entity 数据", ontology_id)
        return summary

    # ── 元数据辅助：entity_class → {pk_col, property_mappings} ──
    class_meta: dict[str, dict] = {}
    for meta in (mapping_meta or {}).values():
        ec = meta.get("entity_class")
        if ec and ec not in class_meta:
            class_meta[ec] = {
                "pk_col": meta.get("pk_col"),
                "property_mappings": meta.get("property_mappings") or [],
                "target_object_type_id": meta.get("target_object_type_id"),
                "binding_context": {
                    "mapping_id": meta.get("mapping_id"),
                    "curated_dataset_id": meta.get("curated_dataset_id"),
                    "source_dataset_version_id": meta.get("source_dataset_version_id"),
                    "dataset_name": meta.get("dataset_name"),
                },
            }

    # 绑定兜底：投影会重投影本体下全部 entity_class（不只本次映射），
    # 其余类的绑定信息不在本次 meta 里——从映射表按 entity_class 补齐，
    # 否则单映射增量灌入时其他类会丢绑定、退化成自建平行类型
    try:
        from app.models.v2.mapping import OntologyMapping as _OM
        for _m in db.query(_OM).filter(_OM.ontology_id == ontology_id).all():
            if _m.target_object_type_id:
                cm = class_meta.setdefault(_m.entity_class, {})
                if not cm.get("target_object_type_id"):
                    cm["target_object_type_id"] = _m.target_object_type_id
    except Exception:  # noqa: BLE001 — 兜底失败不阻断投影（仍有按名匹配保护）
        logger.warning("读取映射绑定失败，仅按名匹配", exc_info=True)

    # ── 1. 按 entity_class 分组 ──
    entities_by_class: dict[str, list[Entity]] = {}
    for ent in entities:
        ec = ent.type or "Object"
        entities_by_class.setdefault(ec, []).append(ent)

    # ── 2. 投影 ObjectType（upsert by name）+ ObjectInstance（upsert by external_id）──
    # entity_class → ObjectType.id
    class_to_ot_id: dict[str, str] = {}
    # entity_class → 主键属性名 (用于 LinkInstance 的属性记录，非必须)
    class_to_pk_field: dict[str, str] = {}
    # Entity.id → ObjectInstance.id
    entity_to_instance: dict[str, str] = {}

    for idx, (ec, ent_list) in enumerate(entities_by_class.items()):
        ent_props_list = []
        for entity in ent_list:
            flattened = dict(entity.properties or {})
            business = flattened.pop("__business_properties__", {})
            if isinstance(business, dict):
                flattened.update(business)
            ent_props_list.append(flattened)
        meta = class_meta.get(ec, {})
        # property_mappings 里的 property 名是落地后的属性键
        pk_source_fields = [part.strip() for part in str(
            meta.get("pk_col") or "").split(",") if part.strip()]
        source_to_property = {
            str(item.get("column")): str(item.get("property") or item.get("column"))
            for item in (meta.get("property_mappings") or [])
            if isinstance(item, dict) and item.get("column")
        }
        # pk_col 是源列名，Entity 中使用的是 field mapping 后的属性名。
        pk_fields = [source_to_property.get(field, field) for field in pk_source_fields]
        data_props, pk_prop_id = _build_object_type_properties(
            ent_props_list, pk_fields, meta.get("property_mappings"),
            meta.get("binding_context"),
        )
        pk_name_from_data = next((p["name"] for p in data_props if p["id"] == pk_prop_id), None)

        # —— 类型解析三级顺序（model-first 的关键）——
        # ① 映射上人工绑定的对象实体（target_object_type_id）
        # ② 图谱里同名的手绘类型（避免平行重复类型 + 编辑器 duplicate_name 422）
        # ③ 投影自建（data-first：uuid5 稳定 id，幂等 upsert）
        existing_ot = None
        bound_id = meta.get("target_object_type_id")
        if bound_id:
            existing_ot = db.query(ObjectType).filter(
                ObjectType.id == bound_id,
                ObjectType.ontology_id == ontology_id).first()
            if existing_ot is None:
                logger.warning("映射绑定的对象实体 %s 已不存在，回退按名匹配 (entity_class=%s)",
                               bound_id, ec)
        if existing_ot is None:
            from sqlalchemy import or_
            existing_ot = db.query(ObjectType).filter(
                ObjectType.ontology_id == ontology_id,
                or_(ObjectType.name == ec, ObjectType.display_name == ec)).first()
        if existing_ot is None:
            proj_id = _stable_id("ot", ontology_id, ec)
            existing_ot = db.query(ObjectType).filter(ObjectType.id == proj_id).first()

        color = _DEFAULT_COLORS[idx % len(_DEFAULT_COLORS)]
        if existing_ot:
            # 属性合并而非替换：手绘/已有定义（computed 属性、类型修正、显示名）
            # 永远优先，只追加数据里新出现的列——重跑投影不再冲掉人的加工
            if schema_locked:
                declared = {p.get("name") for p in (existing_ot.properties or [])
                            if isinstance(p, dict) and p.get("name")}
                incoming = {p.get("name") for p in data_props
                            if isinstance(p, dict) and p.get("name")}
                unknown = sorted(incoming - declared)
                if unknown:
                    raise ValueError(
                        f"已发布对象类型「{existing_ot.display_name}」出现未声明字段 {unknown}；"
                        "请先撤回版本、维护 schema 并重新发布"
                    )
                merged = list(existing_ot.properties or [])
            else:
                merged = _merge_properties(existing_ot.properties or [], data_props)
            merged = [dict(prop) for prop in merged]
            # Existing hand-authored property metadata remains authoritative,
            # except that every canonical composite-key component must be
            # non-null and visibly marked in the final type contract.
            pk_rank = {name: index + 1 for index, name in enumerate(pk_fields)}
            for prop in merged:
                if prop.get("name") in pk_rank:
                    prop["required"] = True
                    prop["primaryKeyPart"] = pk_rank[prop["name"]]
            if merged != (existing_ot.properties or []):
                existing_ot.properties = merged
            existing_ot.display_name = existing_ot.display_name or ec
            if not existing_ot.primary_key and pk_name_from_data:
                pk_prop = next((p for p in merged if p.get("name") == pk_name_from_data), None)
                if pk_prop:
                    existing_ot.primary_key = pk_prop.get("id")
            ot_id = existing_ot.id
            final_props = merged
            final_pk_id = existing_ot.primary_key
        else:
            if schema_locked:
                raise ValueError(
                    f"已发布本体不允许由数据投影自动创建对象类型「{ec}」；"
                    "请先在草稿中建立并绑定类型"
                )
            ot_id = _stable_id("ot", ontology_id, ec)
            db.add(ObjectType(
                id=ot_id,
                ontology_id=ontology_id,
                name=ec,
                display_name=ec,
                description=f"由数据流水线投影生成（来源 entity_class={ec}）",
                icon="cube",
                color=color,
                primary_key=pk_prop_id,
                properties=data_props,
                position_x=float((idx % 4) * 320),
                position_y=float((idx // 4) * 240),
            ))
            summary["object_types"] += 1
            final_props = data_props
            final_pk_id = pk_prop_id
        class_to_ot_id[ec] = ot_id
        # 找出主键属性的 name（以最终类型定义为准）
        pk_name = next((p["name"] for p in final_props if p.get("id") == final_pk_id), None)
        class_to_pk_field[ec] = pk_name or pk_name_from_data or ""

        # ObjectInstance：每个 Entity → 一条实例，external_id=Entity.id 去重。
        # 变化才写：无变化实例不进 session.dirty（否则重跑投影会让 CDC 对全量
        # 实例误报变化）；变化的写入事实流（source=pipeline）并触发派生重算。
        from app.ontologies.formal_modeling.facts import record_property_facts
        from app.ontologies.formal_modeling.derived import recompute_instance_derived
        for ent in ent_list:
            inst_id = _stable_id("oi", ontology_id, ent.id)
            props = {k: v for k, v in (ent.properties or {}).items()
                     if k not in ("ontology_id", "__mapping_ids__", "__business_properties__")}
            business = (ent.properties or {}).get("__business_properties__")
            if isinstance(business, dict):
                props.update(business)
            # 按类型属性定义转换值类型（CSV 字符串 → number/boolean）
            props = _coerce_props_to_type(props, final_props)
            # 补充展示名，便于图谱卡片渲染
            props.setdefault("name", ent.name_cn or ent.name_en or ent.id)
            existing_inst = db.query(ObjectInstance).filter(
                ObjectInstance.id == inst_id,
            ).first()
            if existing_inst:
                old_props = dict(existing_inst.properties or {})
                changed = (old_props != props
                           or existing_inst.object_type_id != ot_id
                           or existing_inst.external_id != ent.id)
                if changed:
                    existing_inst.object_type_id = ot_id
                    existing_inst.properties = props
                    existing_inst.source = "pipeline"
                    existing_inst.external_id = ent.id
                    new_facts = record_property_facts(
                        db, ontology_id=ontology_id, instance_id=inst_id,
                        object_type_id=ot_id, old_props=old_props, new_props=props,
                        source="pipeline")
                    if new_facts:
                        recompute_instance_derived(
                            db, ontology_id=ontology_id, instance=existing_inst,
                            trigger_facts=new_facts)
                    summary["updated_instances"] = summary.get("updated_instances", 0) + 1
                inst_obj = existing_inst
            else:
                inst_obj = ObjectInstance(
                    id=inst_id,
                    ontology_id=ontology_id,
                    object_type_id=ot_id,
                    properties=props,
                    computed={},
                    source="pipeline",
                    external_id=ent.id,
                )
                db.add(inst_obj)
                db.flush()
                new_facts = record_property_facts(
                    db, ontology_id=ontology_id, instance_id=inst_id,
                    object_type_id=ot_id, old_props=None, new_props=props,
                    source="pipeline")
                if new_facts:
                    recompute_instance_derived(
                        db, ontology_id=ontology_id, instance=inst_obj,
                        trigger_facts=new_facts)
                summary["object_instances"] += 1
            entity_to_instance[ent.id] = inst_id

    # Keep object and link projection in one caller-controlled transaction.
    db.flush()

    # ── 3. 投影 LinkType + LinkInstance ──
    relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
    if not relations:
        logger.info("投影：本体 %s 无 Relation，跳过链接投影", ontology_id)
        from app.ontologies.formal_modeling.validation import validate_instance_contract
        from app.ontologies.formal_modeling.facts import record_link_fact
        for link in db.query(LinkInstance).filter(
                LinkInstance.ontology_id == ontology_id).all():
            if link.source_relation_id:
                record_link_fact(
                    db, ontology_id=ontology_id, link_instance_id=link.id,
                    link_type_id=link.link_type_id, exists=False,
                    source="pipeline-reconcile")
                db.delete(link)
                summary["removed_link_instances"] += 1
        all_object_types = db.query(ObjectType).filter(ObjectType.ontology_id == ontology_id).all()
        all_instances = db.query(ObjectInstance).filter(ObjectInstance.ontology_id == ontology_id).all()
        contract_errors = validate_instance_contract(all_object_types, all_instances)
        if contract_errors:
            preview = "; ".join(error.get("message", "") for error in contract_errors[:5])
            raise ValueError(
                f"正规本体投影违反运行契约（{len(contract_errors)} 项）: {preview}")
        # 即使无关系，实例投影也已发生 → 同样要推进修订戳
        from datetime import datetime as _dt, timezone as _tz
        from app.models.ontology import OntologyProject as _OP
        proj = db.query(_OP).filter(_OP.id == ontology_id).first()
        if proj is not None:
            proj.updated_at = _dt.now(_tz.utc)
        db.flush()
        return summary

    # entity_class 查询缓存：Entity.id → entity_class
    entity_class_of: dict[str, str] = {e.id: (e.type or "Object") for e in entities}

    # (src_class, tgt_class, rel_type) → LinkType.id
    linktype_cache: dict[tuple[str, str, str], str] = {}

    # ── 基数兜底推断 ──
    # 上游 FK 推断理应把 cardinality 写进 Relation.properties，但若缺失
    # （历史数据 / 回写失败），这里按实际边的重数自行推断，避免一律退化为
    # many-to-many。统计每个 link 分组下：每个 src 连了几个不同 tgt，反之亦然。
    def _infer_cardinality_for_group(rel_group: list) -> str:
        # source_object_type --link--> target_object_type
        # src_to_tgts: 一个 source 实例连了几个不同 target
        # tgt_to_srcs: 一个 target 实例被几个不同 source 连
        src_to_tgts: dict[str, set] = {}
        tgt_to_srcs: dict[str, set] = {}
        for r in rel_group:
            src_to_tgts.setdefault(r.source_entity, set()).add(r.target_entity)
            tgt_to_srcs.setdefault(r.target_entity, set()).add(r.source_entity)
        # source 侧是否"多"：某个 target 被多个 source 指
        source_is_many = max((len(v) for v in tgt_to_srcs.values()), default=1) > 1
        # target 侧是否"多"：某个 source 指向多个 target
        target_is_many = max((len(v) for v in src_to_tgts.values()), default=1) > 1
        if source_is_many and target_is_many:
            return "many-to-many"
        if source_is_many and not target_is_many:
            return "many-to-one"
        if target_is_many and not source_is_many:
            return "one-to-many"
        return "one-to-one"

    # 预分组用于兜底推断
    _rels_by_key: dict[tuple[str, str, str], list] = {}
    for _r in relations:
        _sc = entity_class_of.get(_r.source_entity, "Object")
        _tc = entity_class_of.get(_r.target_entity, "Object")
        _rt = _r.type or "RELATED_TO"
        _rels_by_key.setdefault((_sc, _tc, _rt), []).append(_r)

    for rel in relations:
        src_ent = rel.source_entity
        tgt_ent = rel.target_entity
        src_inst = entity_to_instance.get(src_ent)
        tgt_inst = entity_to_instance.get(tgt_ent)
        if not src_inst or not tgt_inst:
            summary["skipped_relations"] += 1
            continue

        src_class = entity_class_of.get(src_ent, "Object")
        tgt_class = entity_class_of.get(tgt_ent, "Object")
        rel_type = rel.type or "RELATED_TO"
        # 优先取 Relation.properties 里上游 FK 推断写入的基数；缺失时按实际边重数兜底推断，
        # 避免一律退化成 many-to-many。
        raw_card = (rel.properties or {}).get("cardinality")
        if raw_card:
            cardinality = str(raw_card).replace("_", "-")
        else:
            cardinality = _infer_cardinality_for_group(
                _rels_by_key.get((src_class, tgt_class, rel_type), [rel])
            )
        if cardinality not in ("one-to-one", "one-to-many", "many-to-one", "many-to-many"):
            cardinality = "many-to-many"

        key = (src_class, tgt_class, rel_type)
        lt_id = linktype_cache.get(key)
        if lt_id is None:
            src_ot = class_to_ot_id.get(src_class)
            tgt_ot = class_to_ot_id.get(tgt_class)
            if not src_ot or not tgt_ot:
                summary["skipped_relations"] += 1
                continue
            # 关系类型同样先复用：图谱里已有"两端类型一致、同名"的手绘关系时直接用它
            # （手绘关系的基数/角色是人定的，不被数据推断覆盖）
            hand_lt = db.query(LinkType).filter(
                LinkType.ontology_id == ontology_id,
                LinkType.name == rel_type,
                LinkType.source_object_type_id == src_ot,
                LinkType.target_object_type_id == tgt_ot).first()
            if hand_lt is not None:
                lt_id = hand_lt.id
            else:
                lt_id = _stable_id("lt", ontology_id, src_class, tgt_class, rel_type)
                existing_lt = db.query(LinkType).filter(LinkType.id == lt_id).first()
                if existing_lt:
                    # 投影自建的关系：随数据更新基数/端点
                    if schema_locked:
                        if (existing_lt.source_object_type_id != src_ot
                                or existing_lt.target_object_type_id != tgt_ot):
                            raise ValueError(f"已发布关系类型「{rel_type}」端点与投影数据不一致")
                    else:
                        existing_lt.cardinality = cardinality
                        existing_lt.source_object_type_id = src_ot
                        existing_lt.target_object_type_id = tgt_ot
                else:
                    if schema_locked:
                        raise ValueError(
                            f"已发布本体不允许由数据投影自动创建关系类型「{rel_type}」；"
                            "请先在草稿中建立关系定义"
                        )
                    db.add(LinkType(
                        id=lt_id,
                        ontology_id=ontology_id,
                        name=rel_type,
                        display_name=rel_type.replace("_", " ").title(),
                        description=f"由数据流水线投影生成（{src_class} → {tgt_class}）",
                        source_object_type_id=src_ot,
                        target_object_type_id=tgt_ot,
                        cardinality=cardinality,
                        source_role=src_class,
                        target_role=tgt_class,
                        properties=[],
                    ))
                    summary["link_types"] += 1
            linktype_cache[key] = lt_id

        # LinkInstance：确定性 id 去重；新建链接进入事实流（Link 也是 Fact）
        # 连接表胖关系：同一对实体可有多条属性不同的边 → id 纳入 __edge_key__，防被去重合并成一条。
        raw_props = rel.properties or {}
        edge_key = raw_props.get("__edge_key__") or ""
        li_id = _stable_id("li", ontology_id, lt_id, src_inst, tgt_inst, edge_key)
        existing_li = db.query(LinkInstance).filter(LinkInstance.id == li_id).first()
        # 只保留真正的业务边属性，剔除映射记账用的内部键
        li_props = {k: v for k, v in raw_props.items() if k not in _INTERNAL_LINK_PROP_KEYS}
        if existing_li:
            if (existing_li.link_type_id != lt_id
                    or existing_li.source_object_id != src_inst
                    or existing_li.target_object_id != tgt_inst
                    or (existing_li.properties or {}) != li_props
                    or existing_li.source_relation_id != rel.id):
                existing_li.link_type_id = lt_id
                existing_li.source_object_id = src_inst
                existing_li.target_object_id = tgt_inst
                existing_li.properties = li_props
                existing_li.source_relation_id = rel.id
        else:
            from app.ontologies.formal_modeling.facts import record_link_fact
            li_obj = LinkInstance(
                id=li_id,
                ontology_id=ontology_id,
                link_type_id=lt_id,
                source_object_id=src_inst,
                target_object_id=tgt_inst,
                properties=li_props,
                source_relation_id=rel.id,
            )
            db.add(li_obj)
            db.flush()
            record_link_fact(
                db, ontology_id=ontology_id, link_instance_id=li_id,
                link_type_id=lt_id, exists=True, source="pipeline")
            summary["link_instances"] += 1

    # Materialized links must mirror the current Relation projection.  Keep the
    # immutable link facts, but tombstone and remove links whose source relation
    # disappeared after an approved lake snapshot or link-mapping change.
    current_relation_ids = {rel.id for rel in relations}
    from app.ontologies.formal_modeling.facts import record_link_fact
    for link in db.query(LinkInstance).filter(
            LinkInstance.ontology_id == ontology_id).all():
        relation_id = link.source_relation_id
        if relation_id and relation_id not in current_relation_ids:
            record_link_fact(
                db, ontology_id=ontology_id, link_instance_id=link.id,
                link_type_id=link.link_type_id, exists=False,
                source="pipeline-reconcile")
            db.delete(link)
            summary["removed_link_instances"] += 1

    # 投影改变了本体数据 → 推进修订戳，否则编辑器的乐观并发检测（以
    # OntologyProject.updated_at 为 revision）看不见投影发生过，旧画布
    # 保存会把刚灌入的数据整体覆盖掉
    from datetime import datetime as _dt, timezone as _tz
    from app.models.ontology import OntologyProject as _OP
    proj = db.query(_OP).filter(_OP.id == ontology_id).first()
    if proj is not None:
        proj.updated_at = _dt.now(_tz.utc)

    from app.ontologies.formal_modeling.validation import (
        validate_instance_contract, validate_link_instance_contract,
    )
    all_object_types = db.query(ObjectType).filter(ObjectType.ontology_id == ontology_id).all()
    all_link_types = db.query(LinkType).filter(LinkType.ontology_id == ontology_id).all()
    all_instances = db.query(ObjectInstance).filter(ObjectInstance.ontology_id == ontology_id).all()
    all_links = db.query(LinkInstance).filter(LinkInstance.ontology_id == ontology_id).all()
    contract_errors = [
        *validate_instance_contract(all_object_types, all_instances),
        *validate_link_instance_contract(all_link_types, all_instances, all_links),
    ]
    if contract_errors:
        preview = "; ".join(error.get("message", "") for error in contract_errors[:5])
        raise ValueError(f"正规本体投影违反运行契约（{len(contract_errors)} 项）: {preview}")

    db.flush()
    logger.info("正规本体投影完成 ontology=%s: %s", ontology_id, summary)
    return summary
