"""Authoritative SQL-to-Neo4j full projection rebuild."""
from __future__ import annotations

import hashlib
import logging
import re

from sqlalchemy.orm import Session

from app.ontologies.mappings.neo4j_projection_contract import (
    neo4j_entity_properties, neo4j_projection_properties,
)


logger = logging.getLogger(__name__)


def _relationship_type(value: str | None) -> str:
    """Return a deterministic, injection-safe Cypher relationship type."""
    raw = str(value or "RELATED").strip() or "RELATED"
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", raw).strip("_").upper()
    if not normalized:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12].upper()
        normalized = f"REL_{digest}"
    if normalized[0].isdigit():
        normalized = "REL_" + normalized
    return normalized


def _projection_key(ontology_id: str, stable_id: str) -> str:
    """Neo4j MERGE key; public IDs remain ontology-local stable IDs."""
    return f"{ontology_id}::{stable_id}"


def projection_node_id(
    formal_instance_id: object,
    external_id: object | None,
    legacy_entity_ids: set[str],
) -> str:
    """Resolve the public node ID shared by rebuild and repair reporting."""
    normalized_external_id = (
        str(external_id) if external_id is not None else None
    )
    if (
        normalized_external_id is not None
        and normalized_external_id in legacy_entity_ids
    ):
        return normalized_external_id
    return str(formal_instance_id)


