"""Neo4j-safe property contract for the rebuildable mapping projection."""
from __future__ import annotations

import json
from typing import Any


_NEO4J_INT64_MIN = -(1 << 63)
_NEO4J_INT64_MAX = (1 << 63) - 1


def neo4j_projection_properties(
    properties: dict | None,
    envelope: dict,
    *,
    reserved_keys: tuple[str, ...] = ("updated_at",),
) -> dict:
    """Overlay graph metadata without discarding same-named business values."""
    source = {str(key): value for key, value in (properties or {}).items()}
    projected = dict(source)
    normalized_envelope = {str(key): value for key, value in envelope.items()}
    collided = False
    for key in (*normalized_envelope, *reserved_keys):
        if key in projected:
            collided = True
            value = projected.pop(key)
            alias = f"business_{key}"
            projected.setdefault(alias, value)
    if collided:
        projected.setdefault("__business_properties_json__", source)
    projected.update(normalized_envelope)
    return projected


def neo4j_safe_value(value: Any) -> Any:
    """Return a Neo4j value, JSON-encoding unsupported SQL JSON shapes."""
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
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def neo4j_entity_properties(
    properties: dict | None,
    *,
    entity_id: Any,
    ontology_id: Any,
    updated_at: Any = None,
    encode_values: bool = True,
) -> dict:
    """Flatten a legacy Entity envelope without corrupting graph identity.

    Reserved business values use aliases; the complete bucket remains as JSON.
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

    envelope = {"id": entity_id, "ontology_id": ontology_id}
    if updated_at is not None:
        envelope["updated_at"] = updated_at
    normalized = neo4j_projection_properties(projected, envelope)
    if not encode_values:
        # The canonical full rebuild delegates reversible property encoding to
        # Neo4jService, so graph reads can recover nested JSON values.  Keep the
        # legacy encoded form as the default for external compatibility users.
        return normalized
    return {key: neo4j_safe_value(value)
            for key, value in normalized.items()}
