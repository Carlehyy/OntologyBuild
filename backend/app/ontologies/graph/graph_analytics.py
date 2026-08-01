"""基于 Neo4j 的高级图分析服务 — 邻居探索、最短路径、度统计。"""
from __future__ import annotations


class GraphAnalyticsService:
    """Neo4j 图分析操作。"""

    def __init__(self, graph_svc=None, *, neo4j=None):
        """Create an analytics service over a graph backend.

        ``neo4j`` is retained as a keyword-only compatibility name for tests
        and existing injected callers. Runtime construction always uses Neo4j.
        """
        if graph_svc is not None and neo4j is not None:
            raise TypeError("provide either graph_svc or neo4j, not both")
        if neo4j is not None:
            graph_svc = neo4j
        if graph_svc is None:
            from app.services.v2.graph.neo4j_service import Neo4jService
            self._svc = Neo4jService()
        else:
            self._svc = graph_svc

    def _status(self) -> dict:
        return {
            "graph_service": "Neo4jService",
            "neo4j_available": bool(self._svc.available),
        }

    def _require_available(self) -> None:
        if not self._svc.available:
            raise RuntimeError("neo4j_unavailable")

    def close(self) -> None:
        self._svc.close()

    def get_neighbors(self, ontology_id: str, node_id: str, depth: int = 1) -> dict:
        """获取节点的 N 度邻居"""
        self._require_available()
        depth = max(1, min(depth, 5))
        query = f"""
        MATCH path = (n)-[r*1..{depth}]-(m)
        WHERE n.id = $node_id AND n.ontology_id = $ontology_id
        RETURN {{id: n.id, labels: labels(n), properties: properties(n)}} AS n,
               {{id: m.id, labels: labels(m), properties: properties(m)}} AS m,
               [rel IN relationships(path) | {{
                   id: rel.id,
                   source: startNode(rel).id,
                   target: endNode(rel).id,
                   type: coalesce(rel.semantic_type, type(rel))
               }}] AS rels
        LIMIT 100
        """
        results = self._svc.run_cypher(
            query,
            {"node_id": node_id, "ontology_id": ontology_id},
        )
        # 展平节点和边
        nodes_map = {}
        edges = []
        for record in results:
            n_data = record.get("n", {})
            if n_data and n_data.get("id"):
                nid = str(n_data["id"])
                nodes_map[nid] = {
                    "id": nid,
                    "labels": n_data.get("labels", []),
                    "properties": n_data.get("properties", {}),
                }
            m_data = record.get("m", {})
            if m_data and m_data.get("id"):
                mid = str(m_data["id"])
                nodes_map[mid] = {
                    "id": mid,
                    "labels": m_data.get("labels", []),
                    "properties": m_data.get("properties", {}),
                }
            for relation in record.get("rels") or []:
                if relation and relation.get("source") and relation.get("target"):
                    edges.append(dict(relation))
        return {
            "nodes": list(nodes_map.values()),
            "edges": edges,
            **self._status(),
        }

    def shortest_path(self, ontology_id: str, src_id: str, tgt_id: str) -> dict:
        """查询两节点间的 Neo4j 最短路径。"""
        self._require_available()
        query = """
        MATCH (s), (t)
        WHERE s.id = $src AND t.id = $tgt
          AND s.ontology_id = $ontology_id AND t.ontology_id = $ontology_id
        MATCH p = shortestPath((s)-[*]-(t))
        RETURN [n IN nodes(p) | {id: n.id, labels: labels(n), name: n.name_cn}] AS path_nodes,
               length(p) AS path_length
        """
        results = self._svc.run_cypher(
            query,
            {"src": src_id, "tgt": tgt_id, "ontology_id": ontology_id},
        )
        if results:
            r = results[0]
            return {
                "path": r.get("path_nodes", []),
                "length": r.get("path_length", -1),
                **self._status(),
            }
        return {
            "path": [],
            "length": -1,
            **self._status(),
            "message": "两节点间无路径",
        }

    def node_degree(self, ontology_id: str, node_id: str) -> dict:
        """查询节点的入度和出度"""
        self._require_available()
        query = """
        MATCH (n)
        WHERE n.id = $node_id AND n.ontology_id = $ontology_id
        OPTIONAL MATCH (n)-[out]->()
        OPTIONAL MATCH ()-[in]->(n)
        RETURN count(DISTINCT out) AS out_degree, count(DISTINCT in) AS in_degree
        """
        results = self._svc.run_cypher(
            query,
            {"node_id": node_id, "ontology_id": ontology_id},
        )
        if results:
            r = results[0]
            return {
                "in_degree": r.get("in_degree", 0),
                "out_degree": r.get("out_degree", 0),
                **self._status(),
            }
        return {"in_degree": 0, "out_degree": 0, **self._status()}

    def top_connected_nodes(self, ontology_id: str, limit: int = 10) -> list[dict]:
        """返回连接数最多的 Top-N 节点"""
        self._require_available()
        query = """
        MATCH (n)-[r]-()
        WHERE n.ontology_id = $ontology_id
        RETURN n.id AS node_id,
               labels(n) AS labels,
               n.name_cn AS name,
               count(r) AS degree
        ORDER BY degree DESC
        LIMIT $limit
        """
        return self._svc.run_cypher(
            query,
            {"ontology_id": ontology_id, "limit": limit},
        )
