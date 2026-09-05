"""v2 Graph API — 基于 Neo4j"""
from __future__ import annotations
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.deps import get_current_user, get_db, require_admin
from app.database import SessionLocal
from app.ontologies.access import ontology_access_guard

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_neo4j():
    """Return the concrete Neo4j service used by graph routes."""
    from app.ontologies.graph.neo4j_service import Neo4jService

    return Neo4jService()


def get_graph_service():
    """Return Neo4j or fail explicitly; graph reads have no local fallback."""
    try:
        neo = get_neo4j()
    except Exception:
        _raise_neo4j_unavailable()
    if not neo.available:
        neo.close()
        _raise_neo4j_unavailable()
    return neo


def _raise_neo4j_unavailable() -> NoReturn:
    raise HTTPException(
        status_code=503,
        detail={
            "code": "neo4j_unavailable",
            "message": "Neo4j 图数据库不可用，请检查服务与连接配置",
        },
    )


def _require_projection_ready(db: Session, ontology_id: str) -> None:
    """Block graph reads while the SQL-to-Neo4j projection is incomplete."""
    from app.ontologies.projection_state import not_ready_detail, snapshot

    state = snapshot(db, ontology_id, lock_for_read=True)
    if not state.ready:
        raise HTTPException(
            status_code=503,
            detail=not_ready_detail(state),
        )


def _raise_neo4j_operation_failed(operation: str) -> NoReturn:
    raise HTTPException(
        status_code=503,
        detail={
            "code": "neo4j_operation_failed",
            "message": f"Neo4j {operation}失败，请检查服务状态",
        },
    )


class CypherRequest(BaseModel):
    query: str
    params: dict = {}


@router.get("/{ontology_id}/graph")
def get_graph(
    ontology_id: str,
    limit: int = 200,
    label_filter: str | None = None,
    db: Session = Depends(get_db),
):
    """返回本体图谱数据 (Neovis.js 兼容格式)"""
    _require_projection_ready(db, ontology_id)
    svc = get_graph_service()
    try:
        data = svc.get_graph_data(ontology_id, limit=limit, label_filter=label_filter)
    except Exception:
        _raise_neo4j_operation_failed("图查询")
    finally:
        svc.close()
    data["graph_service"] = "Neo4jService"
    return data


@router.get("/{ontology_id}/graph/quality")
def graph_quality(ontology_id: str):
    from app.models.entity import Entity
    from app.models.relation import Relation
    from collections import Counter

    db = SessionLocal()
    try:
        entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
        relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
        entity_ids = {e.id for e in entities}
        connected_ids = {r.source_entity for r in relations} | {r.target_entity for r in relations}
        orphan_relations = [
            r.id for r in relations
            if r.source_entity not in entity_ids or r.target_entity not in entity_ids
        ]
        isolated = [e.id for e in entities if e.id not in connected_ids]
        names = [e.name_cn for e in entities if e.name_cn]
        duplicate_names = {name: count for name, count in Counter(names).items() if count > 1}
        object_types = Counter(e.type or "Entity" for e in entities)
        relation_types = Counter(r.type or "RELATED" for r in relations)
        node_count = len(entities)
        edge_count = len(relations)
        duplicate_name_instances = sum(duplicate_names.values())
        quality_score = 1.0
        if node_count:
            quality_score -= min(0.4, len(isolated) / node_count * 0.4)
            quality_score -= min(0.25, duplicate_name_instances / node_count * 0.25)
        if edge_count:
            quality_score -= min(0.25, len(orphan_relations) / edge_count * 0.25)
        return {
            "ontology_id": ontology_id,
            "node_count": node_count,
            "edge_count": edge_count,
            "isolated_node_count": len(isolated),
            "orphan_relation_count": len(orphan_relations),
            "duplicate_display_name_count": duplicate_name_instances,
            "object_type_counts": dict(object_types),
            "relation_type_counts": dict(relation_types),
            "relation_density": round(edge_count / node_count, 4) if node_count else 0,
            "quality_score": round(max(0.0, quality_score), 4),
            "samples": {
                "isolated_node_ids": isolated[:10],
                "orphan_relation_ids": orphan_relations[:10],
                "duplicate_display_names": dict(list(duplicate_names.items())[:10]),
            },
        }
    finally:
        db.close()


