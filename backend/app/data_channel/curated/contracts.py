"""HTTP response contracts for curated datasets."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CuratedDatasetResponse(BaseModel):
    id: str
    name: str
    status: str
    # Canonical lake identity contract. Mapping clients must display this value
    # rather than asking users to define a second, conflicting key.
    primary_key: str = ""
    row_count: Optional[int] = None
    quality_score: Optional[float] = None
    producer_pipeline_id: Optional[str] = None
    output_key: Optional[str] = None
    has_review_evidence: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True
