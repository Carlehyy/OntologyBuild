from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.config import settings
from app.deps import get_db, get_current_user
from app.ontologies.projects.models import OntologyProject
from app.ontologies.entities.models import Entity
from app.ontologies.logic.models import LogicRule
from app.ontologies.actions.models import Action
from app.platform import cache as platform_cache

router = APIRouter()

def _safe_count(db, model):
    try:
        return db.query(model).count()
    except Exception:
        return 0

def _counts_by_ontology(db, model, ontology_ids: list[str]) -> dict[str, int]:
    """按本体分组的计数聚合：一次 GROUP BY 取回全部最近本体的卡片计数。"""
    rows = (
        db.query(model.ontology_id, func.count(model.id))
        .filter(model.ontology_id.in_(ontology_ids))
        .group_by(model.ontology_id)
        .all()
    )
    return {ontology_id: count for ontology_id, count in rows}

@router.get("/stats")
def get_stats(db: Session = Depends(get_db), _=Depends(get_current_user)):
    def build():
        # Recent ontologies：卡片计数按模型分组聚合取回，避免逐本体 COUNT。
        recent = db.query(OntologyProject).order_by(OntologyProject.updated_at.desc()).limit(6).all()
        recent_ids = [o.id for o in recent]
        entity_counts = _counts_by_ontology(db, Entity, recent_ids) if recent_ids else {}
        logic_counts = _counts_by_ontology(db, LogicRule, recent_ids) if recent_ids else {}
        action_counts = _counts_by_ontology(db, Action, recent_ids) if recent_ids else {}
        recent_list = []
        for o in recent:
            recent_list.append({
                "id": o.id,
                "name": o.name,
                "domain": o.domain,
                "status": o.status,
                "entity_count": entity_counts.get(o.id, 0),
                "logic_count": logic_counts.get(o.id, 0),
                "action_count": action_counts.get(o.id, 0),
                "updated_at": o.updated_at.isoformat() if o.updated_at else None,
            })

        # Domain distribution
        domain_rows = (
            db.query(OntologyProject.domain, func.count(OntologyProject.id))
            .group_by(OntologyProject.domain)
            .all()
        )
        domain_counts = {row[0]: row[1] for row in domain_rows if row[0]}

        # Status breakdown
        status_rows = (
            db.query(OntologyProject.status, func.count(OntologyProject.id))
            .group_by(OntologyProject.status)
            .all()
        )
        status_counts = {row[0]: row[1] for row in status_rows if row[0]}

        # jsonable_encoder 把 datetime 等归一为与 FastAPI 响应一致的 JSON
        # 值，保证缓存命中与直查两种路径返回逐字节相同的响应。
        return jsonable_encoder({
            "data": {
                "ontology_count": _safe_count(db, OntologyProject),
                "entity_count": _safe_count(db, Entity),
                "logic_count": _safe_count(db, LogicRule),
                "action_count": _safe_count(db, Action),
                "recent_ontologies": recent_list,
                "domain_counts": domain_counts,
                "status_counts": status_counts,
            },
            "message": "ok"
        })

    # 全局只读聚合看板：短 TTL 缓存兜底新鲜度，无写路径联动失效（fail-open）。
    return platform_cache.cached_call(
        platform_cache.stats_cache_key(),
        settings.platform_stats_cache_ttl_seconds,
        build,
    )
