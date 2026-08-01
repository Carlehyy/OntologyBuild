"""将 v1 LLM 提取结果写入 Neo4j 的历史兼容桥接器。"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


class LegacyExtractionBridge:
    def __init__(self):
        self._neo4j = None
        self._init_neo4j()

    def _init_neo4j(self):
        from app.services.v2.graph.neo4j_service import Neo4jService
        self._neo4j = Neo4jService()

    def sync_to_neo4j(self, ontology_id: str, entities: list[dict], relations: list[dict]) -> None:
        if not self._neo4j or not self._neo4j.available:
            raise RuntimeError("neo4j_unavailable")

        # 实体 MERGE
        for entity in entities:
            label = entity.get("type", "Entity")
            props = {
                "id": entity.get("id", ""),
                "name_cn": entity.get("name_cn", ""),
                "name_en": entity.get("name_en", ""),
                "description": entity.get("description", ""),
                "confidence": entity.get("confidence", 0.0),
                "ontology_id": ontology_id,
            }
            synced = self._neo4j.upsert_entity(label, props, key_field="id")
            if synced is None:
                raise RuntimeError("neo4j_entity_projection_incomplete")

        # 关系 MERGE
        for rel in relations:
            synced = self._neo4j.upsert_relation(
                src_label=rel.get("source_type", "Entity"),
                src_key=rel.get("source", ""),
                tgt_label=rel.get("target_type", "Entity"),
                tgt_key=rel.get("target", ""),
                rel_type=rel.get("type", "RELATES_TO"),
                props={"confidence": rel.get("confidence", 0.0), "ontology_id": ontology_id},
            )
            if not synced:
                raise RuntimeError("neo4j_relation_projection_incomplete")

        logger.info(f"[bridge] Neo4j sync: ontology={ontology_id}, {len(entities)} entities, {len(relations)} relations")

    def sync_all(self, ontology_id: str, entities: list[dict], relations: list[dict]) -> None:
        self.sync_to_neo4j(ontology_id, entities, relations)
