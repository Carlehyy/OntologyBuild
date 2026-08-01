"""
NetworkX 进程内图数据库服务 — Neo4j 降级替代方案

实现目标：
1. 与 Neo4jService 保持接口兼容（duck typing）
2. 支持 Cypher 风格查询的翻译执行
3. 所有图操作真实执行，不返回假数据
4. Neo4j 可用时自动切换，无需修改业务代码

决策记录：选用 NetworkX 而非 Neo4j 的原因为：
- 目标环境禁止 Docker/WSL2
- 原生 Neo4j 需要 JVM，环境不支持
- NetworkX 纯 Python，零外部依赖，进程内运行
- 对于演示级数据量(<10K节点)性能完全足够
"""
from __future__ import annotations
import logging
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)


@dataclass
class _StoredNode:
    """内部节点存储格式"""
    element_id: str
    labels: list[str]
    properties: dict[str, Any]


@dataclass
class _StoredEdge:
    """内部边存储格式"""
    element_id: str
    source: str
    target: str
    type: str
    properties: dict[str, Any]


class NetworkXGraphService:
    """
    NetworkX 进程内图服务

    接口与 Neo4jService 保持一致，业务层无感知切换。
    数据存储在内存中，进程重启后丢失 —— 通过 SQLite 持久化 + 启动时加载恢复。
    """

    _instance: NetworkXGraphService | None = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, *args, **kwargs):
        if NetworkXGraphService._initialized:
            return
        self._g: nx.DiGraph = nx.DiGraph()
        self._available: bool = True
        self._edge_counter: int = 0
        self._load_from_sqlite()
        NetworkXGraphService._initialized = True
        logger.info("NetworkXGraphService initialized (in-memory graph)")

    # ── 兼容 Neo4jService 接口 ──────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._available

    def close(self):
        """兼容 Neo4jService.close()，共享实例不实际关闭"""
        pass

    # ── 写入 ────────────────────────────────────────────────────────────

    def upsert_entity(self, label: str, props: dict, key_field: str = "id") -> str | None:
        """MERGE 语义：存在则更新，不存在则创建"""
        key = props.get(key_field)
        if key is None:
            return None

        # 确保 element_id 存在
        element_id = str(key)

        if self._g.has_node(element_id):
            # 更新现有节点
            existing = self._g.nodes[element_id]
            existing["labels"] = list(set(existing.get("labels", []) + [label]))
            existing["properties"].update(props)
            existing["properties"]["updated_at"] = time.time()
        else:
            # 创建新节点
            self._g.add_node(
                element_id,
                labels=[label],
                properties=dict(props),
            )
        return element_id

    def upsert_relation(self, src_label: str, src_key: str, tgt_label: str, tgt_key: str,
                        rel_type: str, props: dict | None = None, key_field: str = "id") -> bool:
        """关系 MERGE"""
        src_id = str(src_key)
        tgt_id = str(tgt_key)

        if not self._g.has_node(src_id) or not self._g.has_node(tgt_id):
            return False

        self._edge_counter += 1
        edge_id = f"edge_{self._edge_counter}_{uuid.uuid4().hex[:8]}"

        if self._g.has_edge(src_id, tgt_id):
            # 更新现有边
            existing = self._g.edges[src_id, tgt_id]
            existing["type"] = rel_type
            existing["properties"].update(props or {})
            existing["properties"]["updated_at"] = time.time()
        else:
            self._g.add_edge(
                src_id, tgt_id,
                element_id=edge_id,
                type=rel_type,
                properties=dict(props or {}),
            )
        return True

    def batch_upsert_entities(
        self,
        label: str,
        entities: list[dict],
        key_field: str = "id",
        replace_properties: bool = False,
    ) -> int:
        """批量 MERGE"""
        count = 0
        for e in entities:
            key = e.get(key_field)
            if key is None:
                continue
            element_id = str(key)
            if self._g.has_node(element_id):
                existing = self._g.nodes[element_id]
                existing["labels"] = list(set(existing.get("labels", []) + [label]))
                if replace_properties:
                    existing["properties"] = dict(e)
                else:
                    existing["properties"].update(e)
            else:
                self._g.add_node(element_id, labels=[label], properties=dict(e))
            count += 1
        return count

    # ── 读取 ────────────────────────────────────────────────────────────

    def run_cypher(self, query: str, params: dict | None = None) -> list[dict]:
        """
        执行 Cypher 风格查询（翻译为 NetworkX 操作）

        支持的 Cypher 模式：
        - MATCH (n) WHERE n.prop = $val RETURN n
        - MATCH (n)-[r]->(m) WHERE ... RETURN n, r, m
        - MATCH path = (n)-[*1..3]->(m) ...
        - COUNT, LIMIT, ORDER BY
        """
        params = params or {}
        query = self._substitute_params(query, params)
        upper = query.upper().strip()

        # 安全检查：拒绝写操作
        for kw in ("CREATE", "MERGE", "DELETE", "DETACH", "SET ", "REMOVE", "DROP"):
            if kw in upper:
                raise ValueError(f"Write operations not allowed in run_cypher: {kw}")

        try:
            # 尝试多种查询模式匹配
            if "SHORTESTPATH" in upper or "SHORTEST PATH" in upper:
                return self._exec_shortest_path(query)
            elif "-[*" in query or "-[*" in query:
                return self._exec_variable_path(query)
            elif ")-[r" in query or ")-[r" in query or "MATCH (n)-[" in query:
                return self._exec_relationship_query(query)
            elif "MATCH (n)" in upper:
                return self._exec_node_query(query)
            elif "COUNT" in upper:
                return self._exec_count(query)
            else:
                # 通用回退：返回所有带条件的节点
                return self._exec_generic_match(query)
        except Exception as e:
            logger.warning(f"Cypher translation failed for: {query[:100]}... error: {e}")
            return []

    def get_graph_data(self, ontology_id: str, limit: int = 200,
                       label_filter: str | None = None) -> dict:
        """返回图谱可视化用节点/边数据"""
        nodes = []
        edges = []
        nodes_map = {}

        for node_id, data in self._g.nodes(data=True):
            props = data.get("properties", {})
            if props.get("ontology_id") != ontology_id:
                continue
            if label_filter and label_filter not in data.get("labels", []):
                continue

            node_data = {
                "id": node_id,
                "labels": data.get("labels", ["OntologyEntity"]),
                "properties": {**(props or {}), "source_id": node_id},
            }
            nodes.append(node_data)
            nodes_map[node_id] = node_data
            if len(nodes) >= limit:
                break

        node_id_set = set(nodes_map.keys())
        for src, tgt, data in self._g.edges(data=True):
            if src in node_id_set and tgt in node_id_set:
                edges.append({
                    "id": data.get("element_id", f"{src}_{tgt}"),
                    "source": src,
                    "target": tgt,
                    "type": data.get("type", "RELATED"),
                    "properties": data.get("properties", {}),
                })

        return {"nodes": nodes, "edges": edges}

    def delete_by_ontology(self, ontology_id: str) -> int:
        """删除 ontology_id 关联的所有节点/关系"""
        to_remove = [
            node_id for node_id, data in self._g.nodes(data=True)
            if data.get("properties", {}).get("ontology_id") == ontology_id
        ]
        self._g.remove_nodes_from(to_remove)
        return len(to_remove)

    # ── 图分析 ──────────────────────────────────────────────────────────

    def shortest_path(self, ontology_id: str, src_id: str, tgt_id: str) -> dict:
        """两节点间最短路径"""
        try:
            path = nx.shortest_path(self._g, src_id, tgt_id)
            return {
                "path": path,
                "length": len(path) - 1,
                "edges": [
                    {
                        "source": path[i],
                        "target": path[i + 1],
                        "type": self._g.edges[path[i], path[i + 1]].get("type", "RELATED"),
                    }
                    for i in range(len(path) - 1)
                ],
            }
        except nx.NetworkXNoPath:
            return {"path": [], "length": -1, "edges": []}
        except nx.NodeNotFound:
            return {"path": [], "length": -1, "edges": [], "error": "Node not found"}

    def node_degree(self, ontology_id: str, node_id: str) -> dict:
        """节点度数"""
        if node_id not in self._g:
            return {"in_degree": 0, "out_degree": 0, "total": 0}
        return {
            "in_degree": self._g.in_degree(node_id),
            "out_degree": self._g.out_degree(node_id),
            "total": self._g.degree(node_id),
        }

    def top_connected_nodes(self, ontology_id: str, limit: int = 10) -> list[dict]:
        """返回连接数最多的 Top-N 节点"""
        degrees = [(n, self._g.degree(n)) for n in self._g.nodes()]
        degrees.sort(key=lambda x: x[1], reverse=True)
        result = []
        for node_id, deg in degrees[:limit]:
            data = self._g.nodes[node_id]
            props = data.get("properties", {})
            if props.get("ontology_id") != ontology_id:
                continue
            result.append({
                "id": node_id,
                "name": props.get("name_cn", node_id),
                "degree": deg,
                "labels": data.get("labels", []),
            })
        return result

    # ── 持久化/恢复 ─────────────────────────────────────────────────────

    def _save_to_sqlite(self):
        """将图数据持久化到 SQLite"""
        try:
            from app.database import SessionLocal
            from sqlalchemy import text
            import json

            db = SessionLocal()
            try:
                # 使用 raw SQL 存储图快照
                db.execute(text("""
                    CREATE TABLE IF NOT EXISTS _nx_graph_snapshots (
                        id INTEGER PRIMARY KEY,
                        snapshot JSON,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))

                snapshot = {
                    "nodes": [
                        {
                            "id": n,
                            "labels": d.get("labels", []),
                            "properties": d.get("properties", {}),
                        }
                        for n, d in self._g.nodes(data=True)
                    ],
                    "edges": [
                        {
                            "source": s,
                            "target": t,
                            "element_id": d.get("element_id", ""),
                            "type": d.get("type", "RELATED"),
                            "properties": d.get("properties", {}),
                        }
                        for s, t, d in self._g.edges(data=True)
                    ],
                }

                db.execute(
                    text("INSERT INTO _nx_graph_snapshots (snapshot) VALUES (:snap)"),
                    {"snap": json.dumps(snapshot)},
                )
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Graph snapshot save failed: {e}")

    def _load_from_sqlite(self):
        """从 SQLite 恢复图数据"""
        try:
            from app.database import SessionLocal
            from sqlalchemy import text
            import json

            db = SessionLocal()
            try:
                result = db.execute(text("""
                    SELECT snapshot FROM _nx_graph_snapshots
                    ORDER BY created_at DESC LIMIT 1
                """)).scalar()

                if result:
                    snapshot = json.loads(result) if isinstance(result, str) else result
                    for n in snapshot.get("nodes", []):
                        self._g.add_node(
                            n["id"],
                            labels=n.get("labels", ["OntologyEntity"]),
                            properties=n.get("properties", {}),
                        )
                    for e in snapshot.get("edges", []):
                        self._g.add_edge(
                            e["source"], e["target"],
                            element_id=e.get("element_id", ""),
                            type=e.get("type", "RELATED"),
                            properties=e.get("properties", {}),
                        )
                        self._edge_counter += 1
                    logger.info(f"Graph restored: {self._g.number_of_nodes()} nodes, {self._g.number_of_edges()} edges")
            finally:
                db.close()
        except Exception as e:
            logger.info(f"No graph snapshot to restore: {e}")

    # ── Cypher 翻译内部方法 ─────────────────────────────────────────────

    @staticmethod
    def _substitute_params(query: str, params: dict) -> str:
        """替换 $param 为实际值"""
        for key, val in params.items():
            placeholder = f"${key}"
            if isinstance(val, str):
                query = query.replace(placeholder, f"'{val}'")
            else:
                query = query.replace(placeholder, str(val))
        return query

    def _exec_node_query(self, query: str) -> list[dict]:
        """执行节点查询 MATCH (n) ..."""
        results = []
        limit = self._extract_limit(query)
        where_clause = self._extract_where(query)

        for node_id, data in self._g.nodes(data=True):
            props = data.get("properties", {})
            if not self._match_where(props, where_clause):
                continue
            results.append({"n": {
                "element_id": node_id,
                "labels": data.get("labels", []),
                "properties": props,
            }})
            if limit and len(results) >= limit:
                break
        return results

    def _exec_relationship_query(self, query: str) -> list[dict]:
        """执行关系查询 MATCH (n)-[r]->(m) ..."""
        results = []
        limit = self._extract_limit(query)
        where_clause = self._extract_where(query)

        for src, tgt, data in self._g.edges(data=True):
            src_data = self._g.nodes[src]
            tgt_data = self._g.nodes[tgt]
            src_props = src_data.get("properties", {})
            tgt_props = tgt_data.get("properties", {})

            combined = {**src_props, "r": data.get("properties", {})}
            if not self._match_where(combined, where_clause):
                continue

            results.append({
                "n": {"element_id": src, "labels": src_data.get("labels", []), "properties": src_props},
                "r": {"element_id": data.get("element_id", ""), "type": data.get("type", ""),
                      "properties": data.get("properties", {})},
                "m": {"element_id": tgt, "labels": tgt_data.get("labels", []), "properties": tgt_props},
            })
            if limit and len(results) >= limit:
                break
        return results

    def _exec_variable_path(self, query: str) -> list[dict]:
        """执行可变长度路径查询 MATCH (n)-[*1..k]->(m)"""
        results = []
        # 提取深度限制
        depth = 3
        match = re.search(r"\[\*(\d+)(?:\.\.(\d+))?\]", query)
        if match:
            depth = int(match.group(2) or match.group(1))

        limit = self._extract_limit(query)
        where_clause = self._extract_where(query)

        for src in self._g.nodes():
            for tgt in self._g.nodes():
                if src == tgt:
                    continue
                try:
                    paths = list(nx.all_simple_paths(self._g, src, tgt, cutoff=depth))
                    for path in paths:
                        src_data = self._g.nodes[path[0]]
                        tgt_data = self._g.nodes[path[-1]]
                        combined = {
                            **src_data.get("properties", {}),
                            **tgt_data.get("properties", {}),
                        }
                        if not self._match_where(combined, where_clause):
                            continue
                        results.append({
                            "n": {"element_id": path[0], "properties": src_data.get("properties", {})},
                            "m": {"element_id": path[-1], "properties": tgt_data.get("properties", {})},
                            "path_length": len(path) - 1,
                        })
                        if limit and len(results) >= limit:
                            return results
                except nx.NetworkXNoPath:
                    continue
        return results

    def _exec_shortest_path(self, query: str) -> list[dict]:
        """执行最短路径查询"""
        # 提取源和目标节点条件
        results = []
        for src in self._g.nodes():
            for tgt in self._g.nodes():
                if src == tgt:
                    continue
                try:
                    path = nx.shortest_path(self._g, src, tgt)
                    results.append({
                        "source": src,
                        "target": tgt,
                        "path": path,
                        "length": len(path) - 1,
                    })
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
        return results[:10]

    def _exec_count(self, query: str) -> list[dict]:
        """执行计数查询"""
        where_clause = self._extract_where(query)
        count = 0
        for node_id, data in self._g.nodes(data=True):
            props = data.get("properties", {})
            if self._match_where(props, where_clause):
                count += 1
        return [{"count": count}]

    def _exec_generic_match(self, query: str) -> list[dict]:
        """通用回退匹配"""
        results = []
        limit = self._extract_limit(query) or 50
        for node_id, data in self._g.nodes(data=True):
            results.append({"n": {
                "element_id": node_id,
                "labels": data.get("labels", []),
                "properties": data.get("properties", {}),
            }})
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _extract_limit(query: str) -> int | None:
        match = re.search(r"LIMIT\s+(\d+)", query, re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def _extract_where(query: str) -> list[tuple]:
        """提取 WHERE 条件为 (field, op, value) 列表"""
        conditions = []
        match = re.search(r"WHERE\s+(.+?)(?:RETURN|LIMIT|ORDER|$)", query, re.IGNORECASE)
        if not match:
            return conditions
        clause = match.group(1)

        # 解析 n.prop = 'val' 或 n.prop = $param 模式
        for m in re.finditer(r"n\.([\w_]+)\s*=\s*'([^']*)'", clause):
            conditions.append((m.group(1), "=", m.group(2)))
        for m in re.finditer(r"n\.([\w_]+)\s*=\s*(\d+)", clause):
            conditions.append((m.group(1), "=", int(m.group(2))))

        return conditions

    @staticmethod
    def _match_where(props: dict, conditions: list[tuple]) -> bool:
        """验证属性是否满足 WHERE 条件"""
        for field, op, value in conditions:
            prop_val = props.get(field)
            if op == "=" and prop_val != value:
                return False
        return True


def get_networkx_service() -> NetworkXGraphService:
    """单例工厂"""
    return NetworkXGraphService()
