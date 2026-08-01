"""v2 Search API — PostgreSQL keyword search and retired semantic search."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import cast, or_, String as SAString
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.deps import get_current_user
from app.models.entity import Entity

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SearchRequest(BaseModel):
    query: str
    mode: str = "keyword"  # keyword | semantic
    entity_type: str | None = None
    n_results: int = 10


def _raise_semantic_search_unsupported() -> None:
    """Expose the retired capability explicitly instead of returning false success."""
    raise HTTPException(
        status_code=501,
        detail={
            "code": "semantic_search_unsupported",
            "message": "语义搜索已停用；当前仅支持 PostgreSQL 关键词搜索",
        },
    )


def _sql_keyword_search(db: Session, ontology_id: str, q: str, n: int) -> list[dict]:
    """Search entity names, descriptions, and properties in PostgreSQL."""
    pattern = f"%{q}%"
    rows = db.query(Entity).filter(
        Entity.ontology_id == ontology_id,
        or_(
            Entity.name_cn.ilike(pattern),
            Entity.name_en.ilike(pattern),
            Entity.description.ilike(pattern),
            cast(Entity.properties, SAString).ilike(pattern),
        ),
    ).limit(n).all()
    return [
        {
            "id": e.id,
            "document": e.description or e.name_cn,
            "metadata": {
                "name_cn": e.name_cn,
                "name_en": e.name_en,
                "entity_type": e.type,
                "properties": e.properties or {},
            },
        }
        for e in rows
    ]


@router.get("/{ontology_id}/search/keyword")
def keyword_search(
    ontology_id: str,
    q: str = Query(..., description="搜索词"),
    n: int = Query(20, description="结果数"),
    db: Session = Depends(get_db),
):
    """关键词搜索 — PostgreSQL is the sole runtime search backend."""
    results = _sql_keyword_search(db, ontology_id, q, n)
    return {"results": results, "query": q, "search_backend": "postgresql"}


@router.get("/{ontology_id}/search/semantic")
def semantic_search(
    ontology_id: str,
    q: str = Query(..., description="搜索词"),
    n: int = Query(10, description="结果数"),
    entity_type: str | None = Query(None, description="实体类型过滤"),
):
    """Return an explicit unsupported response for the retired vector API."""
    del ontology_id, q, n, entity_type
    _raise_semantic_search_unsupported()


@router.post("/{ontology_id}/search")
def unified_search(ontology_id: str, body: SearchRequest, db: Session = Depends(get_db)):
    """统一搜索端点"""
    if body.mode == "semantic":
        _raise_semantic_search_unsupported()
    results = _sql_keyword_search(db, ontology_id, body.query, body.n_results)
    return {
        "results": results,
        "mode": body.mode,
        "search_backend": "postgresql",
    }
