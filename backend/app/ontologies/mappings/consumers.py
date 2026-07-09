"""数据集被本体映射绑定的消费者查询——删除数据集前的依赖检查。

对象映射（OntologyMapping.curated_dataset_id）与关系映射
（OntologyLinkMapping.src/tgt/edge_dataset_id）都可能绑定某个数据集。
列名带 curated_ 是历史遗留，实际存任意数据集 id（含人工数据集），
故成品/人工删除都需经此检查，避免删掉正被本体投影灌数的数据集造成断源。
"""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session


def dataset_mapping_bindings(db: Session, dataset_id: str) -> list[dict]:
    """返回绑定了该数据集的本体映射（对象映射 + 关系映射）。空 = 无绑定。"""
    from app.ontologies.mappings.models import OntologyMapping, OntologyLinkMapping

    out: list[dict] = []
    for m in db.query(OntologyMapping).filter(
            OntologyMapping.curated_dataset_id == dataset_id).all():
        out.append({"mapping_id": m.id, "ontology_id": m.ontology_id,
                    "kind": "object", "name": m.entity_class})
    for lm in db.query(OntologyLinkMapping).filter(or_(
            OntologyLinkMapping.src_dataset_id == dataset_id,
            OntologyLinkMapping.tgt_dataset_id == dataset_id,
            OntologyLinkMapping.edge_dataset_id == dataset_id)).all():
        out.append({"mapping_id": lm.id, "ontology_id": lm.ontology_id,
                    "kind": "link", "name": lm.relation_type})
    return out
