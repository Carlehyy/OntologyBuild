from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.entity import Entity
from app.models.relation import Relation
from app.models.ontology import OntologyProject
from app.ontologies.access import (
    legacy_ontology_write_guard,
    ontology_access_guard,
)

router = APIRouter(dependencies=[
    Depends(ontology_access_guard),
    Depends(legacy_ontology_write_guard),
])


def _finish_projection(db: Session, ontology_id: str) -> None:
    from app.ontologies.projection_state import (
        ProjectionRebuildError,
        rebuild_after_commit,
    )

    try:
        rebuild_after_commit(db, ontology_id)
    except ProjectionRebuildError as exc:
        raise HTTPException(
            503,
            detail={
                "code": "ontology_projection_failed",
                "message": "关系已保存到关系型真相，但 Neo4j 图投影失败；请执行图修复",
                "ontology_id": ontology_id,
            },
        ) from exc

@router.get("")
def get_graph(ontology_id: str, limit: int = 300, db: Session = Depends(get_db), _=Depends(get_current_user)):
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise HTTPException(404, "Ontology not found")

    _require_projection_ready(db, ontology_id)
    neo4j_nodes, neo4j_edges = _try_neo4j(ontology_id, limit)
    return {
        "data": {
            "nodes": neo4j_nodes,
            "edges": neo4j_edges,
            "meta": {
                "ontology_id": ontology_id,
                "name": project.name,
                "entity_count": len(neo4j_nodes),
                "relation_count": len(neo4j_edges),
                "source": "neo4j",
            }
        }
    }


def _require_projection_ready(db: Session, ontology_id: str) -> None:
    from app.ontologies.projection_state import not_ready_detail, snapshot

    state = snapshot(db, ontology_id, lock_for_read=True)
    if not state.ready:
        raise HTTPException(
            status_code=503,
            detail=not_ready_detail(state),
        )


def _try_neo4j(ontology_id: str, limit: int) -> tuple[list, list]:
    """从 Neo4j 读取并转换为 v1 格式；空图保持为空图。"""
    try:
        from app.services.v2.graph.neo4j_service import Neo4jService
        svc = Neo4jService()
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "neo4j_unavailable",
                "message": "Neo4j 图数据库不可用，请检查服务与连接配置",
            },
        )

    if not svc.available:
        svc.close()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "neo4j_unavailable",
                "message": "Neo4j 图数据库不可用，请检查服务与连接配置",
            },
        )

    try:
        data = svc.get_graph_data(ontology_id, limit=limit)
        raw_nodes = data.get("nodes", [])
        raw_edges = data.get("edges", [])
        # 转换为 v1 GraphTab 期望的格式
        node_ids = {n["id"] for n in raw_nodes}
        nodes = [
            {
                "data": {
                    "id": n["id"],
                    "label": (n.get("properties") or {}).get("name_cn")
                             or (n.get("properties") or {}).get("name")
                             or (n.get("labels") or [n["id"]])[0],
                    "name_en": (n.get("properties") or {}).get("name_en", ""),
                    "type": (n.get("properties") or {}).get("type")
                    or (n.get("labels") or [""])[0],
                    "confidence": (n.get("properties") or {}).get("confidence", 1.0),
                }
            }
            for n in raw_nodes
        ]
        edges = [
            {
                "data": {
                    "id": e["id"],
                    "source": e["source"],
                    "target": e["target"],
                    "label": e.get("type", "RELATED"),
                    "confidence": (e.get("properties") or {}).get("confidence", 1.0),
                }
            }
            for e in raw_edges
            if e["source"] in node_ids and e["target"] in node_ids
        ]
        return nodes, edges
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "neo4j_operation_failed",
                "message": "Neo4j 图查询失败，请检查服务状态",
            },
        )
    finally:
        svc.close()

@router.post("/relations")
def create_relation(
    ontology_id: str,
    body: dict,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    from app.models.relation import Relation
    from app.ontologies.projection_state import mark_projecting
    from app.ontologies.runtime_fence import _ontology_build_lock
    import uuid
    with _ontology_build_lock(db, ontology_id):
        source_id = str(body.get("source_entity") or "").strip()
        target_id = str(body.get("target_entity") or "").strip()
        requested_endpoints = {source_id, target_id} - {""}
        existing_endpoints = {
            str(row[0])
            for row in db.query(Entity.id).filter(
                Entity.ontology_id == ontology_id,
                Entity.id.in_(requested_endpoints),
            ).all()
        }
        missing_endpoints = sorted(
            ({source_id, target_id} - {""}) - existing_endpoints
        )
        if not source_id or not target_id or missing_endpoints:
            raise HTTPException(
                422,
                detail={
                    "code": "relation_endpoint_not_in_ontology",
                    "message": "关系两端必须是当前本体中的实体",
                    "missing_entity_ids": missing_endpoints,
                },
            )
        relation = Relation(
            id=str(uuid.uuid4()),
            ontology_id=ontology_id,
            source_entity=source_id,
            target_entity=target_id,
            type=body.get("type", "关联"),
            properties=body.get("properties", {}),
            confidence=body.get("confidence", 1.0),
        )
        db.add(relation)
        mark_projecting(db, ontology_id)
        db.commit()
        db.refresh(relation)
        _finish_projection(db, ontology_id)
    return {"data": {"id": relation.id, "source": relation.source_entity, "target": relation.target_entity, "type": relation.type}}

@router.delete("/relations/{relation_id}", status_code=204)
def delete_relation(ontology_id: str, relation_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    from app.ontologies.projection_state import mark_projecting
    from app.ontologies.runtime_fence import _ontology_build_lock

    with _ontology_build_lock(db, ontology_id):
        r = db.query(Relation).filter(Relation.id == relation_id, Relation.ontology_id == ontology_id).first()
        if not r:
            raise HTTPException(404, "Not found")
        db.delete(r)
        mark_projecting(db, ontology_id)
        db.commit()
        _finish_projection(db, ontology_id)
