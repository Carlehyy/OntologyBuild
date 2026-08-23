"""三维场景 — Pydantic 入参模型（world_model 风格：plain BaseModel）。
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SceneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    icon: str = Field(default="boxes", max_length=40)
    definition: Optional[dict] = None


class SceneUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    icon: Optional[str] = Field(default=None, max_length=40)


class SceneDefinitionSave(BaseModel):
    definition: dict
    note: str = ""


class RuntimeLogEntry(BaseModel):
    level: str = "info"
    object_id: Optional[str] = Field(default=None, max_length=80)
    event_key: str = Field(default="", max_length=80)
    message: str = Field(min_length=1, max_length=2000)
    payload: Optional[dict] = None
    occurred_at: Optional[datetime] = None


class RuntimeLogAppend(BaseModel):
    entries: list[RuntimeLogEntry]


class SceneConversationCreate(BaseModel):
    scene_id: Optional[str] = None
    title: str = Field(default="", max_length=200)
    model_config_id: Optional[str] = None


class SceneChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    model_config_id: Optional[str] = None
