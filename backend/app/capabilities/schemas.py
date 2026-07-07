"""能力注册中心 API Schemas — 对外 camelCase。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field

from app.ontologies.formal_modeling.schemas import CamelModel

VALID_SCOPES = {"exploration", "agent"}   # agent = 智能体工作台（P3 接入）


class SkillOut(CamelModel):
    id: str
    name: str
    display_name: str
    description: str = ""
    instructions: str = ""
    scopes: list[str] = Field(default_factory=list)
    enabled: bool = True
    builtin: bool = False
    created_at: datetime
    updated_at: datetime


class SkillCreate(CamelModel):
    name: str
    display_name: str
    description: str = ""
    instructions: str = ""
    scopes: list[str] = Field(default_factory=lambda: ["exploration"])
    enabled: bool = True


class SkillUpdate(CamelModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    instructions: Optional[str] = None
    scopes: Optional[list[str]] = None
    enabled: Optional[bool] = None