def rebuild_neo4j_projection(db: Session, ontology_id: str) -> bool:
    """Rebuild and validate the complete derived graph from SQL truth.

    Both the legacy relational projection and Formal-only instances are
    included. Nodes expose stable SQL/Formal identifiers; Neo4j
    ``elementId`` is never part of the public identity contract.
    """
    from app.models.entity import Entity
    from app.models.relation import Relation
    from app.models.ontology_formal import (
        LinkInstance,
        LinkType,
        ObjectInstance,
        ObjectType,
    )

    neo = None
    try:
        from app.ontologies.graph.neo4j_service import Neo4jService

        neo = Neo4jService()
        if not neo.available:
            return False
        neo.delete_by_ontology(ontology_id)

        entities = db.query(Entity).filter(
            Entity.ontology_id == ontology_id,
        ).all()
        nodes: dict[str, dict] = {}
        for entity in entities:
            # Preserve explicitly mapped business id/name values under stable
            # aliases instead of letting the graph identity envelope erase
            # them. Neo4jService performs the final reversible value encoding.
            legacy_properties = neo4j_entity_properties(
                entity.properties,
                entity_id=entity.id,
                ontology_id=ontology_id,
                updated_at=entity.updated_at,
                encode_values=False,
            )
            nodes[str(entity.id)] = {
                **legacy_properties,
                "id": str(entity.id),
                "projection_key": _projection_key(
                    ontology_id,
                    str(entity.id),
                ),
                "source_entity_id": str(entity.id),
                "ontology_id": ontology_id,
                "name_cn": entity.name_cn or "",
                "name": entity.name_cn or "",
                "name_en": entity.name_en or "",
                "type": entity.type or "Object",
                "description": entity.description or "",
                "confidence": entity.confidence,
                "version": entity.version or "v0.1",
            }

        object_types = {
            item.id: item
            for item in db.query(ObjectType).filter(
                ObjectType.ontology_id == ontology_id,
            ).all()
        }
        formal_instances = db.query(ObjectInstance).filter(
            ObjectInstance.ontology_id == ontology_id,
        ).all()
        # Freeze the legacy namespace: rebuilding a growing set is quadratic
        # and can misclassify a later Formal external_id as a legacy mirror.
        legacy_entity_ids = set(nodes)
        formal_stable_ids: dict[str, str] = {}
        formal_stable_owners: dict[str, str] = {}
        for instance in formal_instances:
            # Only merge a Formal row with the legacy row it explicitly
            # mirrors. Arbitrary external IDs are not globally unique.
            stable_id = projection_node_id(
                instance.id,
                instance.external_id,
                legacy_entity_ids,
            )
            explicit_legacy_id = (
                str(instance.external_id)
                if instance.external_id is not None
                else None
            )
            if (
                stable_id in legacy_entity_ids
                and explicit_legacy_id != stable_id
            ):
                logger.error(
                    "Formal instance conflicts with an unrelated legacy "
                    "entity identity: instance=%s entity=%s ontology=%s",
                    instance.id,
                    stable_id,
                    ontology_id,
                )
                return False
            previous_owner = formal_stable_owners.get(stable_id)
            if (
                previous_owner is not None
                and previous_owner != str(instance.id)
            ):
                logger.error(
                    "Formal instances collide on graph identity: "
                    "%s/%s ontology=%s",
                    previous_owner,
                    instance.id,
                    ontology_id,
                )
                return False
            formal_stable_owners[stable_id] = str(instance.id)
            formal_stable_ids[str(instance.id)] = stable_id
            object_type = object_types.get(instance.object_type_id)
            business = {
                **dict(instance.properties or {}),
                **dict(instance.computed or {}),
            }
            existing = nodes.get(stable_id, {})
            envelope = {
                "id": stable_id,
                "projection_key": _projection_key(
                    ontology_id,
                    stable_id,
                ),
                "formal_instance_id": str(instance.id),
                "object_type_id": str(instance.object_type_id),
                "ontology_id": ontology_id,
                "type": (
                    object_type.name
                    if object_type is not None
                    else existing.get("type") or "Object"
                ),
                "type_display_name": (
                    object_type.display_name
                    if object_type is not None
                    else ""
                ),
                "name": business.get("name")
                or business.get("name_cn")
                or existing.get("name")
                or stable_id,
                "name_cn": business.get("name_cn")
                or business.get("name")
                or existing.get("name_cn")
                or stable_id,
                "updated_at": instance.updated_at,
            }
            formal_properties = neo4j_projection_properties(
                business, envelope)
            nodes[stable_id] = {**existing, **formal_properties}

        rows = list(nodes.values())
        if rows:
            written = neo.batch_upsert_entities(
                "OntologyEntity",
                rows,
                key_field="projection_key",
            )
            if written != len(rows):
                logger.error(
                    "Neo4j node projection incomplete: expected=%s "
                    "actual=%s ontology=%s",
                    len(rows),
                    written,
                    ontology_id,
                )
                return False

        formal_links = db.query(LinkInstance).filter(
            LinkInstance.ontology_id == ontology_id,
        ).all()
        link_types = {
            item.id: item
            for item in db.query(LinkType).filter(
                LinkType.ontology_id == ontology_id,
            ).all()
        }
        legacy_relations = db.query(Relation).filter(
            Relation.ontology_id == ontology_id,
        ).all()
        legacy_relation_ids = {str(item.id) for item in legacy_relations}
        represented_relation_ids = {
            str(item.source_relation_id)
            for item in formal_links
            if item.source_relation_id
        }
        projected_relations: list[tuple[str, str, str, dict]] = []
        for relation in legacy_relations:
            if str(relation.id) in represented_relation_ids:
                continue
            src = str(relation.source_entity)
            tgt = str(relation.target_entity)
            if src not in nodes or tgt not in nodes:
                logger.error(
                    "Neo4j relation has missing endpoint: relation=%s "
                    "ontology=%s",
                    relation.id,
                    ontology_id,
                )
                return False
            projected_relations.append((
                src,
                tgt,
                str(relation.type or "RELATED"),
                neo4j_projection_properties(relation.properties, {
                        "id": str(relation.id),
                        "ontology_id": ontology_id,
                        "confidence": relation.confidence,
                        "semantic_type": str(relation.type or "RELATED"),
                    },
                ),
            ))

        for link in formal_links:
            link_id = str(link.id)
            source_relation_id = (
                str(link.source_relation_id)
                if link.source_relation_id is not None
                else None
            )
            if (
                link_id in legacy_relation_ids
                and source_relation_id != link_id
            ):
                logger.error(
                    "Formal link conflicts with an unrelated legacy relation "
                    "identity: link=%s relation=%s ontology=%s",
                    link.id,
                    link_id,
                    ontology_id,
                )
                return False
            src = formal_stable_ids.get(str(link.source_object_id))
            tgt = formal_stable_ids.get(str(link.target_object_id))
            if not src or not tgt or src not in nodes or tgt not in nodes:
                logger.error(
                    "Neo4j formal link has missing endpoint: link=%s "
                    "ontology=%s",
                    link.id,
                    ontology_id,
                )
                return False
            link_type = link_types.get(link.link_type_id)
            semantic_type = (
                link_type.name if link_type is not None else "RELATED"
            )
            projected_relations.append((
                src,
                tgt,
                semantic_type,
                neo4j_projection_properties(link.properties, {
                        "id": link_id,
                        "formal_link_instance_id": link_id,
                        "link_type_id": str(link.link_type_id),
                        "ontology_id": ontology_id,
                        "source_relation_id": link.source_relation_id,
                        "semantic_type": semantic_type,
                    },
                ),
            ))

        for src, tgt, semantic_type, props in projected_relations:
            if not neo.upsert_relation(
                "OntologyEntity",
                _projection_key(ontology_id, src),
                "OntologyEntity",
                _projection_key(ontology_id, tgt),
                _relationship_type(semantic_type),
                props=props,
                key_field="projection_key",
            ):
                logger.error(
                    "Neo4j relationship projection incomplete: id=%s "
                    "ontology=%s",
                    props.get("id"),
                    ontology_id,
                )
                return False
        return True
    except Exception as exc:
        logger.warning("Neo4j projection rebuild failed: %s", exc)
        return False
    finally:
        if neo is not None:
            try:
                neo.close()
            except Exception:
                pass
