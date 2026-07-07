"""
后端动作引擎 (Action Engine)

执行动作 (ActionType)：参数校验 → 校验函数 → 事务性规则执行 → 审计日志。
支持规则类型：validation / create_object / update_property / create_link /
delete_link / notification / webhook（webhook 仅记录，不实际外呼，演示安全）。

治理语义：
  - requires_approval 的动作真实执行先落 status=pending 日志，等待人工批准/拒绝
    （决策本身写入事实流 kind=decision，批准/拒绝都可回放）。
  - 每条属性/链接变化都追加事实（source=action://<name>，caused_by=执行日志或决策事实）。
  - 单条规则失败 → 整个动作原子回滚，落 status=failed 日志（失败也是可追溯的历史）。
"""
from __future__ import annotations
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.ontology_formal import (
    ActionType, ObjectType, ObjectInstance, LinkInstance,
    ActionExecutionLog, OntologyFunction,
)
from app.services.formal.function_engine import execute_function
from app.services.formal.safe_eval import safe_eval, SafeEvalError


def _now():
    return datetime.now(timezone.utc)


class RuleExecutionError(Exception):
    """单条规则执行失败——携带规则名，供失败日志展示。"""
    def __init__(self, rule_name: str, message: str):
        super().__init__(f"规则「{rule_name}」执行失败: {message}")


def _resolve_value(mapping: dict, params: dict, source_props: Optional[dict],
                   db: Session, ontology_id: str) -> Any:
    """解析取值来源。expression 无 functionId 时直接对表达式求值
    （作用域 object=目标属性, params=参数），求值失败抛 SafeEvalError 让上层可见。"""
    st = mapping.get("sourceType")
    sv = mapping.get("sourceValue", "")
    if st == "parameter":
        return params.get(sv)
    if st in ("source_property", "property"):
        return (source_props or {}).get(sv)
    if st == "constant":
        try:
            return json.loads(sv)
        except (json.JSONDecodeError, TypeError):
            return sv
    if st in ("expression", "function"):
        fid = mapping.get("functionId")
        if fid:
            fn = db.query(OntologyFunction).filter(
                OntologyFunction.id == fid, OntologyFunction.ontology_id == ontology_id).first()
            if fn:
                r = execute_function(fn, db, ontology_id, obj_props=source_props, params=params)
                if not r.get("success"):
                    raise SafeEvalError(r.get("error") or "函数执行失败")
                return r.get("result")
            raise SafeEvalError(f"函数不存在: {fid}")
        if st == "expression" and sv:
            return safe_eval(sv, {"params": params, "object": source_props or {}})
    return None


_TPL_RE = re.compile(r"\{\{?\s*(params?|object)\.(\w+)\s*\}?\}")


def _render_template(tpl: str, params: dict, props: Optional[dict]) -> str:
    """模板替换：同时兼容 {{params.x}} / {param.x} / {{object.x}} / {object.x}
    （前端编辑器教用户写 {{params.x}}，历史模板可能是 {param.x}）。"""
    def _sub(m: re.Match) -> str:
        ns, key = m.group(1), m.group(2)
        src = params if ns in ("param", "params") else (props or {})
        v = (src or {}).get(key)
        return "" if v is None else str(v)
    return _TPL_RE.sub(_sub, tpl or "")


def _resolve_recipient(cfg: dict, params: dict, target_props: Optional[dict],
                       target_instance, db: Session, ontology_id: str) -> Optional[str]:
    """解析通知收件人。除常量/参数/属性外，支持沿链接跳转（订单 → 商家 → 邮箱）。"""
    src = cfg.get("recipientSource", "constant")
    val = cfg.get("recipient", "")
    if src == "constant":
        return val
    if src == "parameter":
        return params.get(val)
    if src == "property":
        return (target_props or {}).get(val)
    if src == "link":
        if not target_instance:
            return None
        link = db.query(LinkInstance).filter(
            LinkInstance.ontology_id == ontology_id,
            LinkInstance.link_type_id == cfg.get("linkTypeId"),
            LinkInstance.source_object_id == target_instance.id,
        ).first()
        if not link:
            return None
        related = db.query(ObjectInstance).filter(
            ObjectInstance.id == link.target_object_id,
            ObjectInstance.ontology_id == ontology_id,
        ).first()
        if not related:
            return None
        return (related.properties or {}).get(cfg.get("recipientProperty", "email"))
    return None


