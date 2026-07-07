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
    from app.services.v2.dataset_service import DatasetService
    from app.models.v2.mapping import OntologyMapping
    from app.models.v2.curated import CuratedDataset

    db = SessionLocal()
    try:
        mapping = db.query(OntologyMapping).filter(OntologyMapping.id == mapping_id).first()
        if not mapping:
            logger.error(f"Mapping {mapping_id} not found")
            return

        # 数据源：curated 数据集的真实行（最新版本 + 行级审核编辑叠加）。
        # 之前只读 CuratedDataset.schema_json['sample_rows']——pipeline 产出的
        # 数据集根本没有该键，审核通过后的自动映射一直在拿空数据空跑。
        data: list[dict] = []
        try:
            from app.data_channel.curated.review_service import load_rows_with_edits
            data = load_rows_with_edits(db, mapping.curated_dataset_id, limit=10000)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"读取 curated 数据集失败，回退 sample_rows: {e}")
        if not data:
            ds = db.query(CuratedDataset).filter(
                CuratedDataset.id == mapping.curated_dataset_id
            ).first()
            if ds and ds.schema_json:
                data = ds.schema_json.get("sample_rows", [])

        if not data:
            logger.warning(f"Mapping {mapping_id} 无可用数据（数据集 {mapping.curated_dataset_id}），跳过")
            return

        svc = MappingService(db)
        result = svc.apply_mapping(mapping_id, data)
        logger.info(f"Mapping applied: {result}")
    except Exception as e:
        logger.error(f"Mapping task failed: {e}")
    finally:
        db.close()


# Celery 注册（可选）
try:
    from app.tasks.extraction import celery_app
    mapping_apply_task = celery_app.task(mapping_apply_task)
except Exception:
    pass