@router.get("/{ontology_id}/integrations/status")
def integration_status(ontology_id: str, db: Session = Depends(get_db)):
    _require_projection_ready(db, ontology_id)
    try:
        neo = get_neo4j()
    except Exception:
        _raise_neo4j_unavailable()
    neo_available = bool(neo.available)
    neo.close()
    if not neo_available:
        _raise_neo4j_unavailable()
    return {
        "ontology_id": ontology_id,
        "neo4j": {"available": True},
        "graph_service": {
            "type": "Neo4jService",
            "available": True,
            "fallback": False,
        },
    }


@router.post(
    "/{ontology_id}/graph/cypher",
    dependencies=[Depends(require_admin)],
)
def run_cypher(
    ontology_id: str,
    body: CypherRequest,
    db: Session = Depends(get_db),
):
    """执行 Cypher 查询 (只读校验 + 强制 ontology_id 过滤)"""
    from app.ontologies.graph.cypher_builder import validate_readonly_cypher

    error = validate_readonly_cypher(body.query)
    if error:
        raise HTTPException(400, error)

    _require_projection_ready(db, ontology_id)
    svc = get_graph_service()
    params = dict(body.params or {})
    params["ontology_id"] = ontology_id  # 供查询中的 $ontology_id 使用, 防跨本体读取
    try:
        results = svc.run_cypher(body.query, params)
    except Exception:
        _raise_neo4j_operation_failed("查询")
    finally:
        svc.close()
    return {"results": results, "graph_service": "Neo4jService"}


@router.get("/{ontology_id}/graph/neighbors/{node_id}")
def get_neighbors(
    ontology_id: str,
    node_id: str,
    depth: int = 1,
    db: Session = Depends(get_db),
):
    """查询节点邻居"""
    _require_projection_ready(db, ontology_id)
    svc = get_graph_service()
    depth = max(1, min(depth, 5))
    query = f"""
    MATCH (n)-[r*1..{depth}]-(m)
    WHERE n.id = $node_id AND n.ontology_id = $ontology_id
    RETURN n, r, m LIMIT 100
    """
    try:
        results = svc.run_cypher(query, {"node_id": node_id, "ontology_id": ontology_id})
    except Exception:
        _raise_neo4j_operation_failed("邻居查询")
    finally:
        svc.close()
    return {"results": results, "graph_service": "Neo4jService"}


# ── 自然语言查询 ──────────────────────────────────────────────────────

class NLQueryRequest(BaseModel):
    question: str
    schema: dict = {}


@router.post("/{ontology_id}/graph/ask")
def nl_query(
    ontology_id: str,
    body: NLQueryRequest,
    db: Session = Depends(get_db),
):
    """自然语言 → Cypher → Neo4j 图数据。"""
    from app.ontologies.graph.nl2cypher import NL2CypherService

    _require_projection_ready(db, ontology_id)
    nl_svc = NL2CypherService()
    plan = nl_svc.translate(body.question, body.schema)

    svc = get_graph_service()
    try:
        results = svc.run_cypher(plan.cypher, {"ontology_id": ontology_id})
    except Exception:
        _raise_neo4j_operation_failed("自然语言图查询")
    finally:
        svc.close()

    return {
        "results": results,
        "cypher": plan.cypher,
        "explanation": plan.explanation,
        "confidence": plan.confidence,
        "graph_service": "Neo4jService",
    }


# ── 高级图分析 ─────────────────────────────────────────────────────────

@router.get("/{ontology_id}/graph/path")
def graph_path(
    ontology_id: str,
    src: str,
    tgt: str,
    db: Session = Depends(get_db),
):
    """两节点间最短路径"""
    from app.ontologies.graph.graph_analytics import GraphAnalyticsService
    _require_projection_ready(db, ontology_id)
    svc = None
    try:
        svc = GraphAnalyticsService()
        return svc.shortest_path(ontology_id, src, tgt)
    except Exception:
        _raise_neo4j_operation_failed("最短路径查询")
    finally:
        if svc is not None:
            svc.close()


