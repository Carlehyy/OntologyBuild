"""决策推演 API 契约。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field, field_validator

from app.ontologies.formal_modeling.schemas import CamelModel


class DecisionSimulationRequest(CamelModel):
    question: str = Field(min_length=8, max_length=4000)
    alternatives: list[str] = Field(default_factory=list, max_length=6)
    horizon: Optional[str] = Field(default=None, max_length=200)
    conversation_id: Optional[str] = None
    model_id: Optional[str] = None
    release_id: Optional[str] = None

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        return value.strip()

    @field_validator("alternatives")
    @classmethod
    def _normalize_alternatives(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw or "").strip()[:160]
            if value and value not in seen:
                out.append(value)
                seen.add(value)
        if out and len(out) < 2:
            raise ValueError("提供 alternatives 时至少需要两个互斥方案")
        return out


class DecisionSimulationSummaryOut(CamelModel):
    id: str
    ontology_id: str
    ontology_release_id: Optional[str] = None
    conversation_id: Optional[str] = None
    title: str
    question: str
    status: str
    model_name: Optional[str] = None
    recommended_option: Optional[str] = None
    robust_score: Optional[float] = None
    perspective_count: int = 0
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None

class DecisionSimulationOut(CamelModel):
    id: str
    ontology_id: str
    ontology_release_id: Optional[str] = None
    conversation_id: Optional[str] = None
    created_by: str
    model_config_id: Optional[str] = None
    model_name: Optional[str] = None
    title: str
    question: str
    status: str
    specification: dict[str, Any] = Field(default_factory=dict)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    perspectives: list[dict[str, Any]] = Field(default_factory=list)
    evaluation: dict[str, Any] = Field(default_factory=dict)
    recommendation: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
