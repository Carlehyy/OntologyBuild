"""LLM 抽取增强路由 — 多轮抽取管道 + 候选审核激活"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.ontology import OntologyProject
from app.ontologies.projection_state import (
    ProjectionRebuildError,
    mark_projecting,
    rebuild_after_commit,
)
from app.ontologies.runtime_fence import _ontology_build_lock
from app.services.llm_extraction_service import (
    LLMExtractionError,
    get_llm_extraction_service,
)

router = APIRouter(dependencies=[Depends(get_current_user)])


def _finish_projection(db: Session, ontology_id: str) -> None:
    try:
        rebuild_after_commit(db, ontology_id)
    except ProjectionRebuildError as exc:
        raise HTTPException(503, detail={
            "code": "ontology_projection_failed",
            "message": (
                "审核结果已保存到关系型真相，但 Neo4j 图投影失败；"
                "图读取已阻断，请执行图修复"
            ),
            "ontology_id": ontology_id,
        }) from exc


@router.post("/{ontology_id}/extract")
def run_extraction(ontology_id: str, body: dict, db: Session = Depends(get_db)):
    """运行LLM多轮抽取管道"""
    text = body.get("text", "")
    domain = body.get("domain", "金融风控")

    svc = get_llm_extraction_service(db)
    try:
        result = svc.extract_pipeline(text, ontology_id, domain)
    except LLMExtractionError as exc:
        raise HTTPException(
            503,
            detail={
                "code": "llm_extraction_failed",
                "message": str(exc),
            },
        ) from exc

    return {
        "data": result,
        "llm_available": svc.available,
        "message": "extraction completed",
    }


@router.post("/{ontology_id}/extract/nl-to-cypher")
def nl_to_cypher(ontology_id: str, body: dict, db: Session = Depends(get_db)):
    """自然语言转Cypher查询"""
    question = body.get("question", "")
    svc = get_llm_extraction_service(db)
    try:
        result = svc.nl_to_cypher(question, ontology_id)
    except LLMExtractionError as exc:
        raise HTTPException(
            503,
            detail={
                "code": "llm_query_translation_failed",
                "message": str(exc),
            },
        ) from exc
    return {"data": result}


@router.post("/{ontology_id}/candidates/approve")
def approve_candidates(ontology_id: str, body: dict, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    """审核通过候选实体/关系，写入正式库"""
    from app.models.entity import Entity
    from app.models.relation import Relation
    import uuid

    entities = body.get("entities", [])
    relations = body.get("relations", [])

    with _ontology_build_lock(db, ontology_id):
        project = db.query(OntologyProject).filter(
            OntologyProject.id == ontology_id,
        ).with_for_update().first()
        if project is None:
            raise HTTPException(404, "Ontology not found")

        approved_entities = []
        for e in entities:
            ent = Entity(
                id=str(uuid.uuid4()),
                ontology_id=ontology_id,
                name_cn=e.get("name_cn", ""),
                name_en=e.get("name_en", ""),
                type=e.get("type", "企业"),
                description=e.get("description", ""),
                properties=e.get("properties", {}),
                confidence=e.get("_confidence", 0.8),
            )
            db.add(ent)
            approved_entities.append({"id": ent.id, "name_cn": ent.name_cn})

        # Keep entity and relation approval in one atomic truth transaction.
        # Autoflush would also make the new rows visible to this query, but an
        # explicit flush documents that the name map includes this batch.
        db.flush()
        approved_relations = []
        all_entities = db.query(Entity).filter(
            Entity.ontology_id == ontology_id,
        ).all()
        name_to_id = {e.name_cn: e.id for e in all_entities}

        for r in relations:
            src_id = name_to_id.get(r.get("source", ""))
            tgt_id = name_to_id.get(r.get("target", ""))
            if src_id and tgt_id:
                rel = Relation(
                    id=str(uuid.uuid4()),
                    ontology_id=ontology_id,
                    source_entity=src_id,
                    target_entity=tgt_id,
                    type=r.get("type", "关联"),
                    properties=r.get("properties", {}),
                    confidence=r.get("_confidence", 0.7),
                )
                db.add(rel)
                approved_relations.append({"id": rel.id, "type": rel.type})

        changed = bool(approved_entities or approved_relations)
        if changed:
            mark_projecting(db, ontology_id)
        db.commit()
        if changed:
            _finish_projection(db, ontology_id)

    return {
        "data": {
            "approved_entities": approved_entities,
            "approved_relations": approved_relations,
        },
        "message": f"Approved {len(approved_entities)} entities, {len(approved_relations)} relations",
    }


@router.get("/{ontology_id}/extraction/status")
def get_extraction_method(ontology_id: str, db: Session = Depends(get_db)):
    """获取当前抽取方式状态"""
    svc = get_llm_extraction_service(db)
    return {
        "data": {
            "llm_available": svc.available,
            "model": svc.model_name,
            "method": "llm_multi_round" if svc.available else "deterministic_rules",
        }
    }
