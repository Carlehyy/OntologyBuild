"""LLM 抽取增强路由 — 多轮抽取管道 + 候选审核激活"""
from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.services.llm_extraction_service import get_llm_extraction_service

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.post("/{ontology_id}/extract")
def run_extraction(ontology_id: str, body: dict, db: Session = Depends(get_db)):
    """运行LLM多轮抽取管道"""
    text = body.get("text", "")
    domain = body.get("domain", "金融风控")

    svc = get_llm_extraction_service()
    result = svc.extract_pipeline(text, ontology_id, domain)

    return {
        "data": result,
        "llm_available": svc.available,
        "message": "extraction completed",
    }


@router.post("/{ontology_id}/extract/nl-to-cypher")
def nl_to_cypher(ontology_id: str, body: dict, db: Session = Depends(get_db)):
    """自然语言转Cypher查询"""
    question = body.get("question", "")
    svc = get_llm_extraction_service()
    result = svc.nl_to_cypher(question, ontology_id)
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

    db.commit()

    approved_relations = []
    # Build name→id map
    all_entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
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

    db.commit()

    return {
        "data": {
            "approved_entities": approved_entities,
            "approved_relations": approved_relations,
        },
        "message": f"Approved {len(approved_entities)} entities, {len(approved_relations)} relations",
    }


@router.get("/{ontology_id}/extraction/status")
def get_extraction_method(ontology_id: str):
    """获取当前抽取方式状态"""
    svc = get_llm_extraction_service()
    return {
        "data": {
            "llm_available": svc.available,
            "model": "deepseek-v4-flash" if svc.available else None,
            "method": "llm_multi_round" if svc.available else "deterministic_fallback",
        }
    }