def _validate(action: ActionType, params: dict, target_props: Optional[dict],
              db: Session, ontology_id: str) -> list[str]:
    errors: list[str] = []

    # 1. 必填参数
    for p in (action.parameters or []):
        if p.get("required") and (params.get(p["name"]) in (None, "")):
            errors.append(f"参数「{p.get('displayName') or p['name']}」为必填")

    # 2. 绑定的校验函数
    if action.validation_function_id:
        fn = db.query(OntologyFunction).filter(
            OntologyFunction.id == action.validation_function_id,
            OntologyFunction.ontology_id == ontology_id).first()
        if fn:
            r = execute_function(fn, db, ontology_id, obj_props=target_props, params=params)
            if r.get("success"):
                vr = r.get("result") or {}
                if isinstance(vr, dict) and not vr.get("valid", True):
                    errors.extend(vr.get("errors") or ["校验失败"])
            else:
                errors.append(f"校验函数执行错误: {r.get('error')}")

    # 3. validation 类型规则 —— 表达式错误 fail-closed（写错的闸门要报错而不是放行）
    for rule in (action.rules or []):
        if rule.get("type") == "validation" and rule.get("enabled", True):
            cfg = rule.get("config", {})
            if cfg.get("functionId"):
                fn = db.query(OntologyFunction).filter(
                    OntologyFunction.id == cfg["functionId"],
                    OntologyFunction.ontology_id == ontology_id).first()
                if fn:
                    r = execute_function(fn, db, ontology_id, obj_props=target_props, params=params)
                    vr = r.get("result") or {}
                    if r.get("success") and isinstance(vr, dict) and not vr.get("valid", True):
                        errors.extend(vr.get("errors") or [cfg.get("errorMessage", "校验失败")])
                    elif not r.get("success"):
                        errors.append(f"校验规则「{rule.get('name')}」函数执行错误: {r.get('error')}")
            elif cfg.get("condition"):
                try:
                    ok = safe_eval(cfg["condition"], {"params": params, "object": target_props or {}})
                    if not ok:
                        errors.append(cfg.get("errorMessage") or f"{rule.get('name')} 校验失败")
                except SafeEvalError as e:
                    errors.append(f"校验规则「{rule.get('name')}」表达式错误（已拒绝执行）: {e}")
    return errors


def _fail_log(db: Session, ontology_id: str, action: Optional[ActionType], body,
              start: float, message: str, effects: Optional[list] = None,
              validation_errors: Optional[list] = None,
              actor_id: Optional[str] = None) -> dict:
    log = ActionExecutionLog(
        ontology_id=ontology_id,
        action_id=body.action_id,
        action_name=action.display_name if action else None,
        object_type_id=action.object_type_id if action else None,
        object_instance_id=body.target_instance_id,
        parameters=body.parameters or {}, status="failed",
        validation_errors=validation_errors or [], effects=effects or [],
        error_message=message, duration_ms=int((time.time() - start) * 1000),
        dry_run=body.dry_run, actor_id=actor_id,
    )
    db.add(log); db.commit(); db.refresh(log)
    return _log_to_dict(log)


