"""Adapters for rebuildable Neo4j and Chroma query projections."""
from __future__ import annotations

import logging

from app.ontologies.mappings.neo4j_projection_contract import (
    neo4j_entity_properties,
    neo4j_safe_value,
)

logger = logging.getLogger(__name__)


class ProjectionAdapterMixin:
    """External projection operations kept patchable on MappingService."""

    def _write_neo4j(self, entity_class: str, entities: list[dict]) -> int:
        try:
            from app.services.v2.graph.neo4j_service import Neo4jService
            neo = Neo4jService()
            if neo.available:
                safe_entities = [
                    neo4j_entity_properties(
                        entity,
                        entity_id=entity.get("id"),
                        ontology_id=entity.get("ontology_id"),
                    )
                    for entity in entities
                ]
                count = neo.batch_upsert_entities(
                    entity_class,
                    safe_entities,
                    replace_properties=True,
                )
                neo.close()
                return count
        except Exception as e:
            logger.error(f"Neo4j 写入失败: {e}")
        return 0

    def _delete_neo4j_entities(self, ontology_id: str, entity_ids: list[str]) -> int:
        if not entity_ids:
            return 0
        try:
            from app.services.v2.graph.neo4j_service import Neo4jService
            neo = Neo4jService()
            if neo.available:
                count = neo.batch_delete_entities(ontology_id, entity_ids)
                neo.close()
                return count
        except Exception as e:
            # Relational/Formal current state remains authoritative and has
            # already committed.  Health/repair tooling can rebuild this derived
            # projection; never roll back truth because a cache is unavailable.
            logger.error("Neo4j stale-node reconciliation failed: %s", e)
        return 0

    def _rebuild_neo4j_projection(self, ontology_id: str) -> bool:
        """Rebuild the derived graph after relation reconciliation.

        Neo4j cannot join the SQL transaction.  Rebuild-after-commit gives it a
        deterministic repair path and removes stale relationships instead of
        accumulating them forever.
        """
        from app.models.entity import Entity
        from app.models.relation import Relation
        neo = None
        try:
            from app.services.v2.graph.neo4j_service import Neo4jService
            neo = Neo4jService()
            if not neo.available:
                return False
            neo.delete_by_ontology(ontology_id)
            entities = self._db.query(Entity).filter(
                Entity.ontology_id == ontology_id).all()
            by_type: dict[str, list[dict]] = {}
            for entity in entities:
                props = neo4j_entity_properties(
                    entity.properties,
                    entity_id=entity.id,
                    ontology_id=ontology_id,
                )
                by_type.setdefault(entity.type or "Object", []).append(props)
            for label, rows in by_type.items():
                neo.batch_upsert_entities(
                    label,
                    rows,
                    replace_properties=True,
                )
            entity_type = {entity.id: entity.type or "Object" for entity in entities}
            for relation in self._db.query(Relation).filter(
                    Relation.ontology_id == ontology_id).all():
                src_type = entity_type.get(relation.source_entity)
                tgt_type = entity_type.get(relation.target_entity)
                if not src_type or not tgt_type:
                    continue
                neo.upsert_relation(
                    src_type, relation.source_entity, tgt_type,
                    relation.target_entity, relation.type,
                    props={
                        key: neo4j_safe_value(value)
                        for key, value in {
                            **dict(relation.properties or {}),
                            "id": relation.id,
                            "ontology_id": ontology_id,
                            "confidence": relation.confidence,
                        }.items()
                    })
            return True
        except Exception as e:
            logger.warning("Neo4j projection rebuild failed: %s", e)
            return False
        finally:
            if neo is not None:
                try:
                    neo.close()
                except Exception:
                    pass

    def _rebuild_chroma_projection(self, ontology_id: str) -> int | None:
        """Replace the semantic-search projection from relational current state."""
        from app.models.entity import Entity
        try:
            from app.services.v2.vector.chroma_service import ChromaService
            chroma = ChromaService()
            if not chroma.available:
                return None
            name = f"ontology_{ontology_id}"
            # Delete may return false when the collection does not exist; upsert
            # below creates it.  Other failures are contained by the service and
            # surfaced as a zero count in the mapping result.
            chroma.delete_collection(name)
            entities = self._db.query(Entity).filter(
                Entity.ontology_id == ontology_id).all()
            payload = [{
                "id": entity.id,
                "type": entity.type or "Object",
                "name_cn": entity.name_cn,
                "name_en": entity.name_en,
                "confidence": entity.confidence,
                "properties": dict(entity.properties or {}),
            } for entity in entities]
            written = chroma.upsert_entities(ontology_id, payload) if payload else 0
            if payload and written != len(payload):
                return None
            if chroma.count(ontology_id) != len(payload):
                return None
            return written
        except Exception as e:
            logger.warning("Chroma projection rebuild failed: %s", e)
            return None

    def _write_neo4j_relations(self, ontology_id: str, src_class: str, tgt_class: str, rel_type: str) -> None:
        from app.models.relation import Relation
        from app.models.entity import Entity
        try:
            from app.services.v2.graph.neo4j_service import Neo4jService
            neo = Neo4jService()
            if not neo.available:
                return
            rels = self._db.query(Relation).filter(
                Relation.ontology_id == ontology_id, Relation.type == rel_type,
            ).all()
            for r in rels:
                neo.upsert_relation(src_class, r.source_entity,
                                    tgt_class, r.target_entity, rel_type,
                                    props={"id": r.id, "ontology_id": ontology_id,
                                           "confidence": r.confidence})
            neo.close()
        except Exception as e:
            logger.warning(f"Neo4j relation 写入失败（非致命）: {e}")
