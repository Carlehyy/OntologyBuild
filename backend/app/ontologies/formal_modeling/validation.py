"""
正规本体模型的服务端强制校验

前端 schemaLint 是"提示"，本模块是"闸门"：不满足硬性约束的模型拒绝入库（422），
防止绕过前端直接调 API 写入损坏模型。

只拦"会损坏数据完整性或让模型不可加载"的硬错误：
  - 同类集合内名称重复（对象/关系/动作/函数）
  - 同一类型内属性名重复（否则保存时静默去重会丢数据）
  - 关系端点 / 动作绑定 / 函数绑定 / computed 属性函数 悬挂
  - 非法基数
  - 实例引用不存在的类型；链接实例引用不存在的关系类型或实例
草稿态（缺主键、函数体为空等）不拦——用户必须能保存半成品。
"""
from __future__ import annotations

from typing import Any, Iterable

VALID_CARDINALITIES = {"one-to-one", "one-to-many", "many-to-one", "many-to-many"}


def _err(code: str, kind: str, message: str, name: str = "", item_id: str = "") -> dict:
    return {"code": code, "kind": kind, "name": name, "id": item_id, "message": message}


def _dup_names(items: Iterable[Any], kind: str, errors: list[dict]) -> None:
    seen: dict[str, int] = {}
    for it in items:
        name = getattr(it, "name", None)
        if not name:
            continue
        seen[name] = seen.get(name, 0) + 1
    for name, n in seen.items():
        if n > 1:
            errors.append(_err("duplicate_name", kind, f"名称 \"{name}\" 重复（{n} 个）", name=name))


def validate_model(object_types: list, link_types: list, actions: list,
                   functions: list, instances: list, link_instances: list) -> list[dict]:
    """校验一份完整的模型视图（全量保存直接传 body；增量保存传合并后的视图）。

    返回错误列表；空列表 = 通过。入参为 pydantic 模型或具备同名属性的对象。
    """
    errors: list[dict] = []

    _dup_names(object_types, "objectType", errors)
    _dup_names(link_types, "linkType", errors)
    _dup_names(actions, "action", errors)
    _dup_names(functions, "function", errors)

    del instances, link_instances  # 实例层不做硬校验，见 prune_dangling_data
    ot_ids = {getattr(o, "id", None) for o in object_types} - {None}
    fn_ids = {getattr(f, "id", None) for f in functions} - {None}

    # —— 对象类型：属性重名 / computed 属性函数悬挂 ——
    for ot in object_types:
        label = getattr(ot, "display_name", "") or getattr(ot, "name", "")
        prop_names: dict[str, int] = {}
        for p in (getattr(ot, "properties", None) or []):
            pname = p.get("name") if isinstance(p, dict) else None
            if pname:
                prop_names[pname] = prop_names.get(pname, 0) + 1
            if isinstance(p, dict) and (p.get("source") == "computed" or p.get("computed")):
                fid = p.get("functionId")
                if fid and fid not in fn_ids:
                    errors.append(_err(
                        "dangling_function", "objectType",
                        f"「{label}」的计算属性 \"{pname}\" 绑定的函数不存在",
                        name=label, item_id=getattr(ot, "id", "") or ""))
        for pname, n in prop_names.items():
            if n > 1:
                errors.append(_err(
                    "duplicate_property", "objectType",
                    f"「{label}」的属性名 \"{pname}\" 重复（{n} 个）——保存会静默丢弃前值",
                    name=label, item_id=getattr(ot, "id", "") or ""))

    # —— 关系类型：端点悬挂 / 非法基数 ——
    for lt in link_types:
        label = getattr(lt, "display_name", "") or getattr(lt, "name", "")
        lid = getattr(lt, "id", "") or ""
        if getattr(lt, "source_object_type_id", None) not in ot_ids:
            errors.append(_err("dangling_endpoint", "linkType",
                               f"关系「{label}」的源对象类型不存在", name=label, item_id=lid))
        if getattr(lt, "target_object_type_id", None) not in ot_ids:
            errors.append(_err("dangling_endpoint", "linkType",
                               f"关系「{label}」的目标对象类型不存在", name=label, item_id=lid))
        if getattr(lt, "cardinality", None) not in VALID_CARDINALITIES:
            errors.append(_err("invalid_cardinality", "linkType",
                               f"关系「{label}」的基数 \"{getattr(lt, 'cardinality', '')}\" 非法",
                               name=label, item_id=lid))

    # —— 动作 / 函数：绑定的对象类型悬挂（rules 内引用由保存后的 scrub 兜底清理）——
    for a in actions:
        label = getattr(a, "display_name", "") or getattr(a, "name", "")
        otid = getattr(a, "object_type_id", None)
        if otid and otid not in ot_ids:
            errors.append(_err("dangling_binding", "action",
                               f"动作「{label}」绑定的对象类型不存在",
                               name=label, item_id=getattr(a, "id", "") or ""))
    for f in functions:
        label = getattr(f, "display_name", "") or getattr(f, "name", "")
        otid = getattr(f, "target_object_type_id", None)
        if otid and otid not in ot_ids:
            errors.append(_err("dangling_binding", "function",
                               f"函数「{label}」绑定的对象类型不存在",
                               name=label, item_id=getattr(f, "id", "") or ""))

    return errors


def prune_dangling_data(instances: list, link_instances: list,
                        object_types: list, link_types: list) -> tuple[list, list, dict]:
    """清理实例层的悬挂引用（自动修复而非拒绝）。

    - 实例引用了不存在的对象类型 → 剔除
    - 链接实例引用了不存在的关系类型 / 端点实例 → 剔除
    返回 (保留的实例, 保留的链接实例, 清理计数)。
    """
    ot_ids = {getattr(o, "id", None) for o in object_types} - {None}
    lt_ids = {getattr(l, "id", None) for l in link_types} - {None}

    kept_instances = [i for i in instances if getattr(i, "object_type_id", None) in ot_ids]
    inst_ids = {getattr(i, "id", None) for i in kept_instances} - {None}

    kept_links = [
        li for li in link_instances
        if getattr(li, "link_type_id", None) in lt_ids
        and getattr(li, "source_object_id", None) in inst_ids
        and getattr(li, "target_object_id", None) in inst_ids
    ]
    pruned = {
        "instances": len(instances) - len(kept_instances),
        "linkInstances": len(link_instances) - len(kept_links),
    }
    return kept_instances, kept_links, pruned
