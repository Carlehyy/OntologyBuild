"""Mapping Apply 异步 Celery 任务"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


def mapping_apply_task(mapping_id: str, ontology_id: str):
    """
    异步执行 Mapping 并写入 Neo4j。
    完整实现在 M3.4 增量更新中集成到触发链路。
    """
    from app.database import SessionLocal
    from app.services.v2.mapping.mapping_service import MappingService
    from app.models.v2.mapping import OntologyMapping

    db = SessionLocal()
    try:
        mapping = db.query(OntologyMapping).filter(
            OntologyMapping.id == mapping_id,
            OntologyMapping.ontology_id == ontology_id).first()
        if not mapping:
            raise ValueError(
                f"Mapping {mapping_id} not found in ontology {ontology_id}")

        svc = MappingService(db)
        # A source-object delta can change inferred FK edges, manual link
        # mappings and vector documents.  Rebuild the complete ontology under
        # the project lock; object-only apply left stale downstream state.
        result = svc.build_all(ontology_id, require_approved=True)
        result["trigger_mapping_id"] = mapping_id
        logger.info(f"Mapping applied: {result}")
    except Exception as e:
        logger.exception(f"Mapping task failed: {e}")
        raise
    finally:
        db.close()


# Celery 注册（可选）
try:
    from app.tasks.extraction import celery_app
    mapping_apply_task = celery_app.task(mapping_apply_task)
except Exception:
    pass
