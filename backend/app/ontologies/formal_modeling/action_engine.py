"""
后端动作引擎 (Action Engine)

执行动作 (ActionType)：参数校验 → 校验函数 → 事务性规则执行 → 审计日志。
支持规则类型：validation / create_object / update_property / create_link /
delete_link / notification / webhook。webhook 经受限的 HTTP 投递器真实调用；
外部通知在可靠投递器接入前仍明确失败，绝不把“仅记录”伪装成已投递。

治理语义：
  - requires_approval 的动作真实执行先落 status=pending 日志，等待人工批准/拒绝
    （决策本身写入事实流 kind=decision，批准/拒绝都可回放）。
  - 每条属性/链接变化都追加事实（source=action://<name>，caused_by=执行日志或决策事实）。
  - 单条规则失败 → 整个动作原子回滚，落 status=failed 日志（失败也是可追溯的历史）。
"""
from __future__ import annotations
import ast
import hashlib
import json
import logging
import re
import time
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ontology_formal import (
    ActionType, ObjectType, ObjectInstance, LinkType, LinkInstance,
    ActionExecutionLog, OntologyFunction, PropertyFact,
)
from app.services.formal.function_engine import (
    derived_function_contract_issues,
    evaluate_function_contract,
    execute_function,
)
from app.services.formal.safe_eval import (
    safe_eval, SafeEvalError, validate_safe_expression,
)
from app.ontologies.formal_modeling.webhook_dispatcher import (
    WebhookDispatchError,
    dispatch_webhook,
    preview_webhook,
)
from app.ontologies.formal_modeling.validation import (
    LEGACY_SYSTEM_PROPERTIES,
    property_value_type_issue,
    validate_instance_contract,
    validate_link_instance_contract,
)
from app.ontologies.formal_modeling.derived import (
    DerivedComputationError,
    evaluate_instance_derived_projection,
    recompute_instance_derived,
)
from app.ontologies.formal_modeling.facts import (
    fact_order_clause,
    record_link_fact,
    record_property_facts,
)


logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


class RuleExecutionError(Exception):
    """单条规则执行失败——携带规则名，供失败日志展示。"""
    def __init__(self, rule_name: str, message: str):
        super().__init__(f"规则「{rule_name}」执行失败: {message}")


def _preview_values(context: Optional[dict], key: str) -> list:
    if not context:
        return []
    values = context.get(key) or []
    return list(values) if isinstance(values, (list, tuple)) else []


def _preview_find(context: Optional[dict], key: str, item_id: Any):
    wanted = str(item_id or "")
    return next((
        item for item in _preview_values(context, key)
        if _definition_id(item) == wanted
    ), None)


def _preview_instance_values(context: Optional[dict]) -> list:
    return _preview_values(context, "objects")


def _preview_link_values(context: Optional[dict]) -> list:
    return _preview_values(context, "links")


def _execute_action_function(
        fn, db: Session | None, ontology_id: str, *,
        obj_props: Optional[dict], params: dict,
        ontology_release_id: str | None,
        preview_context: Optional[dict] = None,
) -> dict[str, Any]:
    """Execute one function against the same data view as the Action preview.

    Snapshot trials cannot query the mutable Formal projection: doing that
    would combine candidate definitions/objects with the current release.  The
    regular runtime continues through ``execute_function`` while isolated
    previews provide their frozen object set to the shared function contract.
    """
    if (
        preview_context is None
        or not preview_context.get("isolated", False)
    ):
        return execute_function(
            fn, db, ontology_id, obj_props=obj_props, params=params,
            ontology_release_id=ontology_release_id)

    target_type_id = str(
        getattr(fn, "target_object_type_id", None) or "")

    def load_scope_objects() -> list[dict]:
        return [
            dict(getattr(item, "properties", None) or {})
            for item in _preview_instance_values(preview_context)
            if not target_type_id
            or str(getattr(item, "object_type_id", None) or "")
            == target_type_id
        ]

    return evaluate_function_contract(
        fn,
        obj_props=obj_props,
        params=params,
        object_loader=load_scope_objects,
    )


def _evaluate_context_derived_projection(
        db: Session | None, ontology_id: str, instance, object_type,
        definition_context: dict,
        ontology_release_id: str | None) -> dict:
    """Evaluate computed properties from one immutable definition context."""
    computed_props = [
        prop for prop in (getattr(object_type, "properties", None) or [])
        if isinstance(prop, dict)
        and (
            prop.get("source") == "computed"
            or bool(prop.get("computed"))
        )
    ]
    computed_names = {
        str(prop.get("name"))
        for prop in computed_props if prop.get("name")
    }
    stored_names = {
        str(prop.get("name"))
        for prop in (getattr(object_type, "properties", None) or [])
        if isinstance(prop, dict)
        and prop.get("name")
        and not (
            prop.get("source") == "computed"
            or bool(prop.get("computed"))
        )
    } | set(LEGACY_SYSTEM_PROPERTIES)
    projected = dict(getattr(instance, "computed", None) or {})
    type_label = str(
        getattr(object_type, "display_name", None)
        or getattr(object_type, "name", None)
        or getattr(object_type, "id", None)
        or "(未命名对象类型)"
    )
    for prop in computed_props:
        name = str(prop.get("name") or "")
        label = str(
            prop.get("displayName")
            or prop.get("display_name")
            or name
            or "(未命名派生属性)"
        )
        if not name:
            raise DerivedComputationError(
                f"对象类型「{type_label}」存在未命名的派生属性，无法安全重算")
        function_id = str(prop.get("functionId") or "").strip()
        if not function_id:
            projected.pop(name, None)
            continue
        fn = _preview_find(
            definition_context, "functions", function_id)
        if fn is None:
            raise DerivedComputationError(
                f"派生属性「{label}」引用的函数不存在: {function_id}")
        if not bool(getattr(fn, "enabled", True)):
            raise DerivedComputationError(
                f"派生属性「{label}」引用的函数已禁用")
        issues = derived_function_contract_issues(
            fn,
            object_type_id=str(getattr(object_type, "id", "") or ""),
            computed_property_names=computed_names,
            stored_property_names=stored_names,
            expected_return_type=str(prop.get("type") or ""),
        )
        if issues:
            raise DerivedComputationError(
                f"派生属性「{label}」引用的函数契约无效: "
                + "；".join(message for _, message in issues))
        if str(getattr(fn, "language", "") or "").strip().lower() \
                != "expression":
            projected.pop(name, None)
            continue
        result = _execute_action_function(
            fn, db, ontology_id,
            obj_props=dict(getattr(instance, "properties", None) or {}),
            params={},
            ontology_release_id=ontology_release_id,
            preview_context=definition_context,
        )
        if not result.get("success"):
            raise DerivedComputationError(
                f"派生属性「{label}」重算失败: "
                f"{result.get('error') or '未知错误'}")
        value = result.get("result")
        type_issue = property_value_type_issue(prop, value)
        if type_issue is not None:
            raise DerivedComputationError(
                f"派生属性「{label}」重算结果类型不匹配："
                f"期望 {type_issue.get('expected') or prop.get('type')}，"
                f"实际为 {type_issue.get('actual') or type(value).__name__}")
        projected[name] = value
    return projected


def _resolve_value(mapping: dict, params: dict, source_props: Optional[dict],
                   db: Session, ontology_id: str,
                   ontology_release_id: str | None = None,
                   preview_context: Optional[dict] = None) -> Any:
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
            fn = (
                _preview_find(preview_context, "functions", fid)
                if preview_context is not None else
                db.query(OntologyFunction).filter(
                    OntologyFunction.id == fid,
                    OntologyFunction.ontology_id == ontology_id,
                ).first()
            )
            if fn:
                r = _execute_action_function(
                    fn, db, ontology_id, obj_props=source_props,
                    params=params,
                    ontology_release_id=ontology_release_id,
                    preview_context=preview_context)
                if not r.get("success"):
                    raise SafeEvalError(r.get("error") or "函数执行失败")
                return r.get("result")
            raise SafeEvalError(f"函数不存在: {fid}")
        if st == "expression" and sv:
            scopes = {"params": params, "object": source_props or {}}
            _validate_expression_property_references(sv, scopes)
            return safe_eval(sv, scopes)
        raise SafeEvalError("expression/function 取值来源缺少表达式或 functionId")
    raise SafeEvalError(f"不支持的取值来源: {st}")


_TPL_RE = re.compile(r"\{\{?\s*(params?|object)\.(\w+)\s*\}?\}")


def _render_template(tpl: str, params: dict, props: Optional[dict]) -> str:
    """模板替换：同时兼容 {{params.x}} / {param.x} / {{object.x}} / {object.x}
    （前端编辑器教用户写 {{params.x}}，历史模板可能是 {param.x}）。

    Missing values fail closed.  Silently replacing a missing recipient/message
    value with an empty string creates a durable ``delivered`` record for
    content that was never actually complete.
    """
    def _sub(m: re.Match) -> str:
        ns, key = m.group(1), m.group(2)
        src = params if ns in ("param", "params") else (props or {})
        if key not in (src or {}) or (src or {}).get(key) is None:
            readable = "参数" if ns in ("param", "params") else "对象属性"
            raise SafeEvalError(f"通知模板引用的{readable}不存在或为空: {key}")
        return str((src or {})[key])
    rendered = _TPL_RE.sub(_sub, tpl or "")
    if re.search(r"\{\{[^{}]*\}\}|\{(?:params?|object)\.[^{}]*\}", rendered):
        raise SafeEvalError("通知模板包含无法解析的占位符")
    if "{{" in rendered or "}}" in rendered:
        raise SafeEvalError("通知模板包含不完整的占位符")
    return rendered


