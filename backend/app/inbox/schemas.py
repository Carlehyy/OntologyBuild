from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ContractModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)


class InboxSource(ContractModel):
    system: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=120)
    id: str = Field(min_length=1, max_length=200)
    occurrence_id: str | None = Field(default=None, max_length=200)
    correlation_key: str = Field(min_length=1, max_length=300)


class InboxContent(ContractModel):
    kind: Literal["task", "alert", "notice"]
    priority: Literal["urgent", "high", "normal", "low"] = "normal"
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default="", max_length=4000)
    safe_context: dict = Field(default_factory=dict)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_safe_context_size(self):
        encoded = json.dumps(self.safe_context, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("safeContext must not exceed 16 KiB")
        return self


class InboxResource(ContractModel):
    type: str = Field(min_length=1, max_length=120)
    id: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=300)
    href: str = Field(min_length=1, max_length=1000)

    @field_validator("href")
    @classmethod
    def internal_href(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("href must be an internal absolute path")
        return value


class InboxAction(ContractModel):
    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=100)
    mode: Literal["navigate"] = "navigate"
    href: str = Field(min_length=1, max_length=1000)

    @field_validator("href")
    @classmethod
    def internal_href(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("href must be an internal absolute path")
        return value


class InboxAudience(ContractModel):
    type: Literal["user", "users", "role"]
    user_id: str | None = None
    user_ids: list[str] = Field(default_factory=list, max_length=100)
    role: str | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.type == "user" and not self.user_id:
            raise ValueError("user audience requires userId")
        if self.type == "users" and not self.user_ids:
            raise ValueError("users audience requires userIds")
        if self.type == "role" and not self.role:
            raise ValueError("role audience requires role")
        return self


class InboxResolution(ContractModel):
    state: Literal["resolved", "cancelled", "expired"] = "resolved"
    reason: str = Field(default="", max_length=120)


class InboxEventIn(ContractModel):
    schema_version: Literal["v1"] = "v1"
    event_id: str = Field(min_length=1, max_length=255)
    occurred_at: datetime
    operation: Literal["append", "upsert", "close"]
    source: InboxSource
    item: InboxContent | None = None
    resource: InboxResource | None = None
    audience: InboxAudience | None = None
    actions: list[InboxAction] = Field(default_factory=list, max_length=8)
    resolution: InboxResolution | None = None

    @model_validator(mode="after")
    def validate_operation(self):
        if self.operation in {"append", "upsert"}:
            if not self.item or not self.resource or not self.audience:
                raise ValueError("append/upsert require item, resource and audience")
        elif not self.resolution:
            raise ValueError("close requires resolution")
        return self


class DeliveryStateUpdate(BaseModel):
    state: Literal["read", "unread", "archived"]
