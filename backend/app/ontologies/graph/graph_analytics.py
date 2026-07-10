"""高级图分析服务 — 邻居探索、最短路径、度统计

兼容 Neo4j 与 NetworkX 两种后端，业务层无感知。
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


class GraphAnalyticsService:
    """图分析操作 — 自动适配 Neo4j 或 NetworkX 后端"""

    def __init__(self, graph_svc=None, *, neo4j=None):
        """Create an analytics service over a graph backend.

        ``neo4j`` is retained as a keyword-only compatibility name.  Keeping
        dependency injection here also makes availability and query behaviour
        deterministic in callers that explicitly provide a backend.
        """
        if graph_svc is not None and neo4j is not None:
            raise TypeError("provide either graph_svc or neo4j, not both")
        if neo4j is not None:
            graph_svc = neo4j
        if graph_svc is None:
            # 优先 Neo4j，回退 NetworkX
            from app.services.v2.graph.neo4j_service import Neo4jService
            neo = Neo4jService()
            if neo.available:
                self._svc = neo
            else:
                neo.close()
                from app.services.v2.graph.networkx_service import NetworkXGraphService
                self._svc = NetworkXGraphService()
        else:
            self._svc = graph_svc

    def _status(self) -> dict:
        available = bool(self._svc.available)
        service_name = self._svc.__class__.__name__
        return {
            "graph_service": service_name,
            # Retain the legacy field while supporting the generic backend
            # status introduced with the NetworkX fallback.
            "neo4j_available": available and service_name != "NetworkXGraphService",
        }

    def get_neighbors(self, ontology_id: str, node_id: str, depth: int = 1) -> dict:
        """获取节点的 N 度邻居"""
        if not self._svc.available:
            return {"nodes": [], "edges": [], **self._status()}
        depth = max(1, min(depth, 5))
        query = f"""
        MATCH path = (n)-[r*1..{depth}]-(m)
        WHERE elementId(n) = $node_id AND n.ontology_id = $ontology_id
        RETURN n, m, relationships(path) AS rels
        LIMIT 100
        """
        try:
            results = self._svc.run_cypher(query, {"node_id": node_id, "ontology_id": ontology_id})
            # 展平节点和边
            nodes_map = {}
            edges = []
            for record in results:
                n_data = record.get("n", {})
                if n_data and "element_id" in n_data:
                    nid = n_data["element_id"]
                    nodes_map[nid] = {
                        "id": nid,
                        "labels": n_data.get("labels", []),
                        "properties": n_data.get("properties", {}),
                    }
                m_data = record.get("m", {})
                if m_data and "element_id" in m_data:
                    mid = m_data["element_id"]
                    nodes_map[mid] = {
                        "id": mid,
                        "labels": m_data.get("labels", []),
                        "properties": m_data.get("properties", {}),
                    }
                    edges.append({
                        "source": n_data.get("element_id", ""),
                        "target": mid,
                        "type": "RELATED",
                    })
            return {
                "nodes": list(nodes_map.values()),
                "edges": edges,
                **self._status(),
            }
        except Exception as e:
            logger.warning(f"get_neighbors failed: {e}")
            return {"nodes": [], "edges": [], **self._status(), "error": str(e)}

    def shortest_path(self, ontology_id: str, src_id: str, tgt_id: str) -> dict:
        """两节点间最短路径 — 优先使用 NetworkX 原生算法"""
        svc_name = self._svc.__class__.__name__
        if not self._svc.available:
            return {"path": [], "length": -1, **self._status()}

        # 如果底层是 NetworkX，直接用其原生 shortest_path
        if svc_name == "NetworkXGraphService":
            return self._svc.shortest_path(ontology_id, src_id, tgt_id)

        # Neo4j 路径
        query = """
        MATCH (s), (t)
        WHERE elementId(s) = $src AND elementId(t) = $tgt
          AND s.ontology_id = $ontology_id AND t.ontology_id = $ontology_id
        MATCH p = shortestPath((s)-[*]-(t))
        RETURN [n IN nodes(p) | {id: elementId(n), labels: labels(n), name: n.name_cn}] AS path_nodes,
               length(p) AS path_length
        """
        try:
            results = self._svc.run_cypher(query, {"src": src_id, "tgt": tgt_id, "ontology_id": ontology_id})
            if results:
                r = results[0]
                return {
                    "path": r.get("path_nodes", []),
                    "length": r.get("path_length", -1),
                    **self._status(),
                }
            return {"path": [], "length": -1, **self._status(), "message": "两节点间无路径"}
        except Exception as e:
            logger.warning(f"shortest_path failed: {e}")
            return {"path": [], "length": -1, **self._status(), "error": str(e)}

    def node_degree(self, ontology_id: str, node_id: str) -> dict:
        """查询节点的入度和出度"""
        svc_name = self._svc.__class__.__name__
        if not self._svc.available:
            return {"in_degree": 0, "out_degree": 0, **self._status()}

        if svc_name == "NetworkXGraphService":
            return self._svc.node_degree(ontology_id, node_id)

        query = """
        MATCH (n)
        WHERE elementId(n) = $node_id AND n.ontology_id = $ontology_id
        OPTIONAL MATCH (n)-[out]->()
        OPTIONAL MATCH ()-[in]->(n)
        RETURN count(DISTINCT out) AS out_degree, count(DISTINCT in) AS in_degree
        """
        try:
            results = self._svc.run_cypher(query, {"node_id": node_id, "ontology_id": ontology_id})
            if results:
                r = results[0]
                return {
                    "in_degree": r.get("in_degree", 0),
                    "out_degree": r.get("out_degree", 0),
                    **self._status(),
                }
        except Exception as e:
            logger.warning(f"node_degree failed: {e}")
        return {"in_degree": 0, "out_degree": 0, **self._status()}

    def top_connected_nodes(self, ontology_id: str, limit: int = 10) -> list[dict]:
        """返回连接数最多的 Top-N 节点"""
        svc_name = self._svc.__class__.__name__
        if not self._svc.available:
            return []

        if svc_name == "NetworkXGraphService":
            return self._svc.top_connected_nodes(ontology_id, limit)

        query = """
        MATCH (n)-[r]-()
        WHERE n.ontology_id = $ontology_id
        RETURN elementId(n) AS node_id,
               labels(n) AS labels,
               n.name_cn AS name,
               count(r) AS degree
        ORDER BY degree DESC
        LIMIT $limit
        """
        try:
            return self._svc.run_cypher(query, {"ontology_id": ontology_id, "limit": limit})
        except Exception as e:
            logger.warning(f"top_connected_nodes failed: {e}")
            return []
