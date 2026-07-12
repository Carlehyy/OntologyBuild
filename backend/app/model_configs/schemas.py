from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator
from datetime import datetime
from typing import Optional, List


def _single_model(value: Optional[List[str]]) -> Optional[List[str]]:
    """每个提供商仅允许配置一个模型：去空去重后只保留第一个。"""
    if value is None:
        return None
    cleaned = [str(m).strip() for m in value if str(m).strip()]
    return cleaned[:1]


def _trimmed(value: str) -> str:
    return value.strip()


def _validate_api_base(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        raise ValueError("API Base 必须是以 http:// 或 https:// 开头的绝对地址")
    return value.rstrip("/")


class ModelConfigCreate(BaseModel):
    name: str
    config_type: str = "llm"
    provider: str  # llm: openai|anthropic|compatible; ocr: paddleocr|tesseract|external_api
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    models: List[str] = Field(default_factory=list)
    options: dict = Field(default_factory=dict)
    enabled: bool = False
    is_default: bool = False

    @field_validator("name", "provider")
    @classmethod
    def _strip_required_text(cls, v):
        value = _trimmed(v)
        if not value:
            raise ValueError("字段不能为空")
        return value

    @field_validator("api_base")
    @classmethod
    def _api_base_must_be_absolute(cls, v):
        return _validate_api_base(v)

    @field_validator("models", mode="before")
    @classmethod
    def _limit_models(cls, v):
        return _single_model(v) or []

    @model_validator(mode="after")
    def _llm_requires_model(self):
        if self.config_type == "llm" and not self.models:
            raise ValueError("LLM 配置必须填写模型名")
        if self.is_default:
            raise ValueError("新配置不能直接设为默认，请先测试并启用")
        return self

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

    @field_validator("name", "provider")
    @classmethod
    def _strip_optional_required_text(cls, v):
        if v is None:
            return None
        value = _trimmed(v)
        if not value:
            raise ValueError("字段不能为空")
        return value

    @field_validator("api_base")
    @classmethod
    def _api_base_must_be_absolute(cls, v):
        return _validate_api_base(v)

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
    options: dict = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False
    last_test_status: Optional[str] = None
    last_tested_at: Optional[datetime] = None
    last_test_message: Optional[str] = None
    created_by: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class ModelEnabledRequest(BaseModel):
    enabled: StrictBool


class ModelConfigImportItem(BaseModel):
    name: str
    config_type: str = "llm"
    provider: str
    api_base: Optional[str] = None
    models: List[str] = Field(default_factory=list)
    options: dict = Field(default_factory=dict)
    enabled: bool = False
    is_default: bool = False

    @field_validator("name", "provider")
    @classmethod
    def _strip_required_text(cls, v):
        value = _trimmed(v)
        if not value:
            raise ValueError("字段不能为空")
        return value

    @field_validator("api_base")
    @classmethod
    def _api_base_must_be_absolute(cls, v):
        return _validate_api_base(v)

    @field_validator("models", mode="before")
    @classmethod
    def _limit_models(cls, v):
        return _single_model(v) or []

    @model_validator(mode="after")
    def _llm_requires_model(self):
        if self.config_type == "llm" and not self.models:
            raise ValueError("LLM 配置必须填写模型名")
        return self


class ModelConfigImportRequest(BaseModel):
    configs: List[ModelConfigImportItem]

    @field_validator("configs")
    @classmethod
    def _reasonable_batch_size(cls, v):
        if not v:
            raise ValueError("导入文件中没有模型配置")
        if len(v) > 100:
            raise ValueError("单次最多导入 100 条模型配置")
        return v
