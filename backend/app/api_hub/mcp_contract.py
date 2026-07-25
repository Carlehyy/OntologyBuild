"""One safe, deterministic parameter contract for API-Hub's public MCP.

HTTP publication already lets an administrator choose which values callers may
override.  MCP must follow the same principle: an Agent must never guess an
arbitrary query key or replace a platform-owned request body by accident.
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from . import executor, publication


class McpContractError(ValueError):
    """The caller did not satisfy the interface's public parameter contract."""


_PATH_PARAMETER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
_MANAGED_HEADERS = {
    "accept",
    "accept-encoding",
    "authorization",
    "connection",
    "content-length",
    "content-type",
    "cookie",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "user-agent",
    "x-api-hub-key",
}


def public_parameters(interface: dict) -> list[dict[str, Any]]:
    """Return the safe runtime parameters shown by ``list_open_interfaces``."""
    result: list[dict[str, Any]] = []
    contract = _contract_by_location(interface)
    for location in ("path", "query", "header", "body"):
        for spec in contract[location].values():
            result.append(
                {
                    "name": spec["name"],
                    "location": location,
                    "value_type": spec.get("value_type", "string"),
                    "required": bool(spec.get("required")),
                    "description": spec.get("description", ""),
                }
            )
    return result


def call_example(interface: dict) -> dict[str, Any]:
    """Return a copyable, secret-free ``call_open_interface`` argument sample.

    The UI uses this exact output instead of re-implementing the mapping rules
    in TypeScript.  It deliberately contains placeholders only: a caller sees
    the fields it may supply but never the platform's saved defaults, tokens,
    or W3 cookies.
    """
    contract = _contract_by_location(interface)
    sample: dict[str, Any] = {"interface_id": interface.get("id")}
    for location, output_key in (("path", "path"), ("query", "query"), ("header", "headers")):
        specs = contract[location]
        if specs:
            sample[output_key] = {
                spec["name"]: _example_value(spec)
                for spec in specs.values()
            }

    body_specs = contract["body"]
    if body_specs:
        body_type = str(interface.get("body_type") or "none").lower()
        if body_type == "json":
            body: dict[str, Any] = {}
            for spec in body_specs.values():
                _set_example_pointer(body, spec["name"], _example_value(spec))
            sample["body"] = body
        elif body_type in {"form", "multipart"}:
            sample["body"] = {
                spec["name"]: _example_value(spec)
                for spec in body_specs.values()
            }
        elif body_type == "raw" and len(body_specs) == 1:
            # Raw bodies have no safely addressable leaf fields.  A dynamic raw
            # contract therefore represents the whole body as one placeholder.
            sample["body"] = _example_value(next(iter(body_specs.values())))
    return sample


def request_overrides(interface: dict, args: Mapping[str, Any]) -> executor.RequestOverrides:
    """Validate MCP input and merge only approved values into the saved request."""
    contract = _contract_by_location(interface)
    path = _pairs(args.get("path"), "path")
    query = _pairs(args.get("query"), "query")
    headers = _pairs(args.get("headers"), "headers")

    path_pairs = _validate_pairs(path, contract["path"], "Path")
    _require_path_values(contract["path"], path_pairs)
    query_pairs = _validate_pairs(query, contract["query"], "Query")
    header_pairs = _validate_pairs(headers, contract["header"], "Header", lower=True)

    kwargs: dict[str, Any] = {
        "path_params": path_pairs or None,
        "query_params": query_pairs or None,
        "headers": header_pairs or None,
        "source": "mcp_open",
    }
    if "body" in args and args.get("body") is not None:
        kwargs.update(_body_override(interface, contract["body"], args.get("body")))
    return executor.RequestOverrides(**kwargs)