def _validate_expression_property_references(
        expression: str, scopes: dict[str, dict]) -> None:
    """Reject direct ``scope.property`` typos before safe_eval returns None."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        # ``safe_eval`` below owns the canonical syntax error message.
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name):
            continue
        scope_name = node.value.id
        if scope_name not in scopes:
            continue
        if node.attr not in scopes[scope_name]:
            raise SafeEvalError(
                f"{scope_name} 引用了不存在的属性: {node.attr}")


def _resolve_recipient(cfg: dict, params: dict, target_props: Optional[dict],
                       target_instance, db: Session, ontology_id: str,
                       *, virtual_links: Optional[list] = None,
                       virtual_objects: Optional[dict[str, Any]] = None,
                       excluded_link_ids: Optional[set[str]] = None,
                       isolated_links: Optional[list] = None,
                       ) -> Optional[str]:
    """解析通知收件人。

    Link lookup must be unambiguous.  Picking an arbitrary ``.first()`` row on
    one-to-many data can report a successful delivery to the wrong person.
    """
    src = cfg.get("recipientSource", "constant")
    val = cfg.get("recipient", "")
    if src == "constant":
        return val
    if src == "parameter":
        if val not in params:
            raise SafeEvalError(f"通知收件人参数不存在: {val}")
        return params.get(val)
    if src == "property":
        if val not in (target_props or {}):
            raise SafeEvalError(f"通知收件人属性不存在: {val}")
        return (target_props or {}).get(val)
    if src == "link":
        if not target_instance:
            raise SafeEvalError("沿链接解析通知收件人需要目标实例")
        excluded = excluded_link_ids or set()
        if isolated_links is not None:
            links = [
                item for item in isolated_links
                if item.link_type_id == cfg.get("linkTypeId")
                and item.source_object_id == target_instance.id
                and str(item.id) not in excluded
            ]
        else:
            link_query = db.query(LinkInstance).filter(
                LinkInstance.ontology_id == ontology_id,
                LinkInstance.link_type_id == cfg.get("linkTypeId"),
                LinkInstance.source_object_id == target_instance.id,
            )
            if target_instance.ontology_release_id is not None:
                link_query = link_query.filter(
                    LinkInstance.ontology_release_id
                    == target_instance.ontology_release_id)
            links = [
                item for item in link_query.order_by(
                    LinkInstance.id.asc()).all()
                if str(item.id) not in excluded
            ]
        links.extend([
            item for item in (virtual_links or [])
            if item.link_type_id == cfg.get("linkTypeId")
            and item.source_object_id == target_instance.id
        ])
        links = sorted(links, key=lambda item: str(item.id))[:2]
        if not links:
            raise SafeEvalError("通知收件人链接不存在")
        if len(links) > 1:
            raise SafeEvalError(
                "通知收件人链接不唯一；请通过动作参数明确传入本次匹配对象")
        link = links[0]
        related = (virtual_objects or {}).get(
            str(link.target_object_id))
        if related is None and isolated_links is None:
            related_query = db.query(ObjectInstance).filter(
                ObjectInstance.id == link.target_object_id,
                ObjectInstance.ontology_id == ontology_id,
            )
            if target_instance.ontology_release_id is not None:
                related_query = related_query.filter(
                    ObjectInstance.ontology_release_id
                    == target_instance.ontology_release_id)
            related = related_query.first()
        if not related:
            raise SafeEvalError("通知收件人关联对象不存在")
        prop = cfg.get("recipientProperty", "email")
        related_values = {
            **dict(getattr(related, "properties", None) or {}),
            **dict(getattr(related, "computed", None) or {}),
        }
        if prop not in related_values:
            raise SafeEvalError(f"通知收件人关联对象属性不存在: {prop}")
        return related_values.get(prop)
    raise SafeEvalError(f"不支持的通知收件人来源: {src}")


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


def _normalize_target_snapshot(
        body, action: ActionType) -> tuple[dict | None, list[str]]:
    raw = getattr(body, "target_snapshot", None)
    if raw is None:
        return None, []
    if not isinstance(raw, dict):
        return None, ["target_snapshot 必须是对象"]
    allowed = {
        "id", "objectTypeId", "object_type_id", "properties", "computed",
    }
    unknown = sorted(set(raw) - allowed)
    errors = (
        [f"target_snapshot 包含未知字段: {', '.join(unknown)}"]
        if unknown else []
    )
    snapshot_id = str(raw.get("id") or "").strip()
    object_type_id = str(
        raw.get("objectTypeId") or raw.get("object_type_id") or "").strip()
    properties = raw.get("properties")
    computed = raw.get("computed", {})
    if not snapshot_id:
        errors.append("target_snapshot 缺少 id")
    if not object_type_id:
        errors.append("target_snapshot 缺少 objectTypeId")
    if not isinstance(properties, dict):
        errors.append("target_snapshot.properties 必须是对象")
    if not isinstance(computed, dict):
        errors.append("target_snapshot.computed 必须是对象")
    target_id = getattr(body, "target_instance_id", None)
    if snapshot_id and snapshot_id != target_id:
        errors.append("target_snapshot.id 与 target_instance_id 不一致")
    if action.object_type_id and object_type_id != action.object_type_id:
        errors.append(
            "target_snapshot.objectTypeId 与动作绑定对象类型不一致")
    if errors:
        return None, errors
    normalized = {
        "id": snapshot_id,
        "objectTypeId": object_type_id,
        "properties": deepcopy(properties),
        "computed": deepcopy(computed),
    }
    try:
        encoded = json.dumps(
            normalized, ensure_ascii=False, default=str).encode("utf-8")
    except (TypeError, ValueError):
        return None, ["target_snapshot 无法序列化为 JSON"]
    if len(encoded) > 1_000_000:
        return None, ["target_snapshot 超过 1000000 bytes 限制"]
    return normalized, []


def _snapshot_rule_safe(rules: list[dict]) -> bool:
    for rule in rules:
        rule_type = rule.get("type")
        if rule_type not in ("validation", "notification", "webhook"):
            return False
        if (
            rule_type == "notification"
            and (rule.get("config") or {}).get("recipientSource") == "link"
        ):
            # A deleted leave target no longer has a live source row from which
            # link-scoped recipient resolution can be performed.
            return False
    return True


def action_supports_snapshot_execution(action) -> bool:
    """Whether an Action can execute after its live target row was deleted.

    Sentinel ``on_enter_leave`` stores an immutable target snapshot for leave
    replay.  Keep this decision shared with the execution engine so release
    gates cannot approve a rule set the runtime will inevitably reject.
    """
    raw_rules = _definition_field(action, "rules", default=[]) or []
    if (
        not isinstance(raw_rules, list)
        or any(not isinstance(rule, dict) for rule in raw_rules)
    ):
        return False
    enabled_rules = [
        rule for rule in raw_rules
        if isinstance(rule, dict) and rule.get("enabled", True)
    ]
    return _snapshot_rule_safe(enabled_rules)


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
                             body, params: dict, ontology_version: str | None,
                             ontology_release_id: str | None,
                             target_snapshot: dict | None) -> bool:
    return (
        log.action_id == action.id
        and log.object_instance_id == body.target_instance_id
        and (log.parameters or {}) == params
        and bool(log.dry_run) == bool(body.dry_run)
        and log.sentinel_match_state_id == _match_state_id(body)
        and log.ontology_version == ontology_version
        and log.ontology_release_id == ontology_release_id
        and (log.target_snapshot or None) == target_snapshot
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
    identifiers: set[str] = set()
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
        identifier = str(rule.get("id") or "").strip()
        if identifier:
            if identifier in identifiers:
                errors.append(f"动作规则定义损坏：规则 id 重复: {identifier}")
                continue
            identifiers.add(identifier)
        rules.append(rule)
    ordered = sorted(rules, key=lambda item: item.get("order", 0))
    first_webhook = next(
        (index for index, item in enumerate(ordered)
         if item.get("type") == "webhook"),
        None,
    )
    if first_webhook is not None and any(
        item.get("type") not in ("validation", "webhook")
        for item in ordered[first_webhook + 1:]
    ):
        errors.append(
            "Webhook 必须排在所有本地副作用规则之后；否则外部成功后本地失败无法回滚")
    first_effect = next(
        (index for index, item in enumerate(ordered)
         if item.get("type") != "validation"),
        None,
    )
    if first_effect is not None and any(
        item.get("type") == "validation"
        for item in ordered[first_effect + 1:]
    ):
        errors.append(
            "validation 必须排在所有副作用规则之前；"
            "运行时校验是动作前置条件，不读取中间变更")
    return rules, errors


def _definition_field(item, *names: str, default=None):
    if isinstance(item, dict):
        for name in names:
            if name in item:
                return item[name]
        return default
    for name in names:
        if hasattr(item, name):
            return getattr(item, name)
    return default


def _definition_id(item) -> str:
    return str(_definition_field(item, "id", default="") or "")


def _definition_properties(item) -> dict[str, dict]:
    return {
        str(prop.get("name")): prop
        for prop in (
            _definition_field(item, "properties", default=[]) or [])
        if isinstance(prop, dict) and prop.get("name")
    }


def _static_sample(definition: dict) -> Any:
    default = _parameter_default(definition)
    if default is not _MISSING:
        return default
    value_type = str(definition.get("type") or "string").lower()
    if value_type in ("number", "float", "double", "integer", "int"):
        return 1
    if value_type in ("boolean", "bool"):
        return True
    if value_type in ("array", "list", "object_set"):
        return []
    if value_type in ("object", "dict", "json"):
        return {}
    if value_type == "date":
        return "2026-01-01"
    if value_type in ("datetime", "timestamp"):
        return "2026-01-01T00:00:00+00:00"
    return "sample"


def validate_action_definition(
        action, object_types: list, link_types: list,
        functions: list | None = None) -> list[str]:
    """Pure/static validation for release gates and isolated trials.

    It performs no database writes and no outbound request.  The validator
    closes the gap where a snapshot could publish a syntactically valid Action
    whose first live Sentinel firing inevitably failed.
    """
    errors: list[str] = []
    action_name = str(
        _definition_field(
            action, "displayName", "display_name", "name",
            default=_definition_id(action)) or _definition_id(action))
    object_types_by_id = {
        _definition_id(item): item for item in object_types
        if _definition_id(item)
    }
    link_types_by_id = {
        _definition_id(item): item for item in link_types
        if _definition_id(item)
    }
    functions_by_id = (
        {
            _definition_id(item): item for item in functions
            if _definition_id(item)
        }
        if functions is not None else None
    )
    bound_type_id = str(_definition_field(
        action, "objectTypeId", "object_type_id", default="") or "")
    bound_type = object_types_by_id.get(bound_type_id)
    if bound_type_id and bound_type is None:
        errors.append(
            f"动作「{action_name}」绑定的对象类型不存在: {bound_type_id}")
    bound_properties = (
        _definition_properties(bound_type) if bound_type is not None else {})
    bound_samples = {
        name: _static_sample(definition)
        for name, definition in bound_properties.items()
    }

    raw_parameters = _definition_field(
        action, "parameters", default=[]) or []
    parameter_defs: dict[str, dict] = {}
    if not isinstance(raw_parameters, list):
        errors.append(f"动作「{action_name}」parameters 必须是数组")
        raw_parameters = []
    for parameter in raw_parameters:
        if not isinstance(parameter, dict):
            errors.append(f"动作「{action_name}」参数定义必须是对象")
            continue
        name = str(parameter.get("name") or "").strip()
        if not name:
            errors.append(f"动作「{action_name}」存在缺少 name 的参数")
            continue
        if name in parameter_defs:
            errors.append(f"动作「{action_name}」参数重复: {name}")
            continue
        parameter_defs[name] = parameter
        default = _parameter_default(parameter)
        if default is not _MISSING:
            type_error = _parameter_type_error(
                name, str(parameter.get("type") or "string"), default)
            if type_error:
                errors.append(f"动作「{action_name}」{type_error}")
    parameter_samples = {
        name: _static_sample(definition)
        for name, definition in parameter_defs.items()
    }
    action_validation_id = str(_definition_field(
        action, "validationFunctionId", "validation_function_id",
        default="") or "").strip()
    if action_validation_id and functions_by_id is not None:
        action_validation = functions_by_id.get(action_validation_id)
        if action_validation is None:
            errors.append(
                f"动作「{action_name}」绑定的校验函数不存在: "
                f"{action_validation_id}")
        else:
            function_type = str(_definition_field(
                action_validation, "functionType", "function_type",
                default="") or "")
            if function_type != "action_validation":
                errors.append(
                    f"动作「{action_name}」绑定的函数不是 "
                    f"action_validation: {action_validation_id}")

    raw_rules = _definition_field(action, "rules", default=[]) or []
    if not isinstance(raw_rules, list):
        return [*errors, f"动作「{action_name}」rules 必须是数组"]
    rules: list[dict] = []
    rule_ids: set[str] = set()
    for index, rule in enumerate(raw_rules):
        if not isinstance(rule, dict):
            errors.append(
                f"动作「{action_name}」第 {index + 1} 条规则必须是对象")
            continue
        if not rule.get("enabled", True):
            continue
        rule_type = rule.get("type")
        label = str(rule.get("name") or rule_type or index + 1)
        if rule_type not in _SUPPORTED_RULE_TYPES:
            errors.append(
                f"动作「{action_name}」规则「{label}」类型不支持: "
                f"{rule_type}")
            continue
        config = rule.get("config", {})
        if not isinstance(config, dict):
            errors.append(
                f"动作「{action_name}」规则「{label}」config 必须是对象")
            continue
        order = rule.get("order", 0)
        if not isinstance(order, (int, float)) or isinstance(order, bool):
            errors.append(
                f"动作「{action_name}」规则「{label}」order 必须是数字")
            continue
        rule_id = str(rule.get("id") or "").strip()
        if rule_id:
            if rule_id in rule_ids:
                errors.append(
                    f"动作「{action_name}」规则 id 重复: {rule_id}")
                continue
            rule_ids.add(rule_id)
        rules.append(rule)

    ordered = sorted(rules, key=lambda item: item.get("order", 0))
    effect_rules = [
        rule for rule in ordered if rule.get("type") != "validation"]
    if not effect_rules:
        errors.append(
            f"动作「{action_name}」没有启用的可执行副作用规则")
    first_webhook = next((
        index for index, rule in enumerate(ordered)
        if rule.get("type") == "webhook"
    ), None)
    if first_webhook is not None and any(
        rule.get("type") not in ("validation", "webhook")
        for rule in ordered[first_webhook + 1:]
    ):
        errors.append(
            f"动作「{action_name}」Webhook 必须位于所有本地副作用规则之后")
    first_effect = next((
        index for index, rule in enumerate(ordered)
        if rule.get("type") != "validation"
    ), None)
    if first_effect is not None and any(
        rule.get("type") == "validation"
        for rule in ordered[first_effect + 1:]
    ):
        errors.append(
            f"动作「{action_name}」validation 必须位于所有副作用规则之前")

    def rule_error(label: str, message: str) -> None:
        errors.append(
            f"动作「{action_name}」规则「{label}」{message}")

    def validate_function_reference(
            label: str, function_id: Any, purpose: str,
            *, expected_type: str | None = None) -> None:
        fid = str(function_id or "").strip()
        if not fid:
            rule_error(label, f"{purpose}缺少 functionId")
        elif functions_by_id is not None:
            function = functions_by_id.get(fid)
            if function is None:
                rule_error(label, f"{purpose}引用的函数不存在: {fid}")
            elif expected_type:
                function_type = str(_definition_field(
                    function, "functionType", "function_type",
                    default="") or "")
                if function_type != expected_type:
                    rule_error(
                        label,
                        f"{purpose}引用的函数类型必须是 {expected_type}: "
                        f"{fid}")

    def validate_mapping_source(
            label: str, mapping: dict,
            *, source_key: str = "sourceType",
            value_key: str = "sourceValue") -> None:
        source_type = str(mapping.get(source_key) or "").strip()
        source_value = str(mapping.get(value_key) or "").strip()
        if source_type == "parameter":
            if source_value not in parameter_defs:
                rule_error(
                    label, f"引用的动作参数不存在: {source_value}")
        elif source_type in ("source_property", "property"):
            if not bound_type_id:
                rule_error(label, "属性来源需要动作绑定对象类型")
            elif bound_properties and source_value not in bound_properties:
                rule_error(
                    label, f"引用的源对象属性不存在: {source_value}")
        elif source_type == "constant":
            return
        elif source_type == "expression":
            if mapping.get("functionId"):
                validate_function_reference(
                    label, mapping.get("functionId"), "表达式")
            elif not source_value:
                rule_error(label, "expression 缺少表达式")
            else:
                try:
                    validate_safe_expression(
                        source_value, allowed_names={"params", "object"})
                    _validate_expression_property_references(
                        source_value, {
                            "params": parameter_samples,
                            "object": bound_samples,
                        })
                except SafeEvalError as exc:
                    rule_error(label, f"表达式无效: {exc}")
        elif source_type == "function":
            validate_function_reference(
                label, mapping.get("functionId"), "函数来源")
        else:
            rule_error(label, f"不支持的取值来源: {source_type or '(空)'}")

    created_types_seen: set[str] = set()
    for rule in ordered:
        rule_type = str(rule.get("type") or "")
        label = str(rule.get("name") or rule_type)
        config = rule.get("config") or {}

        if rule_type == "validation":
            function_id = config.get("functionId")
            condition = str(config.get("condition") or "").strip()
            if function_id:
                validate_function_reference(
                    label, function_id, "校验规则",
                    expected_type="action_validation")
            elif condition:
                try:
                    validate_safe_expression(
                        condition, allowed_names={"params", "object"})
                    _validate_expression_property_references(
                        condition, {
                            "params": parameter_samples,
                            "object": bound_samples,
                        })
                except SafeEvalError as exc:
                    rule_error(label, f"校验表达式无效: {exc}")
            else:
                rule_error(label, "未配置 functionId 或 condition")
            continue

        if rule_type == "create_object":
            target_type_id = str(
                config.get("targetObjectTypeId") or "")
            target_type = object_types_by_id.get(target_type_id)
            if target_type is None:
                rule_error(
                    label, f"目标对象类型不存在: {target_type_id or '(空)'}")
                continue
            mappings = config.get("propertyMappings", [])
            if not isinstance(mappings, list):
                rule_error(label, "propertyMappings 必须是数组")
                continue
            target_properties = _definition_properties(target_type)
            mapped: set[str] = set()
            for mapping in mappings:
                if not isinstance(mapping, dict):
                    rule_error(label, "属性映射必须是对象")
                    continue
                target_property = str(
                    mapping.get("targetProperty") or "")
                if not target_property:
                    rule_error(label, "属性映射缺少 targetProperty")
                    continue
                if target_property in mapped:
                    rule_error(
                        label, f"目标属性重复映射: {target_property}")
                mapped.add(target_property)
                if (
                    target_properties
                    and target_property not in target_properties
                ):
                    rule_error(
                        label, f"目标对象属性不存在: {target_property}")
                elif target_property in target_properties:
                    definition = target_properties[target_property]
                    if (
                        definition.get("source") == "computed"
                        or bool(definition.get("computed"))
                    ):
                        rule_error(
                            label,
                            f"派生属性不能由 create_object 映射写入: "
                            f"{target_property}")
                validate_mapping_source(label, mapping)
            primary_key = str(_definition_field(
                target_type, "primaryKey", "primary_key", default="") or "")
            for name, definition in target_properties.items():
                computed = (
                    definition.get("source") == "computed"
                    or bool(definition.get("computed"))
                )
                is_primary = (
                    primary_key
                    and primary_key in (
                        str(definition.get("id") or ""), name)
                )
                if (
                    definition.get("required")
                    and not computed
                    and not is_primary
                    and name not in mapped
                ):
                    rule_error(
                        label, f"未映射必填目标属性: {name}")
            created_types_seen.add(target_type_id)
            continue

        if rule_type == "update_property":
            target_property = str(
                config.get("targetProperty") or "")
            if not bound_type_id:
                rule_error(label, "update_property 需要动作绑定对象类型")
            elif (
                bound_properties
                and target_property not in bound_properties
            ):
                rule_error(
                    label,
                    f"目标对象属性不存在: {target_property or '(空)'}")
            elif target_property in bound_properties:
                definition = bound_properties[target_property]
                if (
                    definition.get("source") == "computed"
                    or bool(definition.get("computed"))
                ):
                    rule_error(
                        label,
                        f"update_property 不能写入派生属性: "
                        f"{target_property}")
            mapping = {
                "sourceType": config.get("valueSource", "constant"),
                "sourceValue": config.get("value", ""),
                "functionId": config.get("functionId"),
            }
            validate_mapping_source(label, mapping)
            continue

        if rule_type in ("create_link", "delete_link"):
            link_type_id = str(config.get("linkTypeId") or "")
            link_type = link_types_by_id.get(link_type_id)
            if link_type is None:
                rule_error(
                    label, f"链接类型不存在: {link_type_id or '(空)'}")
                continue
            source_type_id = str(_definition_field(
                link_type, "sourceObjectTypeId",
                "source_object_type_id", default="") or "")
            target_type_id = str(_definition_field(
                link_type, "targetObjectTypeId",
                "target_object_type_id", default="") or "")
            if bound_type_id != source_type_id:
                rule_error(
                    label,
                    f"动作对象类型必须是链接源类型: "
                    f"{bound_type_id or '(空)'} != {source_type_id}")
            if rule_type == "create_link":
                target_source = str(
                    config.get("targetSource") or "parameter")
                target_value = str(config.get("targetValue") or "")
                if target_source == "parameter":
                    if target_value not in parameter_defs:
                        rule_error(
                            label,
                            f"链接目标参数不存在: {target_value}")
                elif target_source == "created_object":
                    if target_value not in created_types_seen:
                        rule_error(
                            label,
                            f"created_object 必须引用前序创建的对象类型: "
                            f"{target_value}")
                    if target_value != target_type_id:
                        rule_error(
                            label,
                            f"created_object 类型与链接目标类型不一致: "
                            f"{target_value} != {target_type_id}")
                elif target_source == "expression":
                    if not target_value:
                        rule_error(label, "链接目标表达式为空")
                    else:
                        try:
                            validate_safe_expression(
                                target_value,
                                allowed_names={"params", "object"})
                        except SafeEvalError as exc:
                            rule_error(
                                label, f"链接目标表达式无效: {exc}")
                elif target_source == "source":
                    if bound_type_id != target_type_id:
                        rule_error(
                            label,
                            "source 仅适用于源类型与目标类型相同的链接")
                else:
                    rule_error(
                        label, f"不支持的链接目标来源: {target_source}")
            else:
                condition = str(config.get("condition") or "").strip()
                if condition:
                    target_type = object_types_by_id.get(target_type_id)
                    target_properties = (
                        _definition_properties(target_type)
                        if target_type is not None else {})
                    link_properties = _definition_properties(link_type)
                    target_samples = {
                        name: _static_sample(definition)
                        for name, definition in target_properties.items()
                    }
                    link_samples = {
                        name: _static_sample(definition)
                        for name, definition in link_properties.items()
                    }
                    try:
                        validate_safe_expression(
                            condition,
                            allowed_names={
                                "object", "source", "target",
                                "link", "params",
                            },
                        )
                        _validate_expression_property_references(
                            condition, {
                                "object": bound_samples,
                                "source": bound_samples,
                                "target": target_samples,
                                "link": link_samples,
                                "params": parameter_samples,
                            })
                    except SafeEvalError as exc:
                        rule_error(
                            label, f"删除条件无效: {exc}")
            continue

        if rule_type == "notification":
            channel = str(config.get("channel") or "internal")
            if channel not in ("internal", "in_app", "in-app"):
                rule_error(
                    label,
                    f"通知通道 {channel} 未配置可靠投递器")
            recipient_source = str(
                config.get("recipientSource") or "constant")
            recipient = str(config.get("recipient") or "")
            if recipient_source == "constant":
                if not recipient.strip():
                    rule_error(label, "固定收件人不能为空")
            elif recipient_source == "parameter":
                if recipient not in parameter_defs:
                    rule_error(
                        label, f"收件人参数不存在: {recipient}")
            elif recipient_source == "property":
                if (
                    not recipient
                    or recipient not in bound_properties
                ):
                    rule_error(
                        label, f"收件人对象属性不存在: {recipient}")
            elif recipient_source == "link":
                link_type_id = str(config.get("linkTypeId") or "")
                link_type = link_types_by_id.get(link_type_id)
                if link_type is None:
                    rule_error(
                        label,
                        f"收件人链接类型不存在: {link_type_id or '(空)'}")
                else:
                    source_type_id = str(_definition_field(
                        link_type, "sourceObjectTypeId",
                        "source_object_type_id", default="") or "")
                    target_type_id = str(_definition_field(
                        link_type, "targetObjectTypeId",
                        "target_object_type_id", default="") or "")
                    if source_type_id != bound_type_id:
                        rule_error(
                            label, "收件人链接源类型与动作对象类型不一致")
                    target_type = object_types_by_id.get(target_type_id)
                    related_properties = (
                        _definition_properties(target_type)
                        if target_type is not None else {})
                    recipient_property = str(
                        config.get("recipientProperty") or "email")
                    if (
                        related_properties
                        and recipient_property not in related_properties
                    ):
                        rule_error(
                            label,
                            f"关联收件人属性不存在: {recipient_property}")
            else:
                rule_error(
                    label, f"不支持的收件人来源: {recipient_source}")
            message_template = str(
                config.get("messageTemplate") or "")
            if not message_template.strip():
                rule_error(label, "通知消息模板不能为空")
            for namespace, key in _TPL_RE.findall(message_template):
                if namespace in ("param", "params"):
                    if key not in parameter_defs:
                        rule_error(
                            label, f"通知模板参数不存在: {key}")
                elif key not in bound_properties:
                    rule_error(
                        label, f"通知模板对象属性不存在: {key}")
            residue = _TPL_RE.sub("", message_template)
            if "{{" in residue or "}}" in residue:
                rule_error(label, "通知模板包含无法解析的占位符")
            continue

        if rule_type == "webhook":
            try:
                preview_webhook(
                    config,
                    params=parameter_samples,
                    object_props=bound_samples,
                )
            except WebhookDispatchError as exc:
                rule_error(label, f"Webhook 配置无效: {exc}")

    return errors


def _rule_identity(rule: dict, ordinal: int) -> str:
    """Stable, non-secret identity used to scope one webhook delivery."""
    configured = str(rule.get("id") or "").strip()
    if configured:
        material = configured
    else:
        material = json.dumps(
            {
                "ordinal": ordinal,
                "type": rule.get("type"),
                "name": rule.get("name"),
                "order": rule.get("order", 0),
                "config": rule.get("config") or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _runtime_instance_query(
        db: Session, ontology_id: str, ontology_release_id: str | None):
    query = db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology_id)
    if ontology_release_id is not None:
        query = query.filter(
            ObjectInstance.ontology_release_id == ontology_release_id)
    return query


def _runtime_link_query(
        db: Session, ontology_id: str, ontology_release_id: str | None):
    query = db.query(LinkInstance).filter(
        LinkInstance.ontology_id == ontology_id)
    if ontology_release_id is not None:
        query = query.filter(
            LinkInstance.ontology_release_id == ontology_release_id)
    return query


def _contract_messages(errors: list[dict]) -> str:
    messages = [
        str(item.get("message") or item.get("code") or item)
        if isinstance(item, dict) else str(item)
        for item in errors
    ]
    return "；".join(messages[:8]) + (
        f"；另有 {len(messages) - 8} 项" if len(messages) > 8 else "")


def _validate_object_write(
        db: Session, ontology_id: str, ontology_release_id: str | None,
        instance_id: str, rule_name: str,
        *, definition_context: Optional[dict] = None) -> None:
    """Validate the exact post-write object projection before side effects commit."""
    db.flush()
    object_types = (
        _preview_values(definition_context, "object_types")
        if definition_context is not None else
        db.query(ObjectType).filter(
            ObjectType.ontology_id == ontology_id).all()
    )
    instances = _runtime_instance_query(
        db, ontology_id, ontology_release_id).all()
    errors = validate_instance_contract(
        object_types, instances, validate_ids={instance_id})
    if errors:
        raise RuleExecutionError(
            rule_name, f"对象实例契约校验失败: {_contract_messages(errors)}")


def _validate_object_candidate(
        db: Session, ontology_id: str, ontology_release_id: str | None,
        candidate, rule_name: str,
        *, extra_candidates: Optional[list] = None,
        preview_context: Optional[dict] = None) -> None:
    """Dry-run equivalent of ``_validate_object_write``."""
    if (
        preview_context is not None
        and preview_context.get("isolated", False)
    ):
        object_types = _preview_values(preview_context, "object_types")
        instances = _preview_instance_values(preview_context)
    else:
        object_types = (
            _preview_values(preview_context, "object_types")
            if preview_context is not None else
            db.query(ObjectType).filter(
                ObjectType.ontology_id == ontology_id).all()
        )
        instances = _runtime_instance_query(
            db, ontology_id, ontology_release_id).all()
    merged = [
        candidate if item.id == candidate.id else item
        for item in instances
    ]
    known_ids = {item.id for item in merged}
    for item in (extra_candidates or []):
        if item.id != candidate.id and item.id not in known_ids:
            merged.append(item)
            known_ids.add(item.id)
    if not any(item.id == candidate.id for item in instances):
        merged.append(candidate)
    errors = validate_instance_contract(
        object_types, merged, validate_ids={candidate.id})
    if errors:
        raise RuleExecutionError(
            rule_name, f"对象实例契约校验失败: {_contract_messages(errors)}")


def _validate_link_write(
        db: Session, ontology_id: str, ontology_release_id: str | None,
        link_id: str, rule_name: str,
        *, definition_context: Optional[dict] = None) -> None:
    """Validate endpoints, duplicate edges and cardinality before commit."""
    db.flush()
    link_types = (
        _preview_values(definition_context, "link_types")
        if definition_context is not None else
        db.query(LinkType).filter(
            LinkType.ontology_id == ontology_id).all()
    )
    instances = _runtime_instance_query(
        db, ontology_id, ontology_release_id).all()
    links = _runtime_link_query(
        db, ontology_id, ontology_release_id).all()
    errors = validate_link_instance_contract(
        link_types, instances, links, validate_ids={link_id})
    if errors:
        raise RuleExecutionError(
            rule_name, f"链接实例契约校验失败: {_contract_messages(errors)}")


def _validate_link_candidate(
        db: Session, ontology_id: str, ontology_release_id: str | None,
        candidate, rule_name: str,
        *, extra_instances: Optional[list] = None,
        existing_candidates: Optional[list] = None,
        excluded_link_ids: Optional[set[str]] = None,
        preview_context: Optional[dict] = None) -> None:
    """Dry-run equivalent of ``_validate_link_write``."""
    if (
        preview_context is not None
        and preview_context.get("isolated", False)
    ):
        link_types = _preview_values(preview_context, "link_types")
        instances = _preview_instance_values(preview_context)
        base_links = _preview_link_values(preview_context)
    else:
        link_types = (
            _preview_values(preview_context, "link_types")
            if preview_context is not None else
            db.query(LinkType).filter(
                LinkType.ontology_id == ontology_id).all()
        )
        instances = _runtime_instance_query(
            db, ontology_id, ontology_release_id).all()
        base_links = _runtime_link_query(
            db, ontology_id, ontology_release_id).all()
    known_instance_ids = {item.id for item in instances}
    for item in (extra_instances or []):
        if item.id not in known_instance_ids:
            instances.append(item)
            known_instance_ids.add(item.id)
    excluded = excluded_link_ids or set()
    links = [
        item for item in base_links
        if str(item.id) not in excluded
    ]
    links.extend(existing_candidates or [])
    links.append(candidate)
    errors = validate_link_instance_contract(
        link_types, instances, links, validate_ids={candidate.id})
    if errors:
        raise RuleExecutionError(
            rule_name, f"链接实例契约校验失败: {_contract_messages(errors)}")


def _validate(action: ActionType, params: dict, target_props: Optional[dict],
              db: Session, ontology_id: str,
              rules: Optional[list[dict]] = None,
              ontology_release_id: str | None = None,
              preview_context: Optional[dict] = None) -> list[str]:
    errors: list[str] = []

    # 1. 绑定的校验函数。引用损坏必须 fail-closed，不能把安全闸门
    # 当成可选增强静默跳过。
    if action.validation_function_id:
        fn = (
            _preview_find(
                preview_context, "functions",
                action.validation_function_id)
            if preview_context is not None else
            db.query(OntologyFunction).filter(
                OntologyFunction.id == action.validation_function_id,
                OntologyFunction.ontology_id == ontology_id).first()
        )
        if not fn:
            errors.append(f"动作绑定的校验函数不存在: {action.validation_function_id}")
        elif fn.function_type != "action_validation":
            errors.append(f"动作绑定的函数「{fn.display_name}」不是 action_validation 类型")
        else:
            r = _execute_action_function(
                fn, db, ontology_id, obj_props=target_props, params=params,
                ontology_release_id=ontology_release_id,
                preview_context=preview_context)
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
                fn = (
                    _preview_find(
                        preview_context, "functions", cfg["functionId"])
                    if preview_context is not None else
                    db.query(OntologyFunction).filter(
                        OntologyFunction.id == cfg["functionId"],
                        OntologyFunction.ontology_id == ontology_id).first()
                )
                if fn and fn.function_type == "action_validation":
                    r = _execute_action_function(
                        fn, db, ontology_id, obj_props=target_props,
                        params=params,
                        ontology_release_id=ontology_release_id,
                        preview_context=preview_context)
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
                    scopes = {
                        "params": params,
                        "object": target_props or {},
                    }
                    _validate_expression_property_references(
                        cfg["condition"], scopes)
                    ok = safe_eval(cfg["condition"], scopes)
                    if not ok:
                        errors.append(cfg.get("errorMessage") or f"{rule.get('name')} 校验失败")
                except SafeEvalError as e:
                    errors.append(f"校验规则「{rule.get('name')}」表达式错误（已拒绝执行）: {e}")
            else:
                errors.append(
                    f"校验规则「{rule.get('name')}」未配置 functionId 或 condition")
    return errors


def _failed_effects(effects: list, *, dry_run: bool) -> list:
    """Make failed audit rows explicit about transactional rollback.

    Local effects are only durable after the surrounding commit.  Retaining a
    successful-looking "delivered/created" description after rollback is a
    dangerous false positive for operators.  A webhook response is different:
    the remote side cannot be rolled back, so expose that uncertainty instead
    of claiming either outcome.
    """
    if dry_run:
        return deepcopy(effects)
    normalized: list = []
    for raw in effects:
        item = deepcopy(raw)
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        if (
            item.get("type") == "webhook"
            and (
                item.get("statusCode") is not None
                or item.get("externalDeliveryMayHaveOccurred") is True
            )
        ):
            item["localTransactionCommitted"] = False
            item["externalDeliveryMayHaveOccurred"] = True
            item["description"] = (
                "Webhook 请求已发出且可能到达远端，但本地事务随后回滚；"
                "必须按 idempotencyKey 对账"
            )
        else:
            item["committed"] = False
            item["rolledBack"] = True
            item.pop("inputFactIds", None)
            derived_count = item.pop("derivedFactCount", None)
            if derived_count:
                item["rolledBackDerivedFactCount"] = derived_count
            if item.get("type") == "notification":
                item["status"] = "rolled_back"
                item["description"] = "站内通知已回滚（未投递）"
            else:
                item["description"] = (
                    f"{item.get('description') or item.get('type') or '副作用'}"
                    "（已回滚）"
                )
        normalized.append(item)
    return normalized


def _fail_log(db: Session, ontology_id: str, action: Optional[ActionType], body,
              start: float, message: str, effects: Optional[list] = None,
              validation_errors: Optional[list] = None,
              actor_id: Optional[str] = None,
              parameters: Optional[dict] = None,
              ontology_version: str | None = None,
              ontology_release_id: str | None = None,
              target_snapshot: dict | None = None,
              suppress_log: bool = False) -> dict:
    normalized_parameters = (
        (body.parameters or {}) if parameters is None else parameters)
    normalized_effects = _failed_effects(
        effects or [], dry_run=bool(body.dry_run))
    if suppress_log:
        return {
            "id": None,
            "actionId": body.action_id,
            "actionName": action.display_name if action else None,
            "objectTypeId": action.object_type_id if action else None,
            "objectInstanceId": body.target_instance_id,
            "parameters": normalized_parameters,
            "status": "failed",
            "validationErrors": validation_errors or [],
            "effects": normalized_effects,
            "errorMessage": message,
            "durationMs": int((time.time() - start) * 1000),
            "dryRun": bool(body.dry_run),
            "executedAt": _now().isoformat(),
            "actorId": actor_id,
            "targetSnapshot": target_snapshot,
            "idempotencyKey": None,
            "sentinelMatchStateId": _match_state_id(body),
            "ontologyVersion": ontology_version,
            "ontologyReleaseId": ontology_release_id,
            "previewOnly": True,
            "sideEffects": "none",
        }
    log = ActionExecutionLog(
        ontology_id=ontology_id,
        action_id=body.action_id,
        action_name=action.display_name if action else None,
        object_type_id=action.object_type_id if action else None,
        object_instance_id=body.target_instance_id,
        parameters=normalized_parameters, status="failed",
        validation_errors=validation_errors or [],
        effects=normalized_effects,
        error_message=message, duration_ms=int((time.time() - start) * 1000),
        dry_run=body.dry_run, actor_id=actor_id,
        # Failed attempts never own the key: retrying the same deterministic
        # sentinel step must be possible after the transaction was rolled back.
        idempotency_key=None,
        sentinel_match_state_id=_match_state_id(body),
        ontology_version=ontology_version,
        ontology_release_id=ontology_release_id,
        target_snapshot=target_snapshot,
    )
    db.add(log); db.commit(); db.refresh(log)
    return _log_to_dict(log)


def execute_action(db: Session, ontology_id: str, body,
                   actor_id: Optional[str] = None,
                   caused_by_fact: Optional[str] = None,
                   skip_approval: bool = False,
                   *,
                   preview_only: bool = False,
                   preview_context: Optional[dict] = None,
                   expected_release_id: str | None = None) -> dict[str, Any]:
    """body 是 RunActionRequest。返回 ActionExecutionLog dict (camelCase)。

    actor_id        发起人（哨兵触发为 None）
    caused_by_fact  因果指针覆盖（审批执行时传决策事实 id，对齐 caused_by=f010 语义）
    skip_approval   审批通过后的真正执行走此口，绕过 pending 闸门
    preview_only    只返回动作计划；不落 ActionLog/Fact/Notification 且不发网络
    preview_context 隔离试跑的冻结定义、对象和关系；绝不查询正式运行投影
    expected_release_id 调用方捕获的发布节点；动作开始和提交前均做 CAS 校验
    """
    start = time.time()
    preview_only = bool(
        preview_only or getattr(body, "preview_only", False))
    expected_release_id = (
        expected_release_id
        or getattr(body, "expected_release_id", None)
    )
    lineage_release_conflict = False
    match_state_id = _match_state_id(body)
    if (
        expected_release_id is None
        and preview_context is None
        and db is not None
        and match_state_id is not None
    ):
        # Approval execution is intentionally started in a fresh Session by the
        # router.  Its request body retains the durable Sentinel match-state id
        # but historically lost the evaluator's expected_release_id.  Recover
        # the immutable release from the already-committed proposal/action
        # lineage so an approved R1 action cannot execute a mutable draft
        # ActionType while R1 is still current.
        lineage_release_ids = {
            str(row[0])
            for row in db.query(
                ActionExecutionLog.ontology_release_id,
            ).filter(
                ActionExecutionLog.ontology_id == ontology_id,
                ActionExecutionLog.sentinel_match_state_id == match_state_id,
                ActionExecutionLog.action_id == body.action_id,
                ActionExecutionLog.ontology_release_id.is_not(None),
            ).distinct().all()
            if row[0] is not None
        }
        if len(lineage_release_ids) == 1:
            expected_release_id = next(iter(lineage_release_ids))
        elif len(lineage_release_ids) > 1:
            lineage_release_conflict = True
    if preview_context is not None and not preview_only:
        return {
            "status": "failed",
            "errorMessage": "隔离 preview_context 只能用于 preview_only",
            "actionId": body.action_id,
            "parameters": body.parameters,
            "effects": [],
            "validationErrors": ["preview_context_requires_preview_only"],
            "dryRun": bool(body.dry_run),
            "executedAt": _now().isoformat(),
            "durationMs": 0,
        }
    if preview_only and not bool(body.dry_run):
        return {
            "status": "failed",
            "errorMessage": "preview_only 必须同时启用 dry_run",
            "actionId": body.action_id,
            "parameters": body.parameters,
            "effects": [],
            "validationErrors": ["preview_only_requires_dry_run"],
            "dryRun": bool(body.dry_run),
            "executedAt": _now().isoformat(),
            "durationMs": 0,
            "previewOnly": True,
            "sideEffects": "none",
        }

    definition_context = preview_context
    action = None

    from app.models.ontology import OntologyProject
    if preview_context is not None:
        project = None
        ontology_version = preview_context.get("ontology_version")
        ontology_release_id = preview_context.get("release_id")
    else:
        project_query = db.query(OntologyProject).filter(
            OntologyProject.id == ontology_id)
        # The same project-row lock is used by release promotion.  Holding it
        # across a real action makes the expected release a transaction fence,
        # not merely a best-effort read.
        if not body.dry_run:
            # CDC owns a dedicated FOR KEY SHARE release lease. PostgreSQL's
            # FOR NO KEY UPDATE is compatible with that lease while remaining
            # mutually exclusive with promotion/rollback's FOR UPDATE.
            project_query = project_query.with_for_update(key_share=True)
        project = project_query.first()
        from app.ontologies.release_context import (
            runtime_release_identity,
            runtime_release_version,
        )
        release_identity = (
            runtime_release_identity(db, ontology_id)
            if project is not None else None
        )
        ontology_version = (
            release_identity.version if release_identity is not None
            else runtime_release_version(db, ontology_id)
            if project is not None else None
        )
        ontology_release_id = (
            release_identity.id if release_identity is not None else None
        )

    if project is None and preview_context is None:
        return _fail_log(
            db, ontology_id, None, body, start, "本体不存在",
            validation_errors=["ontology_not_found"], actor_id=actor_id,
            ontology_version=ontology_version,
            ontology_release_id=ontology_release_id,
            suppress_log=preview_only)
    if lineage_release_conflict:
        return _fail_log(
            db, ontology_id, None, body, start,
            "哨兵动作血缘包含多个发布节点，已拒绝执行",
            validation_errors=["action_release_lineage_conflict"],
            actor_id=actor_id,
            ontology_version=ontology_version,
            ontology_release_id=ontology_release_id,
            suppress_log=preview_only,
        )
    if (
        expected_release_id is not None
        and ontology_release_id != expected_release_id
    ):
        return _fail_log(
            db, ontology_id, None, body, start,
            "动作捕获的发布节点已变化，已拒绝跨发布执行",
            validation_errors=["release_context_changed"],
            actor_id=actor_id,
            ontology_version=ontology_version,
            ontology_release_id=ontology_release_id,
            suppress_log=preview_only,
        )

    if (
        preview_context is None
        and expected_release_id is not None
        and ontology_release_id == expected_release_id
    ):
        # Runtime Formal definition tables remain editable compatibility
        # projections while a draft is being prepared.  An execution pinned to
        # release A must therefore resolve Action/Function/ObjectType/LinkType
        # from A's immutable snapshot, never from those mutable tables.
        try:
            from app.models.ontology_version import OntologyVersion
            from app.ontologies.versions.evolution_service import (
                snapshot_models,
            )
            release = db.query(OntologyVersion).filter(
                OntologyVersion.id == expected_release_id,
                OntologyVersion.ontology_id == ontology_id,
                OntologyVersion.node_kind == "release",
                OntologyVersion.lifecycle_status == "released",
            ).first()
            if release is None:
                raise ValueError("发布快照不存在或不是有效 release")
            frozen_models = snapshot_models(release.snapshot_formal or {})
            definition_context = {
                "isolated": False,
                "release_id": expected_release_id,
                "ontology_version": release.version_number,
                "object_types": frozen_models["objectTypes"],
                "link_types": frozen_models["linkTypes"],
                "actions": frozen_models["actions"],
                "functions": frozen_models["functions"],
            }
            action = _preview_find(
                definition_context, "actions", body.action_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "加载发布快照动作定义失败: ontology=%s release=%s action=%s",
                ontology_id, expected_release_id, body.action_id)
            return _fail_log(
                db, ontology_id, None, body, start,
                "发布快照动作定义无法加载，请检查服务端日志",
                validation_errors=["release_definition_invalid"],
                actor_id=actor_id,
                ontology_version=ontology_version,
                ontology_release_id=ontology_release_id,
                suppress_log=preview_only,
            )
    elif preview_context is not None:
        action = preview_context.get("action") or _preview_find(
            preview_context, "actions", body.action_id)
        if action is not None and _definition_id(action) != str(body.action_id):
            action = None
    else:
        action = db.query(ActionType).filter(
            ActionType.id == body.action_id,
            ActionType.ontology_id == ontology_id,
        ).first()

    if not action:
        return {"status": "failed", "errorMessage": "动作不存在", "actionId": body.action_id,
                "parameters": body.parameters, "effects": [], "validationErrors": [],
                "dryRun": body.dry_run, "executedAt": _now().isoformat(), "durationMs": 0,
                **({"previewOnly": True, "sideEffects": "none"}
                   if preview_only else {})}

    target_snapshot, snapshot_errors = _normalize_target_snapshot(body, action)
    # Draft runtime rows are an editable compatibility projection, not official
    # current-release data.  Published actions must stay inside the immutable
    # release; draft actions continue to operate on the draft projection.
    instance_release_id = (
        ontology_release_id
        if preview_context is not None or expected_release_id is not None
        else (
            ontology_release_id
            if (project.status or "") == "published"
            else None
        )
    )

    def current_release_error() -> str | None:
        if expected_release_id is None or preview_context is not None:
            return None
        # Real executions hold the project row lock.  Read-only previews cannot
        # lock, so the second read below detects a promotion that raced the
        # preview and rejects its mixed observation.
        db.refresh(project, attribute_names=["current_release_id"])
        if str(project.current_release_id or "") != str(expected_release_id):
            return "动作执行期间当前发布节点发生变化"
        return None
    params, parameter_errors = prepare_action_parameters(action, body.parameters)
    rules, rule_definition_errors = _prepare_action_rules(action)
    has_effect_rule = any(
        rule.get("type") != "validation" for rule in rules)
    approval_proposal_only = (
        action.requires_approval
        and not body.dry_run
        and not skip_approval
    )
    idem_key = _idempotency_key(body)
    if getattr(body, "idempotency_key", None) is not None and not idem_key:
        parameter_errors.append("idempotency_key 必须是非空字符串")
    if idem_key and len(idem_key) > 255:
        parameter_errors.append("idempotency_key 长度不得超过 255")

    # Replay is resolved before any validation that depends on mutable current
    # projection state.  Otherwise a successful "count 0 -> 1" request would
    # fail its original precondition when the caller retries after losing the
    # response, and HITL crash recovery could be blocked by its own committed
    # effect.
    owner = (
        None if preview_only
        else _idempotency_owner(db, ontology_id, idem_key)
    )
    if owner is not None:
        if not _same_idempotent_request(
                owner, action, body, params, ontology_version,
                ontology_release_id, target_snapshot):
            return _fail_log(
                db, ontology_id, action, body, start,
                "同一 idempotency_key 对应的动作、目标或参数不一致",
                validation_errors=["idempotency_key_payload_mismatch"],
                actor_id=actor_id, parameters=params,
                ontology_version=ontology_version,
                ontology_release_id=ontology_release_id,
                target_snapshot=target_snapshot,
                suppress_log=preview_only,
            )
        replay = _idempotent_replay(db, ontology_id, idem_key)
        if replay is not None:
            return replay

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
                    actor_id=actor_id, ontology_version=ontology_version,
                    ontology_release_id=ontology_release_id,
                    target_snapshot=target_snapshot,
                    suppress_log=preview_only)

    target_props: Optional[dict] = None
    target_instance = None
    if body.target_instance_id:
        if preview_context is not None:
            target_instance = next((
                item for item in _preview_instance_values(preview_context)
                if str(item.id) == str(body.target_instance_id)
            ), None)
        else:
            target_query = db.query(ObjectInstance).filter(
                ObjectInstance.id == body.target_instance_id,
                ObjectInstance.ontology_id == ontology_id)
            if instance_release_id is not None:
                target_query = target_query.filter(
                    ObjectInstance.ontology_release_id == instance_release_id)
            target_instance = target_query.first()
        target_props = ({
            **dict(target_instance.properties or {}),
            **dict(target_instance.computed or {}),
        } if target_instance else None)

    target_errors: list[str] = []
    snapshot_target = (
        target_instance is None
        and target_snapshot is not None
        and _snapshot_rule_safe(rules)
    )
    if snapshot_target:
        snapshot_type = (
            _preview_find(
                definition_context, "object_types",
                target_snapshot["objectTypeId"])
            if definition_context is not None else
            db.query(ObjectType).filter(
                ObjectType.id == target_snapshot["objectTypeId"],
                ObjectType.ontology_id == ontology_id,
            ).first()
        )
        if snapshot_type is None:
            target_errors.append(
                f"target_snapshot 引用的对象类型不存在: "
                f"{target_snapshot['objectTypeId']}")
        else:
            candidate = SimpleNamespace(
                id=target_snapshot["id"],
                object_type_id=target_snapshot["objectTypeId"],
                properties=target_snapshot["properties"],
                computed=target_snapshot["computed"],
            )
            contract_errors = validate_instance_contract(
                [snapshot_type], [candidate], validate_ids={candidate.id})
            target_errors.extend(
                f"target_snapshot 契约校验失败: {item.get('message')}"
                for item in contract_errors
            )
        target_props = {
            **target_snapshot["properties"],
            **target_snapshot["computed"],
        }
    elif body.target_instance_id and target_instance is None:
        target_errors.append(f"目标实例不存在: {body.target_instance_id}")
    if action.object_type_id:
        if target_instance is None and not snapshot_target:
            target_errors.append("该动作绑定了对象类型，必须提供有效的目标实例")
        elif (target_instance is not None
              and target_instance.object_type_id != action.object_type_id):
            target_errors.append(
                f"目标实例类型不匹配：动作要求 {action.object_type_id}，"
                f"实际为 {target_instance.object_type_id}")

    # 校验
    errors = [
        *parameter_errors, *snapshot_errors, *target_errors,
        *rule_definition_errors,
    ]
    if not errors:
        errors.extend(_validate(
            action, params, target_props, db, ontology_id, rules,
            ontology_release_id=instance_release_id,
            preview_context=definition_context))
    if not has_effect_rule and not approval_proposal_only:
        errors.append(
            "动作没有启用的可执行副作用规则，已拒绝记录伪成功")
    if errors:
        return _fail_log(db, ontology_id, action, body, start,
                         "校验未通过", validation_errors=errors, actor_id=actor_id,
                         parameters=params, ontology_version=ontology_version,
                         ontology_release_id=ontology_release_id,
                         target_snapshot=target_snapshot,
                         suppress_log=preview_only)

    # —— HITL 审批闸门：真实执行先挂起，等人拍板（决策也是 Fact）——
    if action.requires_approval and not body.dry_run and not skip_approval:
        release_error = current_release_error()
        if release_error:
            return _fail_log(
                db, ontology_id, action, body, start, release_error,
                validation_errors=["release_context_changed"],
                actor_id=actor_id, parameters=params,
                ontology_version=ontology_version,
                ontology_release_id=ontology_release_id,
                target_snapshot=target_snapshot,
                suppress_log=preview_only,
            )
        log = ActionExecutionLog(
            ontology_id=ontology_id, action_id=action.id, action_name=action.display_name,
            object_type_id=action.object_type_id, object_instance_id=body.target_instance_id,
            parameters=params, status="pending", validation_errors=[], effects=[],
            error_message=None, duration_ms=int((time.time() - start) * 1000),
            dry_run=False, actor_id=actor_id,
            idempotency_key=idem_key,
            sentinel_match_state_id=_match_state_id(body),
            ontology_version=ontology_version,
            ontology_release_id=ontology_release_id,
            target_snapshot=target_snapshot,
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

    # Pre-allocate the durable success-log identity before mutations.  Facts
    # have no foreign-key dependency on it, so every ordered mutation can use
    # one stable causal pointer while the log itself is inserted only after all
    # local rules have succeeded.  Any failure rolls all of them back.
    execution_log_id = str(uuid.uuid4())
    causal = caused_by_fact or execution_log_id
    src = f"action://{action.name or action.id}"

    def record_and_recompute(
        instance: ObjectInstance,
        *,
        old_props: Optional[dict],
        new_props: dict,
    ) -> tuple[list, int]:
        input_facts = record_property_facts(
            db,
            ontology_id=ontology_id,
            instance_id=instance.id,
            object_type_id=instance.object_type_id,
            old_props=old_props,
            new_props=new_props,
            source=src,
            actor_id=actor_id,
            caused_by=causal,
            ontology_version=ontology_version,
            ontology_release_id=ontology_release_id,
        )
        frozen_object_type = (
            _preview_find(
                definition_context, "object_types",
                instance.object_type_id)
            if definition_context is not None else None
        )
        if definition_context is None:
            derived_count = recompute_instance_derived(
                db,
                ontology_id=ontology_id,
                instance=instance,
                trigger_facts=input_facts,
                caused_by=causal,
            )
        elif frozen_object_type is None:
            raise DerivedComputationError(
                f"发布快照中缺少对象类型: {instance.object_type_id}")
        else:
            old_computed = dict(instance.computed or {})
            new_computed = _evaluate_context_derived_projection(
                db, ontology_id, instance, frozen_object_type,
                definition_context, instance_release_id)
            trigger_ids = [
                fact.id for fact in input_facts if fact.id]
            derived_count = 0
            for prop in (
                getattr(frozen_object_type, "properties", None) or []
            ):
                if not isinstance(prop, dict) or not (
                    prop.get("source") == "computed"
                    or bool(prop.get("computed"))
                ):
                    continue
                name = str(prop.get("name") or "")
                function_id = str(
                    prop.get("functionId") or "").strip()
                if not name or not function_id:
                    continue
                fn = _preview_find(
                    definition_context, "functions", function_id)
                if (
                    fn is None
                    or str(getattr(fn, "language", "") or "")
                    .strip().lower() != "expression"
                ):
                    continue
                last = (
                    db.query(PropertyFact)
                    .filter(
                        PropertyFact.ontology_id == ontology_id,
                        PropertyFact.instance_id == instance.id,
                        PropertyFact.property_name == name,
                    )
                    .order_by(*fact_order_clause())
                    .first()
                )
                last_value = (
                    (last.value or {}).get("v")
                    if last is not None else None
                )
                new_value = new_computed.get(name)
                if last is not None and last_value == new_value:
                    continue
                record_property_facts(
                    db,
                    ontology_id=ontology_id,
                    instance_id=instance.id,
                    object_type_id=instance.object_type_id,
                    old_props=(
                        {name: last_value}
                        if last is not None else None),
                    new_props={name: new_value},
                    source=f"fn:{getattr(fn, 'name', function_id)}",
                    caused_by=causal,
                    kind="derived",
                    derived_from=trigger_ids or None,
                    ontology_version=ontology_version,
                    ontology_release_id=ontology_release_id,
                )
                derived_count += 1
            if old_computed != new_computed:
                instance.computed = new_computed
        db.flush()
        return input_facts, derived_count

    # 执行规则（原子：任一规则失败 → 全部回滚 → 落 failed 日志）
    effects: list[dict] = []
    created_by_type: dict[str, ObjectInstance] = {}   # objectTypeId → 本次创建的实例（供 created_object 引用）
    declared_created_types: set[str] = set()          # dry-run 也要验证 created_object 链路
    pending_links: list[dict] = []                    # 链接事实缓冲 {link_id, link_type_id, exists}
    deferred_webhooks: list[tuple[int, dict, str, dict]] = []
    dry_run_created_objects: list = []
    dry_run_links: list = []
    dry_run_deleted_link_ids: set[str] = set()
    dry_run_target_properties = (
        dict(target_instance.properties or {})
        if body.dry_run and target_instance is not None
        else None
    )

    def preview_derived(candidate, object_type=None) -> dict:
        callback = (
            preview_context.get("derive")
            if preview_context is not None else None
        )
        if callable(callback):
            return dict(callback(
                candidate,
                object_type,
                [*dry_run_created_objects],
            ) or {})
        if definition_context is not None:
            return _evaluate_context_derived_projection(
                db, ontology_id, candidate, object_type,
                definition_context, instance_release_id)
        return evaluate_instance_derived_projection(
            db,
            ontology_id=ontology_id,
            instance=candidate,
            object_type=object_type,
        )
    dry_run_target_computed = (
        dict(target_instance.computed or {})
        if body.dry_run and target_instance is not None
        else None
    )

    try:
        for rule_ordinal, rule in enumerate(
                sorted(rules, key=lambda r: r.get("order", 0))):
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
                ot = (
                    _preview_find(
                        definition_context, "object_types", ot_id)
                    if definition_context is not None else
                    db.query(ObjectType).filter(
                        ObjectType.id == ot_id,
                        ObjectType.ontology_id == ontology_id).first()
                )
                if not ot:
                    raise RuleExecutionError(rname, f"目标对象类型不存在: {ot_id}")
                declared_created_types.add(ot_id)
                mappings = cfg.get("propertyMappings", [])
                if not isinstance(mappings, list):
                    raise RuleExecutionError(rname, "propertyMappings 必须是数组")
                target_definitions = _definition_properties(ot)
                props: dict = {}
                for m in mappings:
                    if not isinstance(m, dict) or not m.get("targetProperty"):
                        raise RuleExecutionError(
                            rname, "属性映射缺少 targetProperty")
                    target_property = str(m["targetProperty"])
                    definition = target_definitions.get(target_property)
                    if definition and (
                        definition.get("source") == "computed"
                        or bool(definition.get("computed"))
                    ):
                        raise RuleExecutionError(
                            rname,
                            f"派生属性不能由 create_object 映射写入: "
                            f"{target_property}",
                        )
                    try:
                        props[target_property] = _resolve_value(
                            m, params, target_props, db, ontology_id,
                            ontology_release_id=instance_release_id,
                            preview_context=definition_context)
                    except SafeEvalError as e:
                        raise RuleExecutionError(rname, f"属性映射「{m.get('targetProperty')}」取值失败: {e}")
                instance_id = str(uuid.uuid4())
                pk_prop = next((p for p in (ot.properties or [])
                                if isinstance(p, dict)
                                and (p.get("id") == ot.primary_key
                                     or p.get("name") == ot.primary_key)),
                               None)
                if pk_prop and not props.get(pk_prop["name"]):
                    props = dict(props)
                    props[pk_prop["name"]] = instance_id
                if not body.dry_run:
                    inst = ObjectInstance(id=instance_id,
                                          ontology_id=ontology_id,
                                          ontology_release_id=instance_release_id,
                                          object_type_id=ot_id,
                                          properties=props, source="action")
                    db.add(inst)
                    _validate_object_write(
                        db, ontology_id, instance_release_id, inst.id, rname,
                        definition_context=definition_context)
                    try:
                        record_and_recompute(
                            inst,
                            old_props=None,
                            new_props=dict(props),
                        )
                    except DerivedComputationError as exc:
                        raise RuleExecutionError(
                            rname, f"派生属性重算失败: {exc}") from exc
                    created_by_type[ot_id] = inst
                else:
                    candidate = SimpleNamespace(
                        id=instance_id,
                        object_type_id=ot_id,
                        properties=props,
                        computed={},
                        ontology_release_id=instance_release_id,
                    )
                    try:
                        candidate.computed = (
                            preview_derived(candidate, ot)
                        )
                    except DerivedComputationError as exc:
                        raise RuleExecutionError(
                            rname, f"派生属性重算失败: {exc}") from exc
                    _validate_object_candidate(
                        db, ontology_id, instance_release_id,
                        candidate,
                        rname,
                        extra_candidates=dry_run_created_objects,
                        preview_context=definition_context,
                    )
                    dry_run_created_objects.append(candidate)
                    created_by_type[ot_id] = candidate
                effects.append({"type": "create_object", "description": f"创建对象 {ot.display_name}",
                                "targetObjectTypeId": ot_id,
                                "targetInstanceId": instance_id,
                                "newValue": props})

            elif rtype == "update_property":
                prop = cfg.get("targetProperty")
                if not isinstance(prop, str) or not prop:
                    raise RuleExecutionError(rname, "update_property 缺少 targetProperty")
                if not body.dry_run and not target_instance:
                    raise RuleExecutionError(rname, "update_property 需要目标实例（执行时未选择实例）")
                if target_instance:
                    target_type = (
                        _preview_find(
                            definition_context, "object_types",
                            target_instance.object_type_id)
                        if definition_context is not None else
                        db.query(ObjectType).filter(
                            ObjectType.id == target_instance.object_type_id,
                            ObjectType.ontology_id == ontology_id,
                        ).first()
                    )
                    definition = (
                        _definition_properties(target_type).get(prop)
                        if target_type is not None else None
                    )
                    if definition and (
                        definition.get("source") == "computed"
                        or bool(definition.get("computed"))
                    ):
                        raise RuleExecutionError(
                            rname,
                            f"update_property 不能写入派生属性: {prop}",
                        )
                try:
                    val = _resolve_value({"sourceType": cfg.get("valueSource", "constant"),
                                          "sourceValue": cfg.get("value", ""),
                                         "functionId": cfg.get("functionId")},
                                         params, target_props, db, ontology_id,
                                         ontology_release_id=instance_release_id,
                                         preview_context=definition_context)
                except SafeEvalError as e:
                    raise RuleExecutionError(rname, f"取值失败: {e}")
                stored_before = (
                    dict(dry_run_target_properties or {})
                    if body.dry_run and target_instance is not None
                    else (
                        dict(target_instance.properties or {})
                        if target_instance else dict(target_props or {})
                    )
                )
                old = stored_before.get(prop)
                changed = prop not in stored_before or old != val
                np = dict(stored_before)
                np[prop] = val
                if not body.dry_run and target_instance:
                    input_facts: list = []
                    derived_count = 0
                    if changed:
                        target_instance.properties = np
                        _validate_object_write(
                            db, ontology_id, instance_release_id,
                            target_instance.id, rname,
                            definition_context=definition_context)
                        try:
                            input_facts, derived_count = (
                                record_and_recompute(
                                    target_instance,
                                    old_props=(
                                        {prop: old}
                                        if prop in stored_before else {}),
                                    new_props={prop: val},
                                )
                            )
                        except DerivedComputationError as exc:
                            raise RuleExecutionError(
                                rname,
                                f"派生属性重算失败: {exc}") from exc
                    # A semantic no-op must not invalidate an unbound/manual
                    # computed projection or emit CDC without fact lineage.
                    target_props = {
                        **dict(target_instance.properties or {}),
                        **dict(target_instance.computed or {}),
                    }
                elif target_instance:
                    if changed:
                        candidate = SimpleNamespace(
                            id=target_instance.id,
                            object_type_id=target_instance.object_type_id,
                            properties=np,
                            computed=dict(dry_run_target_computed or {}),
                            ontology_release_id=(
                                target_instance.ontology_release_id),
                        )
                        try:
                            candidate.computed = (
                                preview_derived(candidate, target_type)
                            )
                        except DerivedComputationError as exc:
                            raise RuleExecutionError(
                                rname,
                                f"派生属性重算失败: {exc}") from exc
                        _validate_object_candidate(
                            db, ontology_id, instance_release_id,
                            candidate,
                            rname,
                            preview_context=definition_context,
                        )
                        dry_run_target_properties = dict(
                            candidate.properties or {})
                        dry_run_target_computed = dict(
                            candidate.computed or {})
                    target_props = {
                        **dict(dry_run_target_properties or {}),
                        **dict(dry_run_target_computed or {}),
                    }
                effects.append({"type": "update_property", "description": f"更新属性 {prop}",
                                "property": prop, "oldValue": old, "newValue": val,
                                "changed": changed,
                                **({
                                    "inputFactIds": [
                                        fact.id for fact in input_facts],
                                    "derivedFactCount": derived_count,
                                } if not body.dry_run and target_instance
                                   else {})})

            elif rtype == "create_link":
                lt_id = cfg.get("linkTypeId")
                lt = (
                    _preview_find(
                        definition_context, "link_types", lt_id)
                    if definition_context is not None else
                    db.query(LinkType).filter(
                        LinkType.id == lt_id,
                        LinkType.ontology_id == ontology_id).first()
                )
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
                elif tsrc == "source":
                    tgt = target_instance.id if target_instance else None
                else:
                    try:
                        v = _resolve_value({"sourceType": tsrc,
                                            "sourceValue": tval,
                                            "functionId": cfg.get("functionId")},
                                           params, target_props, db, ontology_id,
                                           ontology_release_id=instance_release_id,
                                           preview_context=definition_context)
                    except SafeEvalError as e:
                        raise RuleExecutionError(rname, f"目标解析失败: {e}")
                    tgt = str(v) if v not in (None, "") else None
                if not tgt:
                    raise RuleExecutionError(rname, f"create_link 无法解析目标对象: {tval}")
                target_object = (
                    created_by_type.get(tval)
                    if tsrc == "created_object" and body.dry_run
                    else None
                )
                if target_object is None:
                    if (
                        preview_context is not None
                        and preview_context.get("isolated", False)
                    ):
                        target_object = next((
                            item for item in _preview_instance_values(
                                preview_context)
                            if str(item.id) == str(tgt)
                        ), None)
                    else:
                        target_object_query = db.query(ObjectInstance).filter(
                            ObjectInstance.id == str(tgt),
                            ObjectInstance.ontology_id == ontology_id)
                        if instance_release_id is not None:
                            target_object_query = target_object_query.filter(
                                ObjectInstance.ontology_release_id
                                == instance_release_id)
                        target_object = target_object_query.first()
                    if not target_object:
                        raise RuleExecutionError(rname, f"链接目标实例不存在: {tgt}")
                    if target_object.object_type_id != lt.target_object_type_id:
                        raise RuleExecutionError(
                            rname,
                            f"目标实例类型不符合链接定义: {target_object.object_type_id} != "
                            f"{lt.target_object_type_id}")
                if not body.dry_run:
                    li = LinkInstance(id=str(uuid.uuid4()),
                                      ontology_id=ontology_id,
                                      ontology_release_id=instance_release_id,
                                      link_type_id=lt_id,
                                      source_object_id=target_instance.id, target_object_id=str(tgt))
                    db.add(li)
                    _validate_link_write(
                        db, ontology_id, instance_release_id, li.id, rname,
                        definition_context=definition_context)
                    pending_links.append({"link_id": li.id, "link_type_id": lt_id, "exists": True})
                else:
                    candidate_link = SimpleNamespace(
                        id=f"dry-run:{uuid.uuid4()}",
                        link_type_id=lt_id,
                        source_object_id=target_instance.id,
                        target_object_id=str(tgt),
                        properties={},
                    )
                    _validate_link_candidate(
                        db, ontology_id, instance_release_id,
                        candidate_link,
                        rname,
                        extra_instances=dry_run_created_objects,
                        existing_candidates=dry_run_links,
                        excluded_link_ids=dry_run_deleted_link_ids,
                        preview_context=definition_context,
                    )
                    dry_run_links.append(candidate_link)
                effects.append({"type": "create_link", "description": f"创建链接 {lt_id}",
                                "linkTypeId": lt_id, "newValue": tgt})

            elif rtype == "delete_link":
                lt_id = cfg.get("linkTypeId")
                lt = (
                    _preview_find(
                        definition_context, "link_types", lt_id)
                    if definition_context is not None else
                    db.query(LinkType).filter(
                        LinkType.id == lt_id,
                        LinkType.ontology_id == ontology_id).first()
                )
                if not lt:
                    raise RuleExecutionError(rname, f"链接类型不存在: {lt_id}")
                if target_instance and target_instance.object_type_id != lt.source_object_type_id:
                    raise RuleExecutionError(
                        rname,
                        f"源实例类型不符合链接定义: {target_instance.object_type_id} != "
                        f"{lt.source_object_type_id}")
                if not target_instance:
                    raise RuleExecutionError(rname, "delete_link 需要源实例")
                if (
                    preview_context is not None
                    and preview_context.get("isolated", False)
                ):
                    candidates = sorted([
                        item for item in _preview_link_values(preview_context)
                        if item.link_type_id == lt_id
                        and item.source_object_id == target_instance.id
                    ], key=lambda item: str(item.id))
                else:
                    q = db.query(LinkInstance).filter(
                        LinkInstance.ontology_id == ontology_id,
                        LinkInstance.link_type_id == lt_id,
                        LinkInstance.source_object_id == target_instance.id)
                    if instance_release_id is not None:
                        q = q.filter(
                            LinkInstance.ontology_release_id
                            == instance_release_id)
                    candidates = q.order_by(LinkInstance.id.asc()).all()
                if body.dry_run:
                    candidates = [
                        item for item in candidates
                        if str(item.id) not in dry_run_deleted_link_ids
                    ]
                    candidates.extend([
                        item for item in dry_run_links
                        if item.link_type_id == lt_id
                        and item.source_object_id == target_instance.id
                    ])
                    candidates = sorted(
                        candidates, key=lambda item: str(item.id))
                condition = str(cfg.get("condition") or "").strip()
                rows: list = []
                for li in candidates:
                    if not condition:
                        rows.append(li)
                        continue
                    related_target = next((
                        item for item in dry_run_created_objects
                        if item.id == li.target_object_id
                    ), None)
                    if related_target is None:
                        if (
                            preview_context is not None
                            and preview_context.get("isolated", False)
                        ):
                            related_target = next((
                                item for item in _preview_instance_values(
                                    preview_context)
                                if item.id == li.target_object_id
                            ), None)
                        else:
                            target_query = db.query(ObjectInstance).filter(
                                ObjectInstance.id == li.target_object_id,
                                ObjectInstance.ontology_id == ontology_id,
                            )
                            if instance_release_id is not None:
                                target_query = target_query.filter(
                                    ObjectInstance.ontology_release_id
                                    == instance_release_id)
                            related_target = target_query.first()
                    if related_target is None:
                        raise RuleExecutionError(
                            rname,
                            f"删除条件无法读取链接目标实例: {li.target_object_id}")
                    condition_scopes = {
                        "object": target_props or {},
                        "source": target_props or {},
                        "target": related_target.properties or {},
                        "link": li.properties or {},
                        "params": params,
                    }
                    try:
                        _validate_expression_property_references(
                            condition, condition_scopes)
                        matched = bool(safe_eval(
                            condition, condition_scopes))
                    except SafeEvalError as exc:
                        raise RuleExecutionError(
                            rname, f"删除条件求值失败: {exc}") from exc
                    if matched:
                        rows.append(li)
                if not body.dry_run:
                    for li in rows:
                        pending_links.append({"link_id": li.id, "link_type_id": lt_id, "exists": False})
                        db.delete(li)
                    effects.append({"type": "delete_link",
                                    "description": f"删除链接 {lt_id} × {len(rows)}",
                                    "linkTypeId": lt_id, "oldValue": len(rows),
                                    "matchedLinkIds": [item.id for item in rows],
                                    "conditionApplied": bool(condition)})
                else:
                    matched_ids = {str(item.id) for item in rows}
                    virtual_ids = {
                        str(item.id) for item in dry_run_links}
                    dry_run_links[:] = [
                        item for item in dry_run_links
                        if str(item.id) not in matched_ids
                    ]
                    dry_run_deleted_link_ids.update(
                        matched_ids - virtual_ids)
                    effects.append({"type": "delete_link",
                                    "description": (
                                        f"删除链接 {lt_id} × {len(rows)}（模拟）"),
                                    "linkTypeId": lt_id,
                                    "oldValue": len(rows),
                                    "matchedLinkIds": [item.id for item in rows],
                                    "conditionApplied": bool(condition)})

            elif rtype == "notification":
                channel = cfg.get("channel", "internal")
                if channel not in ("internal", "in_app", "in-app"):
                    raise RuleExecutionError(
                        rname,
                        f"外部通知通道「{channel}」尚未配置可靠投递器，已拒绝伪造 delivered")
                try:
                    recipient = _resolve_recipient(
                        cfg, params, target_props, target_instance,
                        db, ontology_id,
                        virtual_links=(
                            dry_run_links if body.dry_run else None),
                        virtual_objects=(
                            {
                                item.id: item
                                for item in [
                                    *(
                                        _preview_instance_values(
                                            preview_context)
                                        if preview_context is not None
                                        and preview_context.get(
                                            "isolated", False)
                                        else []
                                    ),
                                    *dry_run_created_objects,
                                ]
                            }
                            if body.dry_run else None),
                        excluded_link_ids=(
                            dry_run_deleted_link_ids
                            if body.dry_run else None),
                        isolated_links=(
                            _preview_link_values(preview_context)
                            if preview_context is not None
                            and preview_context.get("isolated", False)
                            else None),
                    )
                except SafeEvalError as exc:
                    raise RuleExecutionError(
                        rname, f"内部通知无法解析收件人: {exc}") from exc
                if not recipient:
                    raise RuleExecutionError(rname, "内部通知无法解析收件人")
                try:
                    message = _render_template(
                        cfg.get("messageTemplate", ""), params, target_props)
                except SafeEvalError as exc:
                    raise RuleExecutionError(
                        rname, f"内部通知模板无效: {exc}") from exc
                if not message.strip():
                    raise RuleExecutionError(rname, "内部通知消息不能为空")
                channel = "internal"
                if not body.dry_run:
                    from app.models.sentinel import Notification
                    db.add(Notification(
                        ontology_id=ontology_id, channel=channel, recipient=recipient,
                        subject=cfg.get("subject") or action.display_name, body=message,
                        related_object_id=(
                            target_instance.id if target_instance
                            else body.target_instance_id),
                        action_id=action.id,
                        ontology_release_id=ontology_release_id,
                        sentinel_id=getattr(body, "sentinel_id", None),
                        action_log_id=execution_log_id,
                        status="delivered",
                    ))
                effects.append({"type": "notification", "channel": channel,
                                "recipient": recipient, "message": message,
                                "description": (
                                    f"站内通知预览（未写入）→ {recipient}"
                                    if body.dry_run
                                    else f"站内通知已写入 → {recipient}"),
                                "status": (
                                    "preview" if body.dry_run
                                    else "delivered"),
                                "sink": "internal_inbox"})

            elif rtype == "webhook":
                # External effects run only after local writes, facts and
                # derived recomputation have flushed successfully.  They still
                # run before commit so a webhook failure rolls local work back.
                deferred_webhooks.append(
                    (rule_ordinal, rule, rname, cfg))

            else:
                raise RuleExecutionError(rname or "unknown", f"不支持的动作规则类型: {rtype}")

    except RuleExecutionError as e:
        if not preview_only:
            db.rollback()
        return _fail_log(db, ontology_id, action, body, start, str(e),
                        effects=effects, actor_id=actor_id,
                        ontology_version=ontology_version,
                        ontology_release_id=ontology_release_id,
                        target_snapshot=target_snapshot,
                        suppress_log=preview_only)
    except Exception as e:  # noqa: BLE001 — 任何意外都必须留下失败日志而非 500
        if not preview_only:
            db.rollback()
        logger.exception(
            "动作规则执行出现未封装异常: ontology=%s action=%s",
            ontology_id, body.action_id)
        return _fail_log(db, ontology_id, action, body, start,
                         "动作执行出现内部错误，请检查服务端日志",
                         effects=effects, actor_id=actor_id,
                         validation_errors=["action_execution_internal_error"],
                         ontology_version=ontology_version,
                         ontology_release_id=ontology_release_id,
                         target_snapshot=target_snapshot,
                         suppress_log=preview_only)

    if preview_only:
        try:
            for _rule_ordinal, _rule, rname, cfg in deferred_webhooks:
                try:
                    preview = preview_webhook(
                        cfg, params=params,
                        object_props=target_props or {})
                except WebhookDispatchError as exc:
                    raise RuleExecutionError(
                        rname, str(exc)) from exc
                effects.append({
                    "type": "webhook",
                    "description": "调用 Webhook（模拟，未发出网络请求）",
                    **preview,
                })
            release_error = current_release_error()
            if release_error:
                raise RuleExecutionError(
                    "release_fence", release_error)
        except RuleExecutionError as exc:
            return _fail_log(
                db, ontology_id, action, body, start, str(exc),
                effects=effects, actor_id=actor_id,
                validation_errors=(
                    ["release_context_changed"]
                    if "发布节点" in str(exc) else []),
                ontology_version=ontology_version,
                ontology_release_id=ontology_release_id,
                target_snapshot=target_snapshot,
                suppress_log=True,
            )
        preview_effects = [
            {
                **dict(effect),
                "status": effect.get("status") or "preview",
                "committed": False,
            }
            for effect in effects
        ]
        return {
            "id": None,
            "actionId": action.id,
            "actionName": action.display_name,
            "objectTypeId": action.object_type_id,
            "objectInstanceId": body.target_instance_id,
            "parameters": params,
            "status": "success",
            "validationErrors": [],
            "effects": preview_effects,
            "errorMessage": None,
            "durationMs": int((time.time() - start) * 1000),
            "dryRun": True,
            "executedAt": _now().isoformat(),
            "actorId": actor_id,
            "targetSnapshot": target_snapshot,
            "idempotencyKey": None,
            "sentinelMatchStateId": _match_state_id(body),
            "ontologyVersion": ontology_version,
            "ontologyReleaseId": ontology_release_id,
            "previewOnly": True,
            "sideEffects": "none",
        }

    release_error = current_release_error()
    if release_error:
        return _fail_log(
            db, ontology_id, action, body, start, release_error,
            effects=effects,
            validation_errors=["release_context_changed"],
            actor_id=actor_id, parameters=params,
            ontology_version=ontology_version,
            ontology_release_id=ontology_release_id,
            target_snapshot=target_snapshot,
        )

    try:
        log = ActionExecutionLog(
            id=execution_log_id,
            ontology_id=ontology_id, action_id=action.id,
            action_name=action.display_name,
            object_type_id=action.object_type_id,
            object_instance_id=body.target_instance_id,
            parameters=params, status="success", validation_errors=[],
            effects=list(effects),
            duration_ms=int((time.time() - start) * 1000),
            dry_run=body.dry_run, actor_id=actor_id,
            idempotency_key=idem_key,
            sentinel_match_state_id=_match_state_id(body),
            ontology_version=ontology_version,
            ontology_release_id=ontology_release_id,
            target_snapshot=target_snapshot,
        )
        db.add(log)
        db.flush()

        # Property and derived facts were written at each ordered mutation so
        # intermediate consumers and history cannot diverge.  Link facts do
        # not affect computed-property scope and can be appended together here
        # after the success-log identity is materialized.
        if pending_links:
            for pl in pending_links:
                record_link_fact(
                    db, ontology_id=ontology_id,
                    link_instance_id=pl["link_id"],
                    link_type_id=pl["link_type_id"],
                    exists=pl["exists"], source=src,
                    actor_id=actor_id, caused_by=causal,
                    ontology_version=ontology_version,
                    ontology_release_id=ontology_release_id,
                )
        db.flush()

        # ``target_props`` is a detached execution snapshot.  Stored values may
        # have changed during the rule loop.  Reload the exact target row after
        # all local effects so every deferred external effect resolves
        # templates from the committed-to-be stored+computed view.
        if not body.dry_run and target_instance is not None:
            db.refresh(
                target_instance,
                attribute_names=["properties", "computed"],
            )
            target_props = {
                **dict(target_instance.properties or {}),
                **dict(target_instance.computed or {}),
            }

        for rule_ordinal, rule, rname, cfg in deferred_webhooks:
            if body.dry_run:
                try:
                    preview = preview_webhook(
                        cfg, params=params,
                        object_props=target_props or {})
                except WebhookDispatchError as exc:
                    raise RuleExecutionError(
                        rname, str(exc)) from exc
                effects.append({
                    "type": "webhook",
                    "description": "调用 Webhook（模拟，未发出网络请求）",
                    **preview,
                })
                continue
            try:
                delivery = dispatch_webhook(
                    cfg,
                    params=params,
                    object_props=target_props or {},
                    idempotency_key=(
                        f"formal-action:{ontology_id}:{action.id}:"
                        f"{idem_key}:{_rule_identity(rule, rule_ordinal)}"
                        if idem_key else None
                    ),
                )
            except WebhookDispatchError as exc:
                if exc.idempotency_key:
                    effects.append({
                        "type": "webhook",
                        "url": exc.safe_url,
                        "method": exc.method,
                        "attempts": exc.attempts,
                        "idempotencyKey": exc.idempotency_key,
                        "status": (
                            "delivery_uncertain"
                            if exc.delivery_uncertain else "failed"),
                        "externalDeliveryMayHaveOccurred": bool(
                            exc.delivery_uncertain),
                        "description": (
                            "Webhook 投递结果不确定，需按幂等键对账"
                            if exc.delivery_uncertain
                            else "Webhook 在发出请求前失败"),
                    })
                raise RuleExecutionError(rname, str(exc)) from exc
            effects.append({
                "type": "webhook",
                "description": (
                    f"Webhook 已调用 → {delivery['method']} "
                    f"{delivery['url']} (HTTP {delivery['statusCode']}，"
                    f"{delivery['attempts']} 次尝试)"),
                **delivery,
            })

        log.effects = list(effects)
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
        return _fail_log(
            db, ontology_id, action, body, start,
            "动作提交违反数据库完整性约束",
            effects=effects, actor_id=actor_id,
            ontology_version=ontology_version,
            ontology_release_id=ontology_release_id,
            target_snapshot=target_snapshot,
        )
    except RuleExecutionError as exc:
        db.rollback()
        return _fail_log(
            db, ontology_id, action, body, start, str(exc),
            effects=effects, actor_id=actor_id,
            ontology_version=ontology_version,
            ontology_release_id=ontology_release_id,
            target_snapshot=target_snapshot,
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "动作事实/派生/提交出现未封装异常: ontology=%s action=%s",
            ontology_id, body.action_id)
        return _fail_log(
            db, ontology_id, action, body, start,
            "动作提交出现内部错误，请检查服务端日志",
            effects=effects, actor_id=actor_id,
            validation_errors=["action_commit_internal_error"],
            ontology_version=ontology_version,
            ontology_release_id=ontology_release_id,
            target_snapshot=target_snapshot,
        )
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
        "targetSnapshot": log.target_snapshot,
        "idempotencyKey": log.idempotency_key,
        "sentinelMatchStateId": log.sentinel_match_state_id,
        "ontologyVersion": log.ontology_version,
        "ontologyReleaseId": log.ontology_release_id,
    }
