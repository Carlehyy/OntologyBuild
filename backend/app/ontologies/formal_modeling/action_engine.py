"""
后端动作引擎 (Action Engine)

执行动作 (ActionType)：参数校验 → 校验函数 → 事务性规则执行 → 审计日志。
支持规则类型：validation / create_object / update_property / create_link /
delete_link / notification。webhook 与外部通知在可靠投递器接入前明确失败，
绝不把“仅记录”伪装成已投递。

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
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ontology_formal import (
    ActionType, ObjectType, ObjectInstance, LinkType, LinkInstance,
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
        if sv not in params:
            raise SafeEvalError(f"参数不存在或未提供: {sv}")
        return params[sv]
    if st in ("source_property", "property"):
        if sv not in (source_props or {}):
            raise SafeEvalError(f"目标属性不存在: {sv}")
        return (source_props or {})[sv]
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
        raise SafeEvalError("expression/function 取值来源缺少表达式或 functionId")
    raise SafeEvalError(f"不支持的取值来源: {st}")


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


_MISSING = object()


def _parameter_default(parameter: dict) -> Any:
    """Return a declared parameter default without conflating it with ``None``."""
    for key in ("defaultValue", "default_value", "default"):
        if key in parameter:
            return deepcopy(parameter[key])
    return _MISSING


def _parameter_options(parameter: dict) -> list[Any] | None:
    raw = parameter.get("enum")
    if raw is None:
        raw = parameter.get("options")
    if raw is None:
        raw = parameter.get("allowedValues", parameter.get("allowed_values"))
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [item.get("value") if isinstance(item, dict) and "value" in item else item
            for item in raw]


def _type_error(name: str, expected: str) -> str:
    return f"参数「{name}」类型错误，期望 {expected}"


def _parameter_type_error(name: str, declared_type: str, value: Any) -> str | None:
    """Validate the action parameter vocabulary used by the graph editor.

    Coercion is deliberately avoided at the execution boundary: a string ``"1"``
    must not silently become the numeric value ``1`` just because a downstream
    expression happens to accept it.
    """
    ptype = (declared_type or "string").strip().lower()
    if value is None:
        return None
    if ptype in ("string", "reference"):
        return None if isinstance(value, str) else _type_error(name, ptype)
    if ptype in ("number", "float", "double"):
        return None if isinstance(value, (int, float)) and not isinstance(value, bool) \
            else _type_error(name, "number")
    if ptype in ("integer", "int"):
        return None if isinstance(value, int) and not isinstance(value, bool) \
            else _type_error(name, "integer")
    if ptype in ("boolean", "bool"):
        return None if isinstance(value, bool) else _type_error(name, "boolean")
    if ptype in ("array", "list", "object_set"):
        return None if isinstance(value, list) else _type_error(name, "array")
    if ptype in ("object", "dict"):
        return None if isinstance(value, dict) else _type_error(name, "object")
    if ptype == "date":
        if not isinstance(value, str):
            return _type_error(name, "ISO date")
        try:
            date.fromisoformat(value)
            return None
        except ValueError:
            return _type_error(name, "ISO date")
    if ptype in ("datetime", "timestamp"):
        if not isinstance(value, str):
            return _type_error(name, "ISO datetime")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return None
        except ValueError:
            return _type_error(name, "ISO datetime")
    if ptype in ("json", "any"):
        try:
            json.dumps(value)
            return None
        except (TypeError, ValueError):
            return _type_error(name, "JSON-compatible value")
    return f"参数「{name}」声明了不支持的类型「{declared_type}」"


def prepare_action_parameters(action: ActionType, supplied: dict | None) -> tuple[dict, list[str]]:
    """Apply defaults and enforce the declared action parameter contract.

    This helper is intentionally public to the ontology runtime: sentinels can
    rely on the exact same contract rather than maintaining a weaker validator.
    """
    if supplied is not None and not isinstance(supplied, dict):
        return {}, ["动作 parameters 必须是对象"]
    raw = dict(supplied or {})
    errors: list[str] = []
    definitions: dict[str, dict] = {}
    parameter_definitions = action.parameters or []
    if not isinstance(parameter_definitions, list):
        return {}, ["动作参数定义损坏：parameters 必须是数组"]
    for item in parameter_definitions:
        if not isinstance(item, dict):
            errors.append("动作参数定义损坏：参数项必须是对象")
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            errors.append("动作参数定义损坏：参数缺少 name")
            continue
        if name in definitions:
            errors.append(f"动作参数定义损坏：参数「{name}」重复")
            continue
        definitions[name] = item

    unknown = sorted(str(k) for k in raw.keys() if k not in definitions)
    if unknown:
        errors.append(f"动作收到未声明参数：{', '.join(unknown)}")

    params: dict[str, Any] = {}
    for name, definition in definitions.items():
        value = raw.get(name, _MISSING)
        if value is _MISSING:
            value = _parameter_default(definition)
        if value is _MISSING:
            if definition.get("required"):
                errors.append(f"参数「{definition.get('displayName') or name}」为必填")
            continue
        if value in (None, "") and definition.get("required"):
            errors.append(f"参数「{definition.get('displayName') or name}」为必填")
            continue

        type_error = _parameter_type_error(name, str(definition.get("type") or "string"), value)
        if type_error:
            errors.append(type_error)
            continue

        options = _parameter_options(definition)
        if options is not None:
            if not options:
                errors.append(f"参数「{name}」的 options/enum 定义无效")
                continue
            if value not in options:
                errors.append(f"参数「{name}」不在允许选项中: {options}")
                continue

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = definition.get("minimum", definition.get("min"))
            maximum = definition.get("maximum", definition.get("max"))
            try:
                if minimum is not None and value < float(minimum):
                    errors.append(f"参数「{name}」不得小于 {minimum}")
                if maximum is not None and value > float(maximum):
                    errors.append(f"参数「{name}」不得大于 {maximum}")
            except (TypeError, ValueError):
                errors.append(f"参数「{name}」的 min/max 定义无效")

        if isinstance(value, (str, list, dict)):
            min_length = definition.get("minLength", definition.get("min_length"))
            max_length = definition.get("maxLength", definition.get("max_length"))
            try:
                if min_length is not None and len(value) < int(min_length):
                    errors.append(f"参数「{name}」长度不得小于 {min_length}")
                if max_length is not None and len(value) > int(max_length):
                    errors.append(f"参数「{name}」长度不得大于 {max_length}")
            except (TypeError, ValueError):
                errors.append(f"参数「{name}」的 minLength/maxLength 定义无效")

        params[name] = value
    return params, errors


def _idempotency_key(body) -> str | None:
    value = getattr(body, "idempotency_key", None)
    if value is None:
        return None
    return value.strip() if isinstance(value, str) else ""


def _match_state_id(body) -> str | None:
    value = getattr(body, "sentinel_match_state_id", None)
    return value if isinstance(value, str) and value else None


def _idempotent_replay(
        db: Session, ontology_id: str, key: str | None) -> dict[str, Any] | None:
    """Return the durable owner of a successful/pending idempotent request.

    An approved HITL row owns the key while the actual execution is kept as a
    related audit log.  It is reusable only when that related execution really
    succeeded; approval by itself is not proof that downstream effects ran.
    """
    if not key:
        return None
    owner = db.query(ActionExecutionLog).filter(
        ActionExecutionLog.ontology_id == ontology_id,
        ActionExecutionLog.idempotency_key == key,
    ).first()
    if owner is None:
        return None
    if owner.status in ("success", "pending"):
        result = _log_to_dict(owner)
        result["idempotentReplay"] = True
        if owner.status == "pending":
            result["pendingApproval"] = True
        return result
    if owner.status == "approved" and owner.related_log_id:
        related = db.query(ActionExecutionLog).filter(
            ActionExecutionLog.id == owner.related_log_id,
            ActionExecutionLog.ontology_id == ontology_id,
        ).first()
        if related is not None and related.status == "success":
            result = _log_to_dict(related)
            result["idempotentReplay"] = True
            result["approvalLogId"] = owner.id
            return result
    return {
        "status": "failed",
        "errorMessage": f"幂等键已被不可复用状态占用: {owner.status}",
        "actionId": owner.action_id,
        "parameters": owner.parameters or {},
        "effects": [],
        "validationErrors": ["idempotency_key_conflict"],
        "dryRun": bool(owner.dry_run),
        "executedAt": _now().isoformat(),
        "durationMs": 0,
        "idempotentReplay": True,
    }


def _same_idempotent_request(log: ActionExecutionLog, action: ActionType,
                             body, params: dict, ontology_version: str | None) -> bool:
    return (
        log.action_id == action.id
        and log.object_instance_id == body.target_instance_id
        and (log.parameters or {}) == params
        and bool(log.dry_run) == bool(body.dry_run)
        and log.sentinel_match_state_id == _match_state_id(body)
        and log.ontology_version == ontology_version
    )


def _idempotency_owner(db: Session, ontology_id: str,
                       key: str | None) -> ActionExecutionLog | None:
    if not key:
        return None
    return db.query(ActionExecutionLog).filter(
        ActionExecutionLog.ontology_id == ontology_id,
        ActionExecutionLog.idempotency_key == key,
    ).first()


_SUPPORTED_RULE_TYPES = {
    "validation", "create_object", "update_property", "create_link",
    "delete_link", "notification", "webhook",
}


def _prepare_action_rules(action: ActionType) -> tuple[list[dict], list[str]]:
    raw = action.rules or []
    if not isinstance(raw, list):
        return [], ["动作规则定义损坏：rules 必须是数组"]
    rules: list[dict] = []
    errors: list[str] = []
    for index, rule in enumerate(raw):
        if not isinstance(rule, dict):
            errors.append(f"动作规则定义损坏：第 {index + 1} 项必须是对象")
            continue
        if not rule.get("enabled", True):
            continue
        rtype = rule.get("type")
        if rtype not in _SUPPORTED_RULE_TYPES:
            errors.append(f"不支持的动作规则类型: {rtype}")
            continue
        if not isinstance(rule.get("config", {}), dict):
            errors.append(f"规则「{rule.get('name') or rtype}」config 必须是对象")
            continue
        order = rule.get("order", 0)
        if not isinstance(order, (int, float)) or isinstance(order, bool):
            errors.append(f"规则「{rule.get('name') or rtype}」order 必须是数字")
            continue
        rules.append(rule)
    return rules, errors


def _validate(action: ActionType, params: dict, target_props: Optional[dict],
              db: Session, ontology_id: str,
              rules: Optional[list[dict]] = None) -> list[str]:
    errors: list[str] = []

    # 1. 绑定的校验函数。引用损坏必须 fail-closed，不能把安全闸门
    # 当成可选增强静默跳过。
    if action.validation_function_id:
        fn = db.query(OntologyFunction).filter(
            OntologyFunction.id == action.validation_function_id,
            OntologyFunction.ontology_id == ontology_id).first()
        if not fn:
            errors.append(f"动作绑定的校验函数不存在: {action.validation_function_id}")
        elif fn.function_type != "action_validation":
            errors.append(f"动作绑定的函数「{fn.display_name}」不是 action_validation 类型")
        else:
            r = execute_function(fn, db, ontology_id, obj_props=target_props, params=params)
            if r.get("success"):
                vr = r.get("result") or {}
                if isinstance(vr, dict) and not vr.get("valid", True):
                    errors.extend(vr.get("errors") or ["校验失败"])
            else:
                errors.append(f"校验函数执行错误: {r.get('error')}")

    # 2. validation 类型规则 —— 表达式错误/函数引用损坏均 fail-closed。
    for rule in (rules if rules is not None else (action.rules or [])):
        if rule.get("type") == "validation" and rule.get("enabled", True):
            cfg = rule.get("config", {})
            if cfg.get("functionId"):
                fn = db.query(OntologyFunction).filter(
                    OntologyFunction.id == cfg["functionId"],
                    OntologyFunction.ontology_id == ontology_id).first()
                if fn and fn.function_type == "action_validation":
                    r = execute_function(fn, db, ontology_id, obj_props=target_props, params=params)
                    vr = r.get("result") or {}
                    if r.get("success") and isinstance(vr, dict) and not vr.get("valid", True):
                        errors.extend(vr.get("errors") or [cfg.get("errorMessage", "校验失败")])
                    elif not r.get("success"):
                        errors.append(f"校验规则「{rule.get('name')}」函数执行错误: {r.get('error')}")
                elif fn:
                    errors.append(
                        f"校验规则「{rule.get('name')}」引用的函数不是 action_validation: "
                        f"{cfg['functionId']}")
                else:
                    errors.append(
                        f"校验规则「{rule.get('name')}」引用的函数不存在: {cfg['functionId']}")
            elif cfg.get("condition"):
                try:
                    ok = safe_eval(cfg["condition"], {"params": params, "object": target_props or {}})
                    if not ok:
                        errors.append(cfg.get("errorMessage") or f"{rule.get('name')} 校验失败")
                except SafeEvalError as e:
                    errors.append(f"校验规则「{rule.get('name')}」表达式错误（已拒绝执行）: {e}")
            else:
                errors.append(
                    f"校验规则「{rule.get('name')}」未配置 functionId 或 condition")
    return errors


def _fail_log(db: Session, ontology_id: str, action: Optional[ActionType], body,
              start: float, message: str, effects: Optional[list] = None,
              validation_errors: Optional[list] = None,
              actor_id: Optional[str] = None,
              parameters: Optional[dict] = None,
              ontology_version: str | None = None) -> dict:
    log = ActionExecutionLog(
        ontology_id=ontology_id,
        action_id=body.action_id,
        action_name=action.display_name if action else None,
        object_type_id=action.object_type_id if action else None,
        object_instance_id=body.target_instance_id,
        parameters=(body.parameters or {}) if parameters is None else parameters, status="failed",
        validation_errors=validation_errors or [], effects=effects or [],
        error_message=message, duration_ms=int((time.time() - start) * 1000),
        dry_run=body.dry_run, actor_id=actor_id,
        # Failed attempts never own the key: retrying the same deterministic
        # sentinel step must be possible after the transaction was rolled back.
        idempotency_key=None,
        sentinel_match_state_id=_match_state_id(body),
        ontology_version=ontology_version,
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

    from app.models.ontology import OntologyProject
    project_query = db.query(OntologyProject).filter(OntologyProject.id == ontology_id)
    if not body.dry_run:
        project_query = project_query.with_for_update()
    project = project_query.first()
    ontology_version = project.version if project is not None else None
    if project is None:
        return _fail_log(
            db, ontology_id, action, body, start, "本体不存在",
            validation_errors=["ontology_not_found"], actor_id=actor_id,
            ontology_version=ontology_version)
    if not body.dry_run:
        from app.config import settings
        if settings.environment == "production":
            from app.models.v2.mapping import OntologyMapping
            unhealthy_mappings = db.query(OntologyMapping).filter(
                OntologyMapping.ontology_id == ontology_id,
                OntologyMapping.status != "applied",
            ).count()
            if unhealthy_mappings:
                return _fail_log(
                    db, ontology_id, action, body, start,
                    "本体数据投影正在更新或处于失败态，真实动作已阻断；请先完成全量映射对账",
                    validation_errors=["ontology_projection_not_ready"],
                    actor_id=actor_id, ontology_version=ontology_version)

    params, parameter_errors = prepare_action_parameters(action, body.parameters)
    rules, rule_definition_errors = _prepare_action_rules(action)
    idem_key = _idempotency_key(body)
    if getattr(body, "idempotency_key", None) is not None and not idem_key:
        parameter_errors.append("idempotency_key 必须是非空字符串")
    if idem_key and len(idem_key) > 255:
        parameter_errors.append("idempotency_key 长度不得超过 255")
    target_props: Optional[dict] = None
    target_instance = None
    if body.target_instance_id:
        target_instance = db.query(ObjectInstance).filter(
            ObjectInstance.id == body.target_instance_id,
            ObjectInstance.ontology_id == ontology_id).first()
        target_props = dict(target_instance.properties or {}) if target_instance else None

    target_errors: list[str] = []
    if body.target_instance_id and target_instance is None:
        target_errors.append(f"目标实例不存在: {body.target_instance_id}")
    if action.object_type_id:
        if target_instance is None:
            target_errors.append("该动作绑定了对象类型，必须提供有效的目标实例")
        elif target_instance.object_type_id != action.object_type_id:
            target_errors.append(
                f"目标实例类型不匹配：动作要求 {action.object_type_id}，"
                f"实际为 {target_instance.object_type_id}")

    # 校验
    errors = [*parameter_errors, *target_errors, *rule_definition_errors]
    if not errors:
        errors.extend(_validate(action, params, target_props, db, ontology_id, rules))
    if errors:
        return _fail_log(db, ontology_id, action, body, start,
                         "校验未通过", validation_errors=errors, actor_id=actor_id,
                         parameters=params, ontology_version=ontology_version)

    # Check after normalization/validation so a reused key cannot smuggle a
    # different target or parameter payload behind the first successful call.
    owner = _idempotency_owner(db, ontology_id, idem_key)
    if owner is not None:
        if not _same_idempotent_request(
                owner, action, body, params, ontology_version):
            return _fail_log(
                db, ontology_id, action, body, start,
                "同一 idempotency_key 对应的动作、目标或参数不一致",
                validation_errors=["idempotency_key_payload_mismatch"],
                actor_id=actor_id, parameters=params,
                ontology_version=ontology_version,
            )
        return _idempotent_replay(db, ontology_id, idem_key)

    # —— HITL 审批闸门：真实执行先挂起，等人拍板（决策也是 Fact）——
    if action.requires_approval and not body.dry_run and not skip_approval:
        log = ActionExecutionLog(
            ontology_id=ontology_id, action_id=action.id, action_name=action.display_name,
            object_type_id=action.object_type_id, object_instance_id=body.target_instance_id,
            parameters=params, status="pending", validation_errors=[], effects=[],
            error_message=None, duration_ms=int((time.time() - start) * 1000),
            dry_run=False, actor_id=actor_id,
            idempotency_key=idem_key,
            sentinel_match_state_id=_match_state_id(body),
            ontology_version=ontology_version,
        )
        db.add(log)
        try:
            db.commit(); db.refresh(log)
        except IntegrityError:
            db.rollback()
            replay = _idempotent_replay(db, ontology_id, idem_key)
            if replay is not None:
                return replay
            raise
        out = _log_to_dict(log)
        out["pendingApproval"] = True
        return out

    # 执行规则（原子：任一规则失败 → 全部回滚 → 落 failed 日志）
    effects: list[dict] = []
    created_by_type: dict[str, ObjectInstance] = {}   # objectTypeId → 本次创建的实例（供 created_object 引用）
    declared_created_types: set[str] = set()          # dry-run 也要验证 created_object 链路
    pending_facts: list[dict] = []                    # 属性事实缓冲
    pending_links: list[dict] = []                    # 链接事实缓冲 {link_id, link_type_id, exists}

    try:
        for rule in sorted(rules, key=lambda r: r.get("order", 0)):
            if not rule.get("enabled", True):
                continue
            rtype = rule.get("type")
            rname = rule.get("name") or rtype
            cfg = rule.get("config", {})

            if rtype == "validation":
                # Already evaluated fail-closed above; it has no mutation phase.
                continue

            if rtype == "create_object":
                ot_id = cfg.get("targetObjectTypeId")
                ot = db.query(ObjectType).filter(
                    ObjectType.id == ot_id, ObjectType.ontology_id == ontology_id).first()
                if not ot:
                    raise RuleExecutionError(rname, f"目标对象类型不存在: {ot_id}")
                declared_created_types.add(ot_id)
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
                if not isinstance(prop, str) or not prop:
                    raise RuleExecutionError(rname, "update_property 缺少 targetProperty")
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
                lt = db.query(LinkType).filter(
                    LinkType.id == lt_id, LinkType.ontology_id == ontology_id).first()
                if not lt:
                    raise RuleExecutionError(rname, f"链接类型不存在: {lt_id}")
                if target_instance and target_instance.object_type_id != lt.source_object_type_id:
                    raise RuleExecutionError(
                        rname,
                        f"源实例类型不符合链接定义: {target_instance.object_type_id} != "
                        f"{lt.source_object_type_id}")
                if not target_instance:
                    raise RuleExecutionError(rname, "create_link 需要源实例")
                tsrc = cfg.get("targetSource", "parameter")
                tval = cfg.get("targetValue", "")
                tgt: Optional[str] = None
                if tsrc == "created_object":
                    # targetValue = objectTypeId，取本次执行创建的该类型实例（与前端引擎一致）
                    inst = created_by_type.get(tval)
                    tgt = inst.id if inst else None
                    if tval not in declared_created_types:
                        raise RuleExecutionError(rname, f"created_object 引用失败：本次执行未创建类型 {tval} 的对象")
                    if tval != lt.target_object_type_id:
                        raise RuleExecutionError(
                            rname,
                            f"created_object 类型不符合链接目标定义: {tval} != "
                            f"{lt.target_object_type_id}")
                    if body.dry_run:
                        tgt = f"<created:{tval}>"
                elif tsrc == "source":
                    tgt = target_instance.id if target_instance else None
                else:
                    try:
                        v = _resolve_value({"sourceType": tsrc,
                                            "sourceValue": tval,
                                            "functionId": cfg.get("functionId")},
                                           params, target_props, db, ontology_id)
                    except SafeEvalError as e:
                        raise RuleExecutionError(rname, f"目标解析失败: {e}")
                    tgt = str(v) if v not in (None, "") else None
                if not tgt:
                    raise RuleExecutionError(rname, f"create_link 无法解析目标对象: {tval}")
                target_object = None
                if tsrc != "created_object" or not body.dry_run:
                    target_object = db.query(ObjectInstance).filter(
                        ObjectInstance.id == str(tgt),
                        ObjectInstance.ontology_id == ontology_id).first()
                    if not target_object:
                        raise RuleExecutionError(rname, f"链接目标实例不存在: {tgt}")
                    if target_object.object_type_id != lt.target_object_type_id:
                        raise RuleExecutionError(
                            rname,
                            f"目标实例类型不符合链接定义: {target_object.object_type_id} != "
                            f"{lt.target_object_type_id}")
                if not body.dry_run:
                    li = LinkInstance(ontology_id=ontology_id, link_type_id=lt_id,
                                      source_object_id=target_instance.id, target_object_id=str(tgt))
                    db.add(li); db.flush()
                    pending_links.append({"link_id": li.id, "link_type_id": lt_id, "exists": True})
                effects.append({"type": "create_link", "description": f"创建链接 {lt_id}",
                                "linkTypeId": lt_id, "newValue": tgt})

            elif rtype == "delete_link":
                lt_id = cfg.get("linkTypeId")
                lt = db.query(LinkType).filter(
                    LinkType.id == lt_id, LinkType.ontology_id == ontology_id).first()
                if not lt:
                    raise RuleExecutionError(rname, f"链接类型不存在: {lt_id}")
                if target_instance and target_instance.object_type_id != lt.source_object_type_id:
                    raise RuleExecutionError(
                        rname,
                        f"源实例类型不符合链接定义: {target_instance.object_type_id} != "
                        f"{lt.source_object_type_id}")
                if not target_instance:
                    raise RuleExecutionError(rname, "delete_link 需要源实例")
                if not body.dry_run:
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
                if channel not in ("internal", "in_app", "in-app"):
                    raise RuleExecutionError(
                        rname,
                        f"外部通知通道「{channel}」尚未配置可靠投递器，已拒绝伪造 delivered")
                recipient = _resolve_recipient(cfg, params, target_props, target_instance,
                                               db, ontology_id)
                if not recipient:
                    raise RuleExecutionError(rname, "内部通知无法解析收件人")
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
                raise RuleExecutionError(
                    rname,
                    "webhook 尚未接入带超时、重试与幂等键的可靠投递器，已拒绝假执行")

            else:
                raise RuleExecutionError(rname or "unknown", f"不支持的动作规则类型: {rtype}")

    except RuleExecutionError as e:
        db.rollback()
        return _fail_log(db, ontology_id, action, body, start, str(e),
                         effects=effects, actor_id=actor_id,
                         ontology_version=ontology_version)
    except Exception as e:  # noqa: BLE001 — 任何意外都必须留下失败日志而非 500
        db.rollback()
        return _fail_log(db, ontology_id, action, body, start,
                         f"动作执行意外失败: {e}", effects=effects, actor_id=actor_id,
                         ontology_version=ontology_version)

    log = ActionExecutionLog(
        ontology_id=ontology_id, action_id=action.id, action_name=action.display_name,
        object_type_id=action.object_type_id, object_instance_id=body.target_instance_id,
        parameters=params, status="success", validation_errors=[], effects=effects,
        duration_ms=int((time.time() - start) * 1000), dry_run=body.dry_run,
        actor_id=actor_id,
        idempotency_key=idem_key,
        sentinel_match_state_id=_match_state_id(body),
        ontology_version=ontology_version,
    )
    db.add(log)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        replay = _idempotent_replay(db, ontology_id, idem_key)
        if replay is not None:
            return replay
        raise
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
    try:
        db.commit()
        db.refresh(log)
    except IntegrityError:
        # Another worker won the same key.  This transaction (including object,
        # link, fact and notification effects) is rolled back before replaying
        # the durable winner, so no duplicate side effect survives.
        db.rollback()
        replay = _idempotent_replay(db, ontology_id, idem_key)
        if replay is not None:
            return replay
        raise
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
        "idempotencyKey": log.idempotency_key,
        "sentinelMatchStateId": log.sentinel_match_state_id,
        "ontologyVersion": log.ontology_version,
    }
