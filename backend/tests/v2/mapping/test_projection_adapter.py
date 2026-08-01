"""Contracts for rebuildable graph projection serialization."""

import json
from unittest.mock import patch

from app.ontologies.mappings.neo4j_projection_contract import (
    neo4j_entity_properties,
    neo4j_projection_properties,
    neo4j_safe_value,
)
from app.ontologies.mappings.projection_adapter import ProjectionAdapterMixin


def test_neo4j_projection_preserves_graph_identity_and_business_collisions():
    source = {
        "id": "runtime-id-from-envelope",
        "ontology_id": "runtime-scope-from-envelope",
        "name": "runtime display",
        "business_id": "pre-existing-alias",
        "__mapping_ids__": ["mapping-owner"],
        "tags": ["priority", "active"],
        "details": {"region": "华东", "level": 2},
        "__business_properties__": {
            "id": "ORDER-1",
            "ontology_id": "business-scope",
            "name": "业务订单一号",
        },
    }

    projected = neo4j_entity_properties(
        source,
        entity_id="stable-entity-id",
        ontology_id="stable-ontology-id",
    )

    assert projected["id"] == "stable-entity-id"
    assert projected["ontology_id"] == "stable-ontology-id"
    assert projected["business_id"] == "pre-existing-alias"
    assert projected["business_ontology_id"] == "business-scope"
    assert projected["name"] == "runtime display"
    assert projected["business_name"] == "业务订单一号"
    assert projected["tags"] == ["priority", "active"]
    assert projected["details"] == '{"level":2,"region":"华东"}'
    assert "__mapping_ids__" not in projected
    assert json.loads(projected["__business_properties_json__"]) == {
        "id": "ORDER-1",
        "ontology_id": "business-scope",
        "name": "业务订单一号",
    }
    assert source["id"] == "runtime-id-from-envelope"
    assert isinstance(source["__business_properties__"], dict)


def test_neo4j_projection_encodes_unsupported_array_shapes_as_json():
    int64_min = -(1 << 63)
    int64_max = (1 << 63) - 1

    assert neo4j_safe_value([]) == []
    assert neo4j_safe_value([1, 2]) == [1, 2]
    assert neo4j_safe_value([int64_min, int64_max]) == [int64_min, int64_max]
    assert neo4j_safe_value([0, int64_max + 1]) == f'[0,{int64_max + 1}]'
    assert neo4j_safe_value([int64_min - 1, 0]) == f'[{int64_min - 1},0]'
    assert neo4j_safe_value([1, "2"]) == '[1,"2"]'
    assert neo4j_safe_value([{"id": "nested"}]) == '[{"id":"nested"}]'


def test_neo4j_projection_stringifies_integers_outside_int64_range():
    int64_min = -(1 << 63)
    int64_max = (1 << 63) - 1

    assert neo4j_safe_value(int64_max) == int64_max
    assert neo4j_safe_value(int64_max + 1) == str(int64_max + 1)
    assert neo4j_safe_value(int64_min) == int64_min
    assert neo4j_safe_value(int64_min - 1) == str(int64_min - 1)


def test_full_rebuild_can_defer_reversible_value_encoding_to_neo4j_service():
    projected = neo4j_entity_properties(
        {
            "nested": {"region": "华东"},
            "__mapping_ids__": ["mapping-1"],
            "__business_properties__": {
                "id": "ORDER-1",
                "name": "Order 1",
            },
        },
        entity_id="stable-id",
        ontology_id="ontology-1",
        encode_values=False,
    )

    assert projected == {
        "id": "stable-id",
        "ontology_id": "ontology-1",
        "nested": {"region": "华东"},
        "business_id": "ORDER-1",
        "business_name": "Order 1",
        "__business_properties_json__": {
            "id": "ORDER-1",
            "name": "Order 1",
        },
    }


def test_projection_envelope_preserves_reserved_business_aliases():
    business = {
        "id": "ORDER-001",
        "ontology_id": "business-scope",
        "updated_at": "business-timestamp",
        "name": "Order one",
    }
    projected = neo4j_projection_properties(
        business,
        {
            "id": "formal-instance-id",
            "ontology_id": "ontology-1",
            "name": "Display name",
        },
    )

    assert projected == {
        "id": "formal-instance-id",
        "ontology_id": "ontology-1",
        "name": "Display name",
        "business_id": "ORDER-001",
        "business_ontology_id": "business-scope",
        "business_updated_at": "business-timestamp",
        "business_name": "Order one",
        "__business_properties_json__": business,
    }


def test_projection_alias_collision_keeps_complete_business_snapshot():
    business = {
        "id": "ORDER-001",
        "business_id": "modeled-business-id",
    }
    projected = neo4j_projection_properties(
        business,
        {"id": "formal-instance-id"},
    )
    merged = {"business_id": "stale-legacy-id", **projected}

    assert merged["business_id"] == "modeled-business-id"
    assert merged["__business_properties_json__"] == business
    assert merged["__business_properties_json__"]["id"] == "ORDER-001"


def test_projection_adapter_delegates_to_authoritative_full_rebuild():
    db = object()
    service = ProjectionAdapterMixin()
    service._db = db
    with patch(
        "app.ontologies.mappings.projection_adapter.rebuild_neo4j_projection",
        return_value=True,
    ) as rebuild:
        assert service._rebuild_neo4j_projection("ontology-1") is True

    rebuild.assert_called_once_with(db, "ontology-1")
