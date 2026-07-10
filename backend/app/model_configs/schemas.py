from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional, List


def _single_model(value: Optional[List[str]]) -> Optional[List[str]]:
    """每个提供商仅允许配置一个模型：去空去重后只保留第一个。"""
    if value is None:
        return None
    cleaned = [str(m).strip() for m in value if str(m).strip()]
    return cleaned[:1]


class ModelConfigCreate(BaseModel):
    name: str
    config_type: str = "llm"
    provider: str  # llm: openai|anthropic|compatible; ocr: paddleocr|tesseract|external_api
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    models: List[str] = []
    options: dict = {}
    enabled: bool = True
    is_default: bool = False

    @field_validator("models", mode="before")
    @classmethod
    def _limit_models(cls, v):
        return _single_model(v) or []

class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    config_type: Optional[str] = None
    provider: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    models: Optional[List[str]] = None
    options: Optional[dict] = None
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None

    @field_validator("models", mode="before")
    @classmethod
    def _limit_models(cls, v):
        return _single_model(v)

class ModelConfigOut(BaseModel):
    id: str
    name: str
    config_type: str = "llm"
    provider: str
    api_base: Optional[str]
    models: List[str]
    options: dict = {}
    enabled: bool = True
    is_default: bool = False
    created_by: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}