@router.get("/{ontology_id}/graph/degree/{node_id}")
def node_degree(
    ontology_id: str,
    node_id: str,
    db: Session = Depends(get_db),
):
    """查询节点度数（入度 + 出度）"""
    from app.ontologies.graph.graph_analytics import GraphAnalyticsService
    _require_projection_ready(db, ontology_id)
    svc = None
    try:
        svc = GraphAnalyticsService()
        return svc.node_degree(ontology_id, node_id)
    except Exception:
        _raise_neo4j_operation_failed("节点度数查询")
    finally:
        if svc is not None:
            svc.close()


@router.get("/{ontology_id}/graph/top-nodes")
def top_nodes(
    ontology_id: str,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """返回连接数最多的 Top-N 节点"""
    from app.ontologies.graph.graph_analytics import GraphAnalyticsService
    _require_projection_ready(db, ontology_id)
    svc = None
    try:
        svc = GraphAnalyticsService()
        return {"nodes": svc.top_connected_nodes(ontology_id, limit)}
    except Exception:
        _raise_neo4j_operation_failed("Top-N 节点查询")
    finally:
        if svc is not None:
            svc.close()


@router.post(
    "/{ontology_id}/graph/sync",
    dependencies=[Depends(ontology_access_guard)],
)
def sync_graph(ontology_id: str):
    """Repair Neo4j from committed PostgreSQL/Formal truth."""
    from app.database import SessionLocal
    from app.models.ontology import OntologyProject
    from app.models.entity import Entity
    from app.models.relation import Relation
    from app.models.ontology_formal import ObjectInstance, LinkInstance
    from app.ontologies.mappings.mapping_service import MappingService
    from app.ontologies.mappings.projection_adapter import projection_node_id
    from app.ontologies.projection_state import (
        ProjectionRebuildError,
        mark_projecting,
        rebuild_after_commit,
    )
    from app.ontologies.runtime_fence import _ontology_build_lock

    db = SessionLocal()
    try:
        project = db.query(OntologyProject).filter(
            OntologyProject.id == ontology_id,
        ).first()
        if project is None:
            raise HTTPException(404, "Ontology not found")
        with _ontology_build_lock(db, ontology_id):
            mark_projecting(db, ontology_id)
            db.commit()
            service = MappingService(db)
            result = rebuild_after_commit(
                db,
                ontology_id,
                rebuild=service._rebuild_neo4j_projection,
                run_in_test=True,
            )
        legacy_entity_ids = {
            str(row[0])
            for row in db.query(Entity.id).filter(
                Entity.ontology_id == ontology_id,
            ).all()
        }
        stable_node_ids = set(legacy_entity_ids)
        stable_node_ids.update(
            projection_node_id(
                instance_id,
                external_id,
                legacy_entity_ids,
            )
            for instance_id, external_id in db.query(
                ObjectInstance.id,
                ObjectInstance.external_id,
            ).filter(ObjectInstance.ontology_id == ontology_id).all()
        )
        represented_relations = {
            str(row[0])
            for row in db.query(LinkInstance.source_relation_id).filter(
                LinkInstance.ontology_id == ontology_id,
                LinkInstance.source_relation_id.is_not(None),
            ).all()
        }
        relational_edges = db.query(Relation.id).filter(
            Relation.ontology_id == ontology_id,
        ).all()
        formal_edge_count = db.query(LinkInstance).filter(
            LinkInstance.ontology_id == ontology_id,
        ).count()
        edge_count = formal_edge_count + sum(
            1 for (relation_id,) in relational_edges
            if str(relation_id) not in represented_relations
        )
        return {
            "synced": True,
            "entities": len(stable_node_ids),
            "relations": edge_count,
            "ontology_id": ontology_id,
            "projection": result,
        }
    except ProjectionRebuildError:
        _raise_neo4j_operation_failed("图同步")
    finally:
        db.close()
