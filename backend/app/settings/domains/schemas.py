"""领域设置 — Pydantic schemas"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("must not be empty")
    return normalized


class DomainCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_name(value)


class DomainUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_name(value) if value is not None else None


class DomainOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    created_by: str
    created_at: datetime
    updated_at: datetime
