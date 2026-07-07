"""
派生属性自动重算 — "能算的不存，输入一变自动重算"的执行器

实例的存储属性发生变化后（编辑器保存 / 实例 CRUD / Action 执行），
对该实例所属类型的全部 computed 属性重算：
  - 仅执行 expression 语言函数（TypeScript 函数由前端引擎计算，后端不覆盖）
  - 计算失败保留旧值，不污染投影
  - 值变化时：更新 fo_object_instances.computed 投影 +
    追加 kind=derived 的 PropertyFact（source=fn:<函数名>，
    derived_from=触发本次重算的输入事实 id 列表）——派生链可回溯
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from .models import ObjectType, ObjectInstance, OntologyFunction, PropertyFact
from .function_engine import execute_function
from .facts import record_property_facts


def recompute_instance_derived(
    db: Session,
    *,
    ontology_id: str,
    instance: ObjectInstance,
    object_type: Optional[ObjectType] = None,
    trigger_facts: Optional[list[PropertyFact]] = None,
    caused_by: Optional[str] = None,
) -> int:
    """重算单个实例的派生属性。返回值发生变化（并已追加派生事实）的属性数。"""
    if object_type is None:
        object_type = db.query(ObjectType).filter(
            ObjectType.id == instance.object_type_id,
            ObjectType.ontology_id == ontology_id,
        ).first()
    if not object_type:
        return 0

    computed_props = [
        p for p in (object_type.properties or [])
        if isinstance(p, dict)
        and (p.get("source") == "computed" or p.get("computed"))
        and p.get("functionId")
    ]
    if not computed_props:
        return 0

    trigger_ids = [f.id for f in (trigger_facts or []) if f.id]
    new_computed = dict(instance.computed or {})
    changed = 0
    projection_dirty = False

    for prop in computed_props:
        name = prop.get("name")
        if not name:
            continue
        fn = db.query(OntologyFunction).filter(
            OntologyFunction.id == prop["functionId"],
            OntologyFunction.ontology_id == ontology_id,
        ).first()
        # TS 函数前端算；禁用/缺失的函数跳过，保留旧值
        if not fn or not fn.enabled or fn.language != "expression":
            continue
        r = execute_function(fn, db, ontology_id, obj_props=instance.properties or {})
        if not r.get("success"):
            continue  # 计算失败保留旧值
        new_val = r.get("result")

        # 对比基准取"最新事实的值"而非投影——投影的 computed 可能被客户端
        # 整包保存清掉，用它判断会导致重复事实或 supersedes 断链
        last = (db.query(PropertyFact)
                .filter(PropertyFact.ontology_id == ontology_id,
                        PropertyFact.instance_id == instance.id,
                        PropertyFact.property_name == name)
                .order_by(PropertyFact.recorded_at.desc(), PropertyFact.id.desc())
                .first())
        last_val = (last.value or {}).get("v") if last is not None else None

        if new_computed.get(name) != new_val:
            new_computed[name] = new_val
            projection_dirty = True  # 投影修复：即使值与最新事实一致也要写回

        if last is not None and last_val == new_val:
            continue  # 值没变，不追加重复事实

        record_property_facts(
            db,
            ontology_id=ontology_id,
            instance_id=instance.id,
            object_type_id=instance.object_type_id,
            old_props={name: last_val} if last is not None else None,
            new_props={name: new_val},
            source=f"fn:{fn.name}",
            caused_by=caused_by,
            kind="derived",
            derived_from=trigger_ids or None,
        )
        changed += 1

    if projection_dirty:
        instance.computed = new_computed  # 投影同步更新
    return changed
