"""Public compatibility facade for formal Action validation.

Parameter preparation remains here; definition validation is implemented by
rule-specific static handlers in ``action_definition_validation``.
"""
from __future__ import annotations

from typing import Any

from app.models.ontology_formal import ActionType
from app.ontologies.formal_modeling.action_definition_validation import (
    _ActionDefinitionValidator,
    _snapshot_rule_safe,
    action_supports_snapshot_execution,
    validate_action_definition,
)
from app.ontologies.formal_modeling.action_validation_primitives import (
    _MISSING,
    _SUPPORTED_RULE_TYPES,
    _TPL_RE,
    _definition_field,
    _definition_id,
    _definition_properties,
    _parameter_default,
    _parameter_options,
    _parameter_type_error,
    _static_sample,
    _type_error,
    _validate_expression_property_references,
)


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
