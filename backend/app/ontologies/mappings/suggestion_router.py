"""草稿映射建议 API — 知识库 + 规则 + LLM 概念化裁决。

建议只读不写（知识库回流除外），采纳后的落库仍走草稿工作区整体保存；
全部建议进入人工确认队列，不自动生效。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ontologies.access import ontology_access_guard
from app.ontologies.mappings import suggestion_service


router = APIRouter(dependencies=[Depends(ontology_access_guard)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class MappingSuggestionRequest(BaseModel):
    datasetIds: list[str] = Field(min_length=1, max_length=50)


@router.post("/{ontology_id}/versions/{version_id}/mapping-suggestions")
def suggest_version_mappings(
    ontology_id: str,
    version_id: str,
    body: MappingSuggestionRequest,
    db: Session = Depends(get_db),
):
    return suggestion_service.generate_mapping_suggestions(
        db, ontology_id, version_id, body.datasetIds)
