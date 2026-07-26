"""Mapping Apply 异步 Celery 任务"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


def mapping_apply_task(mapping_id: str, ontology_id: str):
    """
    异步执行 Mapping 并写入 Neo4j。
    完整实现在 M3.4 增量更新中集成到触发链路。
    """
    # Celery workers do not run FastAPI's lifespan, where the API process
    # registers Sentinel CDC listeners. Register in the worker process before
    # creating its Session so Formal projection commits atomically create the
    # durable outbox rows consumed by Mapping's synchronous Sentinel barrier.
    # ``register_cdc`` is process-idempotent.  This task drains its captured
    # causal chain synchronously, so it registers listeners without starting a
    # second consumer that would race the task for the same outbox row.  The
    # API process keeps the recovery worker enabled for crash recovery.
    from app.ontologies.sentinels.cdc import register_cdc
    register_cdc(start_worker=False)

    from app.database import SessionLocal
    from app.services.v2.mapping.mapping_service import MappingService
    from app.models.v2.mapping import OntologyLinkMapping, OntologyMapping

    db = SessionLocal()
    try:
        mapping = db.query(OntologyMapping).filter(
            OntologyMapping.id == mapping_id,
            OntologyMapping.ontology_id == ontology_id).first()
        trigger_mapping_kind = "object"
        if not mapping:
            mapping = db.query(OntologyLinkMapping).filter(
                OntologyLinkMapping.id == mapping_id,
                OntologyLinkMapping.ontology_id == ontology_id).first()
            trigger_mapping_kind = "link"
        if not mapping:
            raise ValueError(
                f"Mapping {mapping_id} not found in ontology {ontology_id}")

        svc = MappingService(db)
        # A source-object delta can change inferred FK edges, manual link
        # mappings and vector documents.  Rebuild the complete ontology under
        # the project lock; object-only apply left stale downstream state.
        result = svc.build_all(ontology_id, require_approved=True)
        result["trigger_mapping_id"] = mapping_id
        result["trigger_mapping_kind"] = trigger_mapping_kind
        logger.info(f"Mapping applied: {result}")
        return result
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
