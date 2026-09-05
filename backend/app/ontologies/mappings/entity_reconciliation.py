"""Relational entity reconciliation and legacy projection compatibility."""
from __future__ import annotations

import logging

from app.ontologies.mappings.models import OntologyMapping
from app.ontologies.mappings.errors import MappingApplyError

logger = logging.getLogger(__name__)


class EntityReconciliationMixin:
    """Own Entity rows and reconcile stale relational/Formal projections."""

    def _write_v1_entities(self, mapping: OntologyMapping, entities: list[dict]) -> int:
        from app.models.entity import Entity
        count = 0
        try:
            for props in entities:
                eid = props["id"]
                name_cn = props.get("name_cn") or props.get("display_name") or eid
                name_en = props.get("name_en") or props.get("display_name") or eid
                other = {k: v for k, v in props.items() if k not in ("id", "ontology_id")}
                existing = self._db.query(Entity).filter(
                    Entity.id == eid, Entity.ontology_id == mapping.ontology_id).first()
                previous_owners = set(
                    (existing.properties or {}).get("__mapping_ids__", [])
                    if isinstance(existing, Entity) else [])
                other["__mapping_ids__"] = sorted(previous_owners | {mapping.id})
                self._db.merge(Entity(
                    id=eid, ontology_id=mapping.ontology_id,
                    name_cn=str(name_cn)[:200], name_en=str(name_en)[:200],
                    type=mapping.entity_class, properties=other,
                    confidence=mapping.confidence or 0.85,
                ))
                count += 1
            self._db.flush()
        except Exception as e:
            logger.exception("v1 entities 写入失败")
            self._db.rollback()
            raise MappingApplyError(f"v1 entities 写入失败: {e}") from e
        return count

    def _adopt_legacy_projection_ownership(self, mapping: OntologyMapping) -> None:
        """Backfill provenance for pre-hardening Entity rows before reconciliation.

        Adoption is safe only when one mapping owns the ontology/entity_class.  If
        several mappings could have produced the same legacy rows, deleting by
        guess would be worse than refusing the apply.
        """
        from app.models.entity import Entity

        unowned = [
            entity for entity in self._db.query(Entity).filter(
                Entity.ontology_id == mapping.ontology_id,
                Entity.type == mapping.entity_class,
            ).all()
            if not (entity.properties or {}).get("__mapping_ids__")
        ]
        if not unowned:
            return
        owners = self._db.query(OntologyMapping).filter(
            OntologyMapping.ontology_id == mapping.ontology_id,
            OntologyMapping.entity_class == mapping.entity_class,
        ).count()
        if owners != 1:
            raise MappingApplyError(
                f"实体类型 {mapping.entity_class} 存在 {len(unowned)} 条无来源的历史投影，"
                f"且有 {owners} 个映射可能拥有它们；拒绝猜测删除归属，请先做一次性血缘迁移")
        for entity in unowned:
            props = dict(entity.properties or {})
            props["__mapping_ids__"] = [mapping.id]
            entity.properties = props
        self._db.flush()

    def _reconcile_mapping_entities(
        self,
        mapping: OntologyMapping,
        current_entity_ids: set[str],
        *,
        source_dataset_version_id: str | None = None,
    ) -> list[str]:
        """Remove current-state projections absent from the new lake snapshot.

        Immutable PropertyFact history is retained; formal object/link tombstones
        are appended before deleting the materialized current-state rows.
        """
        from sqlalchemy import or_
        from app.models.entity import Entity
        from app.models.relation import Relation
        from app.models.ontology_formal import ObjectInstance, LinkInstance
        from app.ontologies.formal_modeling.facts import (
            record_link_fact, record_object_tombstone)

        removed: list[str] = []
        candidates = self._db.query(Entity).filter(
            Entity.ontology_id == mapping.ontology_id).all()
        for entity in candidates:
            props = dict(entity.properties or {})
            owners = set(props.get("__mapping_ids__") or [])
            if mapping.id not in owners or entity.id in current_entity_ids:
                continue
            owners.discard(mapping.id)
            if owners:
                props["__mapping_ids__"] = sorted(owners)
                entity.properties = props
                continue

            # Legacy current-state edges are derived from the object projection.
            for relation in self._db.query(Relation).filter(
                Relation.ontology_id == mapping.ontology_id,
                or_(Relation.source_entity == entity.id,
                    Relation.target_entity == entity.id),
            ).all():
                self._db.delete(relation)

            instance = self._db.query(ObjectInstance).filter(
                ObjectInstance.ontology_id == mapping.ontology_id,
                ObjectInstance.external_id == entity.id,
                ObjectInstance.source == "pipeline",
            ).first()
            if instance is not None:
                links = self._db.query(LinkInstance).filter(
                    LinkInstance.ontology_id == mapping.ontology_id,
                    or_(LinkInstance.source_object_id == instance.id,
                        LinkInstance.target_object_id == instance.id),
                ).all()
                for link in links:
                    record_link_fact(
                        self._db, ontology_id=mapping.ontology_id,
                        link_instance_id=link.id, link_type_id=link.link_type_id,
                        exists=False, source=f"mapping://{mapping.id}",
                        source_dataset_version_id=source_dataset_version_id)
                    self._db.delete(link)
                record_object_tombstone(
                    self._db, ontology_id=mapping.ontology_id,
                    instance_id=instance.id, object_type_id=instance.object_type_id,
                    source=f"mapping://{mapping.id}",
                    source_dataset_version_id=source_dataset_version_id)
                self._db.delete(instance)
            self._db.delete(entity)
            removed.append(entity.id)
        self._db.flush()
        return removed
