from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.relation import Relation
from app.ontologies.graph import router as graph_router


def test_legacy_graph_returns_503_when_neo4j_is_unavailable():
    neo4j = MagicMock(available=False)

    with patch(
        "app.services.v2.graph.neo4j_service.Neo4jService",
        return_value=neo4j,
    ), pytest.raises(HTTPException) as exc_info:
        graph_router._try_neo4j("ont-1", 100)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "neo4j_unavailable"
    neo4j.close.assert_called_once()


def test_legacy_graph_returns_503_when_neo4j_query_fails():
    neo4j = MagicMock(available=True)
    neo4j.get_graph_data.side_effect = RuntimeError("driver failure")

    with patch(
        "app.services.v2.graph.neo4j_service.Neo4jService",
        return_value=neo4j,
    ), pytest.raises(HTTPException) as exc_info:
        graph_router._try_neo4j("ont-1", 100)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "neo4j_operation_failed"
    neo4j.close.assert_called_once()


def test_legacy_empty_neo4j_graph_does_not_fall_back_to_sql_or_formal():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        name="Empty ontology",
    )

    with patch.object(graph_router, "_require_projection_ready"), patch.object(
        graph_router,
        "_try_neo4j",
        return_value=([], []),
    ):
        result = graph_router.get_graph("ont-empty", db=db)

    assert result == {
        "data": {
            "nodes": [],
            "edges": [],
            "meta": {
                "ontology_id": "ont-empty",
                "name": "Empty ontology",
                "entity_count": 0,
                "relation_count": 0,
                "source": "neo4j",
            },
        },
    }


def test_legacy_graph_router_enforces_ontology_and_write_guards():
    from app.ontologies.access import (
        legacy_ontology_write_guard,
        ontology_access_guard,
    )

    dependency_calls = {
        dependency.dependency for dependency in graph_router.router.dependencies
    }

    assert ontology_access_guard in dependency_calls
    assert legacy_ontology_write_guard in dependency_calls


def test_relation_create_rejects_endpoint_from_another_ontology(
    db,
    admin_user,
):
    primary = OntologyProject(
        name="Primary relation ontology",
        domain="test",
        created_by=admin_user.id,
    )
    other = OntologyProject(
        name="Other relation ontology",
        domain="test",
        created_by=admin_user.id,
    )
    db.add_all([primary, other])
    db.flush()
    source = Entity(
        ontology_id=primary.id,
        name_cn="source",
        type="Object",
    )
    foreign_target = Entity(
        ontology_id=other.id,
        name_cn="foreign target",
        type="Object",
    )
    db.add_all([source, foreign_target])
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        graph_router.create_relation(
            primary.id,
            {
                "source_entity": source.id,
                "target_entity": foreign_target.id,
                "type": "INVALID_CROSS_ONTOLOGY",
            },
            db=db,
            _=admin_user,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == (
        "relation_endpoint_not_in_ontology"
    )
    assert foreign_target.id in exc_info.value.detail["missing_entity_ids"]
    assert db.query(Relation).filter(
        Relation.ontology_id == primary.id,
    ).count() == 0