def _contract_by_location(interface: dict) -> dict[str, dict[str, dict[str, Any]]]:
    configured = interface.get("parameter_schema") or []
    if configured:
        contract = {location: {} for location in ("path", "query", "header", "body")}
        for raw in configured:
            if not isinstance(raw, Mapping):
                continue
            location = str(raw.get("location") or "").lower()
            name = str(raw.get("name") or "").strip()
            if location not in contract or not name:
                continue
            if (
                raw.get("sensitive")
                or not raw.get("dynamic", True)
                or publication.is_sensitive_name(name)
                or (location == "header" and name.lower() in _MANAGED_HEADERS)
            ):
                continue
            canonical = _canonical_name(interface, location, name)
            if not canonical:
                continue
            marker = canonical.lower() if location == "header" else canonical
            contract[location][marker] = {
                "name": canonical,
                "required": bool(raw.get("required")),
                "value_type": str(raw.get("value_type") or "string"),
                "description": str(raw.get("description") or ""),
            }
        if (
            str(interface.get("body_type") or "none").lower() == "raw"
            and len(contract["body"]) != 1
        ):
            # A raw payload has no addressable leaf fields.  Exposing zero or
            # multiple names would make a copyable example misleading, so keep
            # it platform-owned unless the schema deliberately names one whole
            # dynamic body value.
            contract["body"] = {}
        return contract

    return _derived_contract(interface)


def _derived_contract(interface: dict) -> dict[str, dict[str, dict[str, Any]]]:
    result = {location: {} for location in ("path", "query", "header", "body")}
    for name in _PATH_PARAMETER_RE.findall(str(interface.get("url") or "")):
        result["path"][name] = _spec(name, required=True, description="URL 路径参数")
    for item in interface.get("query_params") or []:
        name = str(item.get("key") or "").strip() if isinstance(item, Mapping) else ""
        if name and not publication.is_sensitive_name(name):
            result["query"][name] = _spec(name, description="查询参数")
    for item in interface.get("headers") or []:
        name = str(item.get("key") or "").strip() if isinstance(item, Mapping) else ""
        if name and name.lower() not in _MANAGED_HEADERS and not publication.is_sensitive_name(name):
            result["header"][name.lower()] = _spec(name, description="业务请求头")

    body_type = str(interface.get("body_type") or "none").lower()
    if body_type == "json":
        for name in publication.infer_body_keys(interface):
            result["body"][name] = _spec(name, description="JSON Body 字段")
    elif body_type in {"form", "multipart"}:
        # MCP's streamable HTTP transport does not carry arbitrary files.  Text
        # form fields remain safely callable; file fields stay platform-owned.
        for name, _value in publication.parse_saved_form(interface.get("body_content") or ""):
            if not publication.is_sensitive_name(name):
                result["body"][name] = _spec(name, description="请求 Body 字段")
    return result


def _spec(name: str, *, required: bool = False, description: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "required": required,
        "value_type": "string",
        "description": description,
    }


