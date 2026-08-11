from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional


def _normalize_build_mode(value: Optional[str]) -> Optional[str]:
    """Map the retired document-extraction mode to manual construction."""
    if value is None:
        return None
    normalized = value.strip()
    return "manual" if normalized == "simple_llm" else normalized


class OntologyCreate(BaseModel):
    name: str = Field(max_length=200)
    domain: str = Field(max_length=100)
    description: Optional[str] = None
    icon: Optional[str] = Field(default="network", max_length=50)
    # Retained only for older API clients. The management UI no longer asks
    # users to choose a construction mode.
    build_mode: Optional[str] = None

    @field_validator("name", "domain")
    @classmethod
    def validate_required_text(cls, v: str):
        value = v.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("icon")
    @classmethod
    def normalize_icon(cls, v: Optional[str]):
        return v.strip() if v and v.strip() else "network"

    @field_validator("build_mode")
    @classmethod
    def normalize_build_mode(cls, v: Optional[str]):
        return _normalize_build_mode(v)


class OntologyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    domain: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    icon: Optional[str] = Field(default=None, max_length=50)
    # status/version are controlled exclusively by the version publish and
    # rollback state machine; generic PUT must not forge release identity.
    build_mode: Optional[str] = None

    @field_validator("name", "domain")
    @classmethod
    def validate_optional_required_text(cls, v: Optional[str]):
        if v is None:
            return v
        value = v.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("build_mode")
    @classmethod
    def normalize_build_mode(cls, v: Optional[str]):
        return _normalize_build_mode(v)


class OntologyOut(BaseModel):
    id: str
    name: str
    domain: str
    description: Optional[str]
    icon: Optional[str] = "network"
    version: str
    current_release_id: Optional[str] = None
    current_release_version: Optional[str] = None
    status: str
    build_mode: Optional[str] = "manual"
    created_by: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class OntologyListItem(BaseModel):
    id: str
    name: str
    domain: str
    description: Optional[str] = None
    icon: Optional[str] = "network"
    version: str
    current_release_id: Optional[str] = None
    current_release_version: Optional[str] = None
    status: str
    build_mode: Optional[str] = "manual"
    entity_count: int = 0
    relation_count: int = 0
    action_count: int = 0
    sentinel_count: int = 0
    assistant_card_clicks: int = 0
    created_by: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}
