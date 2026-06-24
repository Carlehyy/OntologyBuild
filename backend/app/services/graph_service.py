"""
Graph Database Service - using Kuzu (embedded property graph).
Falls back to in-memory mode if Kuzu is not available.
"""

import os
import json
from typing import Optional, List, Dict, Any
from datetime import datetime

# Dynamic import - handle missing kuzu gracefully
try:
    import kuzu
    KUZU_AVAILABLE = True
except ImportError:
    KUZU_AVAILABLE = False
    print("[WARN] Kuzu not available - graph service running in memory mode")


class GraphService:
    """Service for interacting with the property graph database."""

    def __init__(self):
        self.db_path = "./data/graph"
        self._db: Optional[Any] = None
        self._conn: Optional[Any] = None
        self._initialized_domains: set = set()
        # In-memory fallback when Kuzu is not available
        self._memory_nodes: Dict[str, List[Dict]] = {}
        self._memory_edges: Dict[str, List[Dict]] = {}

    def _get_db(self):
        if not KUZU_AVAILABLE:
            return None
        if self._db is None:
            parent = os.path.dirname(self.db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._db = kuzu.Database(self.db_path)
        return self._db

    def _get_conn(self):
        if not KUZU_AVAILABLE:
            return None
        if self._conn is None:
            self._conn = kuzu.Connection(self._get_db())
        return self._conn

    def initialize_schema(self, domain_id: str):
        """Initialize graph schema for a domain."""
        if domain_id in self._initialized_domains:
            return
        self._initialized_domains.add(domain_id)

        if not KUZU_AVAILABLE:
            return

        conn = self._get_conn()
        if not conn:
            return

        table_name = f"Entity_{domain_id.replace('-', '_')}"
        try:
            conn.execute(
                f"""CREATE NODE TABLE IF NOT EXISTS {table_name} (
                    id STRING, name STRING, object_type_id STRING,
                    object_type_name STRING, properties STRING, domain_id STRING,
                    confidence DOUBLE, is_verified BOOLEAN, created_at STRING,
                    PRIMARY KEY (id)
                )"""
            )
        except Exception as e:
            print(f"[Graph] Entity table init note: {e}")

    def sync_entity(self, domain_id: str, entity_id: str, object_type_id: str,
                    object_type_name: str, name: str, properties: Dict[str, Any],
                    confidence: float = 1.0, is_verified: bool = False):
        """Create or update an entity node."""
        self.initialize_schema(domain_id)

        # Always sync to memory
        nodes = self._memory_nodes.setdefault(domain_id, [])
        # Remove existing
        nodes[:] = [n for n in nodes if n["id"] != entity_id]
        nodes.append({
            "id": entity_id, "label": name, "type": object_type_name,
            "type_id": object_type_id, "properties": properties,
            "color": "#3b82f6", "confidence": confidence,
            "is_verified": is_verified,
        })

        if not KUZU_AVAILABLE:
            return entity_id

        conn = self._get_conn()
        if not conn:
            return entity_id

        table_name = f"Entity_{domain_id.replace('-', '_')}"
        props_json = json.dumps(properties, ensure_ascii=False)
        created_at = datetime.utcnow().isoformat()

        try:
            conn.execute(f'MATCH (e:{table_name} {{id: "{entity_id}"}}) DELETE e')
        except Exception:
            pass

        try:
            conn.execute(f"""
            CREATE (e:{table_name} {{
                id: "{entity_id}", name: "{self._escape(name)}",
                object_type_id: "{object_type_id}",
                object_type_name: "{self._escape(object_type_name)}",
                properties: "{self._escape(props_json)}",
                domain_id: "{domain_id}", confidence: {confidence},
                is_verified: {str(is_verified).lower()}, created_at: "{created_at}"
            }})
            """)
        except Exception as e:
            print(f"[Graph] Entity sync error: {e}")

        return entity_id

    def delete_entity(self, domain_id: str, entity_id: str):
        """Delete entity and its relations."""
        # Memory cleanup
        nodes = self._memory_nodes.get(domain_id, [])
        self._memory_nodes[domain_id] = [n for n in nodes if n["id"] != entity_id]
        edges = self._memory_edges.get(domain_id, [])
        self._memory_edges[domain_id] = [e for e in edges if e["source"] != entity_id and e["target"] != entity_id]

        if not KUZU_AVAILABLE:
            return

        conn = self._get_conn()
        if not conn:
            return

        table_name = f"Entity_{domain_id.replace('-', '_')}"
        try:
            conn.execute(f'MATCH (e:{table_name} {{id: "{entity_id}"}})-[r]->() DELETE r')
            conn.execute(f'MATCH (e:{table_name} {{id: "{entity_id}"}})<-[r]-() DELETE r')
            conn.execute(f'MATCH (e:{table_name} {{id: "{entity_id}"}}) DELETE e')
        except Exception as e:
            print(f"[Graph] Delete error: {e}")

    def sync_relation(self, domain_id: str, relation_id: str, relation_type_id: str,
                      relation_name: str, source_id: str, target_id: str,
                      properties: Dict[str, Any] = None, confidence: float = 1.0,
                      is_verified: bool = False):
        """Create a relationship."""
        # Always sync to memory
        edges = self._memory_edges.setdefault(domain_id, [])
        edges[:] = [e for e in edges if e["id"] != relation_id]
        edges.append({
            "id": relation_id, "source": source_id, "target": target_id,
            "label": relation_name, "relation_type_id": relation_type_id,
            "properties": properties or {}, "confidence": confidence,
        })

        if not KUZU_AVAILABLE:
            return relation_id

        conn = self._get_conn()
        if not conn:
            return relation_id

        self.create_relation_table(domain_id, relation_type_id, relation_name)

        entity_table = f"Entity_{domain_id.replace('-', '_')}"
        rel_table = f"Rel_{domain_id.replace('-', '_')}_{relation_type_id.replace('-', '_')}"
        props_json = json.dumps(properties or {}, ensure_ascii=False)
        created_at = datetime.utcnow().isoformat()

        try:
            conn.execute(
                f'MATCH (s:{entity_table} {{id: "{source_id}"}})'
                f'-[r:{rel_table} {{id: "{relation_id}"}}]->'
                f'(t:{entity_table} {{id: "{target_id}"}}) DELETE r'
            )
        except Exception:
            pass

        try:
            conn.execute(f"""
            MATCH (s:{entity_table} {{id: "{source_id}"}}),
                  (t:{entity_table} {{id: "{target_id}"}})
            CREATE (s)-[r:{rel_table} {{
                id: "{relation_id}", relation_type_id: "{relation_type_id}",
                relation_name: "{self._escape(relation_name)}",
                properties: "{self._escape(props_json)}",
                confidence: {confidence}, is_verified: {str(is_verified).lower()},
                created_at: "{created_at}"
            }}]->(t)
            """)
        except Exception as e:
            print(f"[Graph] Relation sync error: {e}")

        return relation_id

    def create_relation_table(self, domain_id: str, relation_type_id: str,
                              relation_name: str, is_directed: bool = True):
        """Create relationship table."""
        if not KUZU_AVAILABLE:
            return

        conn = self._get_conn()
        if not conn:
            return

        entity_table = f"Entity_{domain_id.replace('-', '_')}"
        rel_table = f"Rel_{domain_id.replace('-', '_')}_{relation_type_id.replace('-', '_')}"

        try:
            conn.execute(
                f"""CREATE REL TABLE IF NOT EXISTS {rel_table} (
                    FROM {entity_table} TO {entity_table},
                    id STRING, relation_type_id STRING, relation_name STRING,
                    properties STRING, confidence DOUBLE, is_verified BOOLEAN,
                    created_at STRING, MANY_MANY
                )"""
            )
        except Exception as e:
            print(f"[Graph] Rel table init note: {e}")

    def delete_relation(self, domain_id: str, relation_type_id: str,
                        relation_id: str, source_id: str, target_id: str):
        """Delete a relationship."""
        edges = self._memory_edges.get(domain_id, [])
        self._memory_edges[domain_id] = [e for e in edges if e["id"] != relation_id]

        if not KUZU_AVAILABLE:
            return

        conn = self._get_conn()
        if not conn:
            return

        entity_table = f"Entity_{domain_id.replace('-', '_')}"
        rel_table = f"Rel_{domain_id.replace('-', '_')}_{relation_type_id.replace('-', '_')}"
        try:
            conn.execute(
                f'MATCH (s:{entity_table} {{id: "{source_id}"}})'
                f'-[r:{rel_table} {{id: "{relation_id}"}}]->'
                f'(t:{entity_table} {{id: "{target_id}"}}) DELETE r'
            )
        except Exception as e:
            print(f"[Graph] Rel delete error: {e}")

    def get_subgraph(self, domain_id: str, entity_ids: Optional[List[str]] = None,
                     limit: int = 500) -> Dict[str, List[Dict]]:
        """Get subgraph for visualization. Returns from memory."""
        nodes = self._memory_nodes.get(domain_id, [])
        edges = self._memory_edges.get(domain_id, [])
        return {"nodes": nodes, "edges": edges}

    def search_entities(self, domain_id: str, query: str,
                        object_type_id: Optional[str] = None,
                        limit: int = 50) -> List[Dict[str, Any]]:
        """Search entities."""
        nodes = self._memory_nodes.get(domain_id, [])
        query_lower = query.lower()
        results = []
        for node in nodes:
            if object_type_id and node.get("type_id") != object_type_id:
                continue
            if query_lower in node.get("label", "").lower():
                results.append(node)
            if len(results) >= limit:
                break
        return results

    def get_statistics(self, domain_id: str) -> Dict[str, int]:
        """Get graph statistics."""
        return {
            "entity_count": len(self._memory_nodes.get(domain_id, [])),
            "relation_count": len(self._memory_edges.get(domain_id, [])),
        }

    def execute_query(self, domain_id: str, query: str) -> List[Dict[str, Any]]:
        """Execute raw Cypher (read-only, safety checked)."""
        query_lower = query.strip().lower()
        if any(cmd in query_lower for cmd in ['create ', 'delete ', 'set ', 'remove ', 'drop ']):
            raise ValueError("Only read queries are allowed")

        if not KUZU_AVAILABLE:
            return []

        conn = self._get_conn()
        if not conn:
            return []

        try:
            result = conn.execute(query)
            results = []
            while result.has_next():
                row = result.get_next()
                row_data = {}
                for i, val in enumerate(row):
                    row_data[f"col_{i}"] = str(val)
                results.append(row_data)
            return results
        except Exception as e:
            print(f"[Graph] Query error: {e}")
            return []

    def _escape(self, s: str) -> str:
        if not s:
            return ""
        return s.replace('"', '\\"').replace('\n', ' ').replace('\r', '')


# Singleton
_graph_service: Optional[GraphService] = None


def get_graph_service() -> GraphService:
    global _graph_service
    if _graph_service is None:
        _graph_service = GraphService()
    return _graph_service
