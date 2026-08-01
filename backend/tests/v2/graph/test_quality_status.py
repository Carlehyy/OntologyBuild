from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.entity import Entity
from app.models.relation import Relation
from app.routers.v2 import graph as graph_router


def _ready_projection_db():
    db = MagicMock()
    project_query = MagicMock()
    project_query.filter.return_value.first.return_value = SimpleNamespace(
        projection_status="ready",
        projection_error=None,
    )
    mapping_query = MagicMock()
    mapping_query.filter.return_value.count.return_value = 0
    db.query.side_effect = [project_query, mapping_query]
    return db


def test_graph_quality_reports_isolated_and_duplicate_nodes(db):
    ontology_id = "ont-graph-quality"
    db.add_all([
        Entity(id="e1", ontology_id=ontology_id, name_cn="供应商A", name_en="Supplier", type="Supplier", properties={}),
        Entity(id="e2", ontology_id=ontology_id, name_cn="供应商A", name_en="Supplier", type="Supplier", properties={}),
        Entity(id="e3", ontology_id=ontology_id, name_cn="订单1", name_en="Order", type="Order", properties={}),
    ])
    db.add(Relation(
        id="r1",
        ontology_id=ontology_id,
        source_entity="e3",
        target_entity="e1",
        type="HAS_SUPPLIER",
        properties={},
    ))
    db.commit()

    with patch.object(graph_router, "SessionLocal", return_value=db):
        result = graph_router.graph_quality(ontology_id)

    assert result["node_count"] == 3
    assert result["edge_count"] == 1
    assert result["isolated_node_count"] == 1
    assert result["duplicate_display_name_count"] == 2
    assert result["object_type_counts"]["Supplier"] == 2
    assert result["relation_type_counts"]["HAS_SUPPLIER"] == 1
    assert result["quality_score"] < 1


def test_integration_status_reports_required_neo4j_only():
    fake_neo = MagicMock()
    fake_neo.available = True

    with patch.object(graph_router, "get_neo4j", return_value=fake_neo):
        result = graph_router.integration_status("ont-1", db=_ready_projection_db())

    assert result["neo4j"]["available"] is True
    assert result["graph_service"] == {
        "type": "Neo4jService",
        "available": True,
        "fallback": False,
    }
    assert "chroma" not in result
    fake_neo.close.assert_called_once()


def test_integration_status_returns_503_when_neo4j_unavailable():
    fake_neo = MagicMock(available=False)

    with patch.object(graph_router, "get_neo4j", return_value=fake_neo), \
         pytest.raises(HTTPException) as exc_info:
        graph_router.integration_status("ont-1", db=_ready_projection_db())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "neo4j_unavailable"
    fake_neo.close.assert_called_once()


def test_projection_not_ready_returns_503_before_graph_access():
    db = MagicMock()
    project_query = MagicMock()
    project_query.filter.return_value.first.return_value = SimpleNamespace(
        projection_status="ready",
        projection_error=None,
    )
    mapping_query = MagicMock()
    mapping_query.filter.return_value.count.return_value = 2
    db.query.side_effect = [project_query, mapping_query]

    with pytest.raises(HTTPException) as exc_info:
        graph_router.get_graph("ont-pending", db=db)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "ontology_projection_not_ready",
        "message": "本体图投影尚未就绪，请先完成或修复投影对账",
        "ontology_id": "ont-pending",
        "projection_status": "ready",
        "projection_error": None,
        "pending_mapping_count": 2,
    }


def test_empty_neo4j_graph_remains_empty_without_sql_fallback():
    fake_neo = MagicMock(available=True)
    fake_neo.get_graph_data.return_value = {"nodes": [], "edges": []}

    with patch.object(graph_router, "get_graph_service", return_value=fake_neo):
        result = graph_router.get_graph("ont-empty", db=_ready_projection_db())

    assert result == {
        "nodes": [],
        "edges": [],
        "graph_service": "Neo4jService",
    }
    fake_neo.close.assert_called_once()


def test_graph_query_failure_returns_503_without_sql_fallback():
    fake_neo = MagicMock(available=True)
    fake_neo.get_graph_data.side_effect = RuntimeError("driver failure")

    with patch.object(graph_router, "get_graph_service", return_value=fake_neo), \
         pytest.raises(HTTPException) as exc_info:
        graph_router.get_graph("ont-failed", db=_ready_projection_db())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "neo4j_operation_failed"
    fake_neo.close.assert_called_once()


def test_nl_query_empty_result_has_no_entity_search_fallback():
    fake_neo = MagicMock(available=True)
    fake_neo.run_cypher.return_value = []
    plan = SimpleNamespace(
        cypher="MATCH (n) WHERE n.ontology_id = $ontology_id RETURN n",
        explanation="test plan",
        confidence=0.9,
    )

    with patch(
        "app.services.v2.graph.nl2cypher.NL2CypherService.translate",
        return_value=plan,
    ), patch.object(graph_router, "get_graph_service", return_value=fake_neo):
        result = graph_router.nl_query(
            "ont-empty",
            graph_router.NLQueryRequest(question="没有结果"),
            db=_ready_projection_db(),
        )

    assert result["results"] == []
    assert "fallback" not in result
    assert result["graph_service"] == "Neo4jService"