def _canonical_name(interface: dict, location: str, name: str) -> str:
    if location != "body" or str(interface.get("body_type") or "").lower() != "json":
        return name
    if name.startswith("/"):
        return name if name != "/" else ""
    # Agent-created contracts historically use dotted body fields.  Normalize
    # them to JSON pointers so both MCP and HTTP publication use one mapping.
    parts = [part for part in name.split(".") if part]
    return "/" + "/".join(_escape_pointer(part) for part in parts) if parts else ""


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _unescape_pointer(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _example_value(spec: Mapping[str, Any]) -> Any:
    value_type = str(spec.get("value_type") or "string").lower()
    if value_type == "boolean":
        return False
    if value_type == "integer":
        return 0
    if value_type == "number":
        return 0
    if value_type == "object":
        return {}
    if value_type == "array":
        return []
    name = str(spec.get("name") or "value").rsplit("/", 1)[-1] or "value"
    return f"<{name}>"


def _set_example_pointer(value: dict[str, Any], path: str, item: Any) -> None:
    if not path.startswith("/"):
        return
    parts = [_unescape_pointer(part) for part in path[1:].split("/")]
    if not parts or not all(parts):
        return
    current = value
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = item


def _pairs(value: Any, label: str) -> list[tuple[str, str]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        iterable = value.items()
    elif isinstance(value, list):
        iterable = []
        for item in value:
            if not isinstance(item, Mapping) or "key" not in item:
                raise McpContractError(f"{label} 参数必须是对象或 {{key,value}} 数组")
            iterable.append((item.get("key"), item.get("value", "")))
    else:
        raise McpContractError(f"{label} 参数必须是对象或 {{key,value}} 数组")
    pairs = []
    for raw_key, raw_value in iterable:
        key = str(raw_key or "").strip()
        if key:
            pairs.append((key, str(raw_value if raw_value is not None else "")))
    return pairs


def _validate_pairs(
    pairs: list[tuple[str, str]],
    allowed: dict[str, dict[str, Any]],
    label: str,
    *,
    lower: bool = False,
) -> list[tuple[str, str]]:
    output = []
    unknown = []
    for key, value in pairs:
        marker = key.lower() if lower else key
        spec = allowed.get(marker)
        if spec is None:
            unknown.append(key)
            continue
        output.append((spec["name"], value))
    if unknown:
        raise McpContractError(
            f"以下 {label} 参数未在 MCP 契约中开放：" + ", ".join(sorted(set(unknown)))
        )
    return output


def _require_path_values(
    allowed: dict[str, dict[str, Any]], pairs: list[tuple[str, str]]
) -> None:
    supplied = {key for key, _value in pairs}
    missing = [spec["name"] for spec in allowed.values() if spec.get("required") and spec["name"] not in supplied]
    if missing:
        raise McpContractError("缺少必填 Path 参数：" + ", ".join(sorted(missing)))


def _body_override(interface: dict, allowed: dict[str, dict[str, Any]], value: Any) -> dict[str, Any]:
    if not allowed:
        raise McpContractError("该接口未开放可由 MCP 覆盖的请求 Body")
    body_type = str(interface.get("body_type") or "none").lower()
    if body_type == "json":
        incoming = _json_object(value)
        incoming_paths = _json_leaf_paths(incoming)
        denied = sorted(set(incoming_paths) - set(allowed))
        if denied:
            raise McpContractError("以下 JSON Body 字段未在 MCP 契约中开放：" + ", ".join(denied))
        try:
            merged = publication.merge_caller_body(
                {**interface, "proxy_body_keys": list(allowed)},
                json.dumps(incoming, ensure_ascii=False).encode("utf-8"),
            )
        except publication.PublicationBodyError as exc:
            raise McpContractError(str(exc)) from exc
        return {"body": merged, "content_type": "application/json; charset=utf-8"}
    if body_type == "form":
        pairs = _validate_pairs(_pairs(value, "Form Body"), allowed, "Form Body")
        try:
            merged = publication.merge_caller_body(
                {**interface, "proxy_body_keys": list(allowed)},
                urlencode(pairs, doseq=True).encode("utf-8"),
            )
        except publication.PublicationBodyError as exc:
            raise McpContractError(str(exc)) from exc
        return {"body": merged, "content_type": "application/x-www-form-urlencoded"}
    if body_type == "multipart":
        pairs = _validate_pairs(_pairs(value, "Multipart Body"), allowed, "Multipart Body")
        replacements = {key: item for key, item in pairs}
        merged = [item for item in publication.parse_saved_form(interface.get("body_content") or "") if item[0] not in replacements]
        merged.extend(pairs)
        return {"multipart_fields": merged, "files": []}
    if body_type == "raw":
        if len(allowed) != 1:
            raise McpContractError("Raw Body 必须且只能声明一个可动态覆盖的字段")
        if not isinstance(value, str):
            raise McpContractError("Raw Body 必须是字符串")
        return {"body": value}
    raise McpContractError("该接口未配置可调用的请求 Body")


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise McpContractError("JSON Body 必须是合法 JSON 对象") from exc
    if not isinstance(value, Mapping):
        raise McpContractError("JSON Body 必须是对象")
    return copy.deepcopy(dict(value))


def _json_leaf_paths(value: Mapping[str, Any], prefix: str = "") -> list[str]:
    result = []
    for raw_key, item in value.items():
        key = _escape_pointer(str(raw_key))
        path = f"{prefix}/{key}"
        if isinstance(item, Mapping) and item:
            result.extend(_json_leaf_paths(item, path))
        else:
            result.append(path)
    return result
