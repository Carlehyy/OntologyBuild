"""Neo4j-safe property contract for the rebuildable mapping projection."""
from __future__ import annotations

import json
from typing import Any


_NEO4J_INT64_MIN = -(1 << 63)
_NEO4J_INT64_MAX = (1 << 63) - 1


def neo4j_safe_value(value: Any) -> Any:
    """Return one value accepted by Neo4j's property-value contract.

    SQL JSON columns may contain objects or nested/heterogeneous arrays, while
    Neo4j properties only accept scalars and homogeneous scalar arrays. Keep
    queryable scalar arrays native and encode other structures as stable JSON.
    """
    if value is None or isinstance(value, (str, bool, float)):
        return value
    if isinstance(value, int):
        if _NEO4J_INT64_MIN <= value <= _NEO4J_INT64_MAX:
            return value
        return str(value)
    if isinstance(value, list):
        if not value:
            return value
        item_type = type(value[0])
        if (
            item_type in (str, bool, float)
            and all(type(item) is item_type for item in value)
        ):
            return value
        if (
            item_type is int
            and all(
                type(item) is int
                and _NEO4J_INT64_MIN <= item <= _NEO4J_INT64_MAX
                for item in value
            )
        ):
            return value
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )


def neo4j_entity_properties(
    properties: dict | None,
    *,
    entity_id: Any,
    ontology_id: Any,
) -> dict:
    """Flatten a legacy Entity envelope without corrupting graph identity.

    Business values reach this bucket precisely because their modeled names
    collide with the legacy Entity envelope. They use ``business_*`` aliases in
    this derived graph, while Formal instances retain their modeled names. The
    complete bucket is kept as JSON so alias collisions cannot lose data.
    """
    projected = dict(properties or {})
    business = projected.pop("__business_properties__", None)
    projected.pop("__mapping_ids__", None)
    if isinstance(business, dict):
        for key, value in business.items():
            target = f"business_{str(key)}"
            if target not in projected:
                projected[target] = value
        projected["__business_properties_json__"] = business
    elif business is not None:
        projected["__business_properties_json__"] = business

    # These are graph identity/scope keys, not modeled business properties.
    projected["id"] = entity_id
    projected["ontology_id"] = ontology_id
    return {
        str(key): neo4j_safe_value(value)
        for key, value in projected.items()
    }
