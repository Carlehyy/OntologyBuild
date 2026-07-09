"""领域设置 — Pydantic schemas"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class DomainCreate(BaseModel):
    name: str
    description: str = ""


class DomainUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class DomainOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    created_by: str
    created_at: datetime
    updated_at: datetime
