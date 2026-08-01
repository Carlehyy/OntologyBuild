"""Contracts for rebuildable graph projection serialization."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.ontologies.mappings.neo4j_projection_contract import (
    neo4j_entity_properties,
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


def test_incremental_neo4j_write_uses_the_same_safe_projection_boundary():
    neo = MagicMock(available=True)
    neo.batch_upsert_entities.return_value = 1
    entity = {
        "id": "stable-id",
        "ontology_id": "ontology-1",
        "name": "runtime name",
        "__mapping_ids__": ["mapping-1"],
        "__business_properties__": {"id": "ORDER-1", "name": "Order 1"},
    }

    with patch(
        "app.services.v2.graph.neo4j_service.Neo4jService",
        return_value=neo,
    ):
        count = ProjectionAdapterMixin()._write_neo4j("Order", [entity])

    assert count == 1
    rows = neo.batch_upsert_entities.call_args.args[1]
    assert rows == [{
        "id": "stable-id",
        "ontology_id": "ontology-1",
        "name": "runtime name",
        "business_id": "ORDER-1",
        "business_name": "Order 1",
        "__business_properties_json__": '{"id":"ORDER-1","name":"Order 1"}',
    }]
    assert neo.batch_upsert_entities.call_args.kwargs == {
        "replace_properties": True,
    }
    neo.close.assert_called_once_with()


def test_full_neo4j_rebuild_sanitizes_entities_and_relation_properties():
    entity = SimpleNamespace(
        id="stable-id",
        ontology_id="ontology-1",
        type="Order",
        properties={
            "source_id": "stable-id",
            "__business_properties__": {"id": "ORDER-1"},
            "nested": {"region": "华东"},
        },
    )
    relation = SimpleNamespace(
        id="relation-1",
        ontology_id="ontology-1",
        source_entity="stable-id",
        target_entity="stable-id",
        type="SELF",
        confidence=1.0,
        properties={"evidence": {"source": "manual"}},
    )

    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *_args):
            return self

        def all(self):
            return self.rows

    class _DB:
        def query(self, model):
            return _Query([entity] if model.__tablename__ == "entities" else [relation])

    neo = MagicMock(available=True)
    service = ProjectionAdapterMixin()
    service._db = _DB()
    with patch(
        "app.services.v2.graph.neo4j_service.Neo4jService",
        return_value=neo,
    ):
        assert service._rebuild_neo4j_projection("ontology-1") is True

    node = neo.batch_upsert_entities.call_args.args[1][0]
    assert node["id"] == "stable-id"
    assert node["business_id"] == "ORDER-1"
    assert node["nested"] == '{"region":"华东"}'
    assert neo.batch_upsert_entities.call_args.kwargs == {
        "replace_properties": True,
    }
    relation_props = neo.upsert_relation.call_args.kwargs["props"]
    assert relation_props["evidence"] == '{"source":"manual"}'
    assert relation_props["id"] == "relation-1"
    neo.close.assert_called_once_with()