def execute_action(db: Session, ontology_id: str, body,
                   actor_id: Optional[str] = None,
                   caused_by_fact: Optional[str] = None,
                   skip_approval: bool = False) -> dict[str, Any]:
    """body 是 RunActionRequest。返回 ActionExecutionLog dict (camelCase)。

    actor_id        发起人（哨兵触发为 None）
    caused_by_fact  因果指针覆盖（审批执行时传决策事实 id，对齐 caused_by=f010 语义）
    skip_approval   审批通过后的真正执行走此口，绕过 pending 闸门
    """
    start = time.time()
    action = db.query(ActionType).filter(
        ActionType.id == body.action_id, ActionType.ontology_id == ontology_id).first()

    if not action:
        return {"status": "failed", "errorMessage": "动作不存在", "actionId": body.action_id,
                "parameters": body.parameters, "effects": [], "validationErrors": [],
                "dryRun": body.dry_run, "executedAt": _now().isoformat(), "durationMs": 0}

    params = body.parameters or {}
    target_props: Optional[dict] = None
    target_instance = None
    if body.target_instance_id:
        target_instance = db.query(ObjectInstance).filter(
            ObjectInstance.id == body.target_instance_id,
            ObjectInstance.ontology_id == ontology_id).first()
        target_props = dict(target_instance.properties or {}) if target_instance else None

    # 校验
    errors = _validate(action, params, target_props, db, ontology_id)
    if errors:
        return _fail_log(db, ontology_id, action, body, start,
                         "校验未通过", validation_errors=errors, actor_id=actor_id)

    # —— HITL 审批闸门：真实执行先挂起，等人拍板（决策也是 Fact）——
    if action.requires_approval and not body.dry_run and not skip_approval:
        log = ActionExecutionLog(
            ontology_id=ontology_id, action_id=action.id, action_name=action.display_name,
            object_type_id=action.object_type_id, object_instance_id=body.target_instance_id,
            parameters=params, status="pending", validation_errors=[], effects=[],
            error_message=None, duration_ms=int((time.time() - start) * 1000),
            dry_run=False, actor_id=actor_id,
        )
        db.add(log); db.commit(); db.refresh(log)
        out = _log_to_dict(log)
        out["pendingApproval"] = True
        return out

    # 执行规则（原子：任一规则失败 → 全部回滚 → 落 failed 日志）
    effects: list[dict] = []
    created_by_type: dict[str, ObjectInstance] = {}   # objectTypeId → 本次创建的实例（供 created_object 引用）
    pending_facts: list[dict] = []                    # 属性事实缓冲
    pending_links: list[dict] = []                    # 链接事实缓冲 {link_id, link_type_id, exists}

    try:
        for rule in sorted((action.rules or []), key=lambda r: r.get("order", 0)):
            if not rule.get("enabled", True):
                continue
            rtype = rule.get("type")
            rname = rule.get("name") or rtype
            cfg = rule.get("config", {})

            if rtype == "create_object":
                ot_id = cfg.get("targetObjectTypeId")
                ot = db.query(ObjectType).filter(
                    ObjectType.id == ot_id, ObjectType.ontology_id == ontology_id).first()
                if not ot:
                    raise RuleExecutionError(rname, f"目标对象类型不存在: {ot_id}")
                props: dict = {}
                for m in cfg.get("propertyMappings", []):
                    try:
                        props[m["targetProperty"]] = _resolve_value(
                            m, params, target_props, db, ontology_id)
                    except SafeEvalError as e:
                        raise RuleExecutionError(rname, f"属性映射「{m.get('targetProperty')}」取值失败: {e}")
                if not body.dry_run:
                    inst = ObjectInstance(ontology_id=ontology_id, object_type_id=ot_id,
                                          properties=props, source="action")
                    db.add(inst); db.flush()
                    # 主键缺失时自动生成（与前端引擎一致：主键=实例 id）
                    pk_prop = next((p for p in (ot.properties or [])
                                    if isinstance(p, dict)
                                    and (p.get("id") == ot.primary_key or p.get("name") == ot.primary_key)),
                                   None)
                    if pk_prop and not props.get(pk_prop["name"]):
                        props = dict(props); props[pk_prop["name"]] = inst.id
                        inst.properties = props
                    created_by_type[ot_id] = inst
                    pending_facts.append({"instance_id": inst.id, "object_type_id": ot_id,
                                          "old_props": None, "new_props": dict(props)})
                effects.append({"type": "create_object", "description": f"创建对象 {ot.display_name}",
                                "targetObjectTypeId": ot_id, "newValue": props})

            elif rtype == "update_property":
                prop = cfg.get("targetProperty")
                if not body.dry_run and not target_instance:
                    raise RuleExecutionError(rname, "update_property 需要目标实例（执行时未选择实例）")
                try:
                    val = _resolve_value({"sourceType": cfg.get("valueSource", "constant"),
                                          "sourceValue": cfg.get("value", ""),
                                          "functionId": cfg.get("functionId")},
                                         params, target_props, db, ontology_id)
                except SafeEvalError as e:
                    raise RuleExecutionError(rname, f"取值失败: {e}")
                old = (target_props or {}).get(prop)
                if not body.dry_run and target_instance:
                    np = dict(target_instance.properties or {})
                    np[prop] = val
                    target_instance.properties = np
                    target_props = dict(np)  # 活视图：后续规则读到最新值，old 不再失真
                    pending_facts.append({"instance_id": target_instance.id,
                                          "object_type_id": target_instance.object_type_id,
                                          "old_props": {prop: old}, "new_props": {prop: val}})
                effects.append({"type": "update_property", "description": f"更新属性 {prop}",
                                "property": prop, "oldValue": old, "newValue": val})

            elif rtype == "create_link":
                lt_id = cfg.get("linkTypeId")
                tsrc = cfg.get("targetSource", "parameter")
                tval = cfg.get("targetValue", "")
                tgt: Optional[str] = None
                if tsrc == "created_object":
                    # targetValue = objectTypeId，取本次执行创建的该类型实例（与前端引擎一致）
                    inst = created_by_type.get(tval)
                    tgt = inst.id if inst else None
                    if tgt is None and not body.dry_run:
                        raise RuleExecutionError(rname, f"created_object 引用失败：本次执行未创建类型 {tval} 的对象")
                elif tsrc == "source":
                    tgt = target_instance.id if target_instance else None
                else:
                    try:
                        v = _resolve_value({"sourceType": "parameter" if tsrc == "expression" and not cfg.get("functionId") else tsrc,
                                            "sourceValue": tval,
                                            "functionId": cfg.get("functionId")},
                                           params, target_props, db, ontology_id)
                    except SafeEvalError as e:
                        raise RuleExecutionError(rname, f"目标解析失败: {e}")
                    tgt = str(v) if v not in (None, "") else None
                if not body.dry_run:
                    if not target_instance:
                        raise RuleExecutionError(rname, "create_link 需要源实例（执行时未选择实例）")
                    if not tgt:
                        raise RuleExecutionError(rname, f"create_link 无法解析目标对象: {tval}")
                    li = LinkInstance(ontology_id=ontology_id, link_type_id=lt_id,
                                      source_object_id=target_instance.id, target_object_id=str(tgt))
                    db.add(li); db.flush()
                    pending_links.append({"link_id": li.id, "link_type_id": lt_id, "exists": True})
                effects.append({"type": "create_link", "description": f"创建链接 {lt_id}",
                                "linkTypeId": lt_id, "newValue": tgt})

            elif rtype == "delete_link":
                lt_id = cfg.get("linkTypeId")
                if not body.dry_run:
                    if not target_instance:
                        raise RuleExecutionError(rname, "delete_link 需要源实例（执行时未选择实例）")
                    q = db.query(LinkInstance).filter(
                        LinkInstance.ontology_id == ontology_id,
                        LinkInstance.link_type_id == lt_id,
                        LinkInstance.source_object_id == target_instance.id)
                    rows = q.all()
                    for li in rows:
                        pending_links.append({"link_id": li.id, "link_type_id": lt_id, "exists": False})
                        db.delete(li)
                    effects.append({"type": "delete_link",
                                    "description": f"删除链接 {lt_id} × {len(rows)}",
                                    "linkTypeId": lt_id, "oldValue": len(rows)})
                else:
                    effects.append({"type": "delete_link",
                                    "description": f"删除链接 {lt_id}（模拟）", "linkTypeId": lt_id})

            elif rtype == "notification":
                channel = cfg.get("channel", "internal")
                recipient = _resolve_recipient(cfg, params, target_props, target_instance,
                                               db, ontology_id)
                message = _render_template(cfg.get("messageTemplate", ""), params, target_props)
                if not body.dry_run:
                    from app.models.sentinel import Notification
                    db.add(Notification(
                        ontology_id=ontology_id, channel=channel, recipient=recipient,
                        subject=cfg.get("subject") or action.display_name, body=message,
                        related_object_id=target_instance.id if target_instance else None,
                        action_id=action.id, status="delivered",
                    ))
                effects.append({"type": "notification", "channel": channel,
                                "recipient": recipient, "message": message,
                                "description": f"通知已投递 → {channel}:{recipient}"})

            elif rtype == "webhook":
                effects.append({"type": "webhook",
                                "description": f"调用 webhook {cfg.get('method', 'POST')} {cfg.get('url', '')} (演示环境仅记录)"})

    except RuleExecutionError as e:
        db.rollback()
        return _fail_log(db, ontology_id, action, body, start, str(e),
                         effects=effects, actor_id=actor_id)
    except Exception as e:  # noqa: BLE001 — 任何意外都必须留下失败日志而非 500
        db.rollback()
        return _fail_log(db, ontology_id, action, body, start,
                         f"动作执行意外失败: {e}", effects=effects, actor_id=actor_id)

    log = ActionExecutionLog(
        ontology_id=ontology_id, action_id=action.id, action_name=action.display_name,
        object_type_id=action.object_type_id, object_instance_id=body.target_instance_id,
        parameters=params, status="success", validation_errors=[], effects=effects,
        duration_ms=int((time.time() - start) * 1000), dry_run=body.dry_run,
        actor_id=actor_id,
    )
    db.add(log)
    db.flush()
    # 追加事实：来源=该动作；因果=决策事实(审批执行) 或 本次执行日志；随后派生重算
    causal = caused_by_fact or log.id
    src = f"action://{action.name or action.id}"
    if pending_facts or pending_links:
        from app.ontologies.formal_modeling.facts import record_property_facts, record_link_fact
        from app.ontologies.formal_modeling.derived import recompute_instance_derived
        affected: dict[str, list] = {}
        for pf in pending_facts:
            new_facts = record_property_facts(
                db, ontology_id=ontology_id,
                instance_id=pf["instance_id"], object_type_id=pf["object_type_id"],
                old_props=pf["old_props"], new_props=pf["new_props"],
                source=src, actor_id=actor_id, caused_by=causal,
            )
            if new_facts:
                affected.setdefault(pf["instance_id"], []).extend(new_facts)
        for pl in pending_links:
            record_link_fact(
                db, ontology_id=ontology_id,
                link_instance_id=pl["link_id"], link_type_id=pl["link_type_id"],
                exists=pl["exists"], source=src, actor_id=actor_id, caused_by=causal,
            )
        for iid, trigger in affected.items():
            inst = db.query(ObjectInstance).filter(
                ObjectInstance.id == iid,
                ObjectInstance.ontology_id == ontology_id).first()
            if inst:
                recompute_instance_derived(
                    db, ontology_id=ontology_id, instance=inst,
                    trigger_facts=trigger, caused_by=causal)
    db.commit()
    db.refresh(log)
    return _log_to_dict(log)


def _log_to_dict(log: ActionExecutionLog) -> dict[str, Any]:
    return {
        "id": log.id, "actionId": log.action_id, "actionName": log.action_name,
        "objectTypeId": log.object_type_id, "objectInstanceId": log.object_instance_id,
        "parameters": log.parameters or {}, "status": log.status,
        "validationErrors": log.validation_errors or [], "effects": log.effects or [],
        "errorMessage": log.error_message, "durationMs": log.duration_ms,
        "dryRun": log.dry_run, "executedAt": log.executed_at.isoformat() if log.executed_at else None,
        "actorId": log.actor_id,
        "decidedBy": log.decided_by,
        "decidedAt": log.decided_at.isoformat() if log.decided_at else None,
        "decisionReason": log.decision_reason,
        "relatedLogId": log.related_log_id,
    }
