"""GraphAnalyticsService 单元测试（Neo4j Mock）"""
import pytest
from unittest.mock import MagicMock, patch
from app.services.v2.graph.graph_analytics import GraphAnalyticsService


def make_analytics(available=False):
    """创建使用 Mock Neo4j 的分析服务"""
    mock_neo4j = MagicMock()
    mock_neo4j.available = available
    return GraphAnalyticsService(neo4j=mock_neo4j), mock_neo4j


def test_get_neighbors_unavailable():
    svc, _ = make_analytics(available=False)
    with pytest.raises(RuntimeError, match="neo4j_unavailable"):
        svc.get_neighbors("ont-1", "node-1", depth=1)


def test_shortest_path_unavailable():
    svc, _ = make_analytics(available=False)
    with pytest.raises(RuntimeError, match="neo4j_unavailable"):
        svc.shortest_path("ont-1", "src-1", "tgt-1")


def test_node_degree_unavailable():
    svc, _ = make_analytics(available=False)
    with pytest.raises(RuntimeError, match="neo4j_unavailable"):
        svc.node_degree("ont-1", "node-1")


def test_top_connected_nodes_unavailable():
    svc, _ = make_analytics(available=False)
    with pytest.raises(RuntimeError, match="neo4j_unavailable"):
        svc.top_connected_nodes("ont-1")


def test_runtime_construction_keeps_unavailable_neo4j_without_networkx_fallback():
    mock_neo4j = MagicMock(available=False)
    with patch(
        "app.services.v2.graph.neo4j_service.Neo4jService",
        return_value=mock_neo4j,
    ):
        svc = GraphAnalyticsService()

    assert svc._svc is mock_neo4j
    with pytest.raises(RuntimeError, match="neo4j_unavailable"):
        svc.top_connected_nodes("ont-1")


def test_shortest_path_no_result():
    svc, mock_neo4j = make_analytics(available=True)
    mock_neo4j.run_cypher.return_value = []
    result = svc.shortest_path("ont-1", "a", "b")
    assert result["length"] == -1
    assert "message" in result


def test_top_connected_nodes_with_result():
    svc, mock_neo4j = make_analytics(available=True)
    mock_neo4j.run_cypher.return_value = [
        {"node_id": "n1", "name": "Alice", "degree": 5},
        {"node_id": "n2", "name": "Bob",   "degree": 3},
    ]
    result = svc.top_connected_nodes("ont-1", limit=2)
    assert len(result) == 2
    assert result[0]["degree"] == 5


def test_node_degree_with_result():
    svc, mock_neo4j = make_analytics(available=True)
    mock_neo4j.run_cypher.return_value = [{"in_degree": 3, "out_degree": 7}]
    result = svc.node_degree("ont-1", "node-1")
    assert result["in_degree"] == 3
    assert result["out_degree"] == 7


def test_query_error_is_not_converted_to_an_empty_result():
    svc, mock_neo4j = make_analytics(available=True)
    mock_neo4j.run_cypher.side_effect = RuntimeError("driver failure")

    with pytest.raises(RuntimeError, match="driver failure"):
        svc.top_connected_nodes("ont-1")


def test_close_closes_injected_neo4j_service():
    svc, mock_neo4j = make_analytics(available=True)
    svc.close()
    mock_neo4j.close.assert_called_once()
