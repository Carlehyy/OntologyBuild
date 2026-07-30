"""HTTP request contracts for the Data Steward API.

The router re-exports these models so existing imports and FastAPI schema names
remain stable while request validation stays separate from orchestration.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ChatBody(BaseModel):
    message: str
    conversationId: Optional[str] = None
    modelId: Optional[str] = None
    targetRecordId: Optional[str] = None
    webSearch: bool = False
    stream: bool = True


class CreateConversationBody(BaseModel):
    title: str = "新对话"


class BrowserUrlBody(BaseModel):
    url: str


class BrowserClickBody(BaseModel):
    text: str


class BrowserTypeBody(BaseModel):
    selector: str
    text: str
    pressEnter: bool = False


class CreateBrowserSourceBody(BaseModel):
    name: str
    sourceType: str
    endpointUrl: str | None = None
    headers: dict[str, str] | None = None


class UpdateBrowserSourceBody(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    endpointUrl: str | None = None
    headers: dict[str, str] | None = None


class BindBrowserSourceBody(BaseModel):
    sourceId: str | None = None


class BrowserLiveLeaseBody(BaseModel):
    leaseId: str


class BrowserLiveInputBody(BrowserLiveLeaseBody):
    message: dict[str, object]


class BrowserLiveControlBody(BrowserLiveLeaseBody):
    action: str


class BootstrapBody(BaseModel):
    name: str
    description: str = ""


__all__ = [
    "BindBrowserSourceBody",
    "BootstrapBody",
    "BrowserClickBody",
    "BrowserLiveControlBody",
    "BrowserLiveInputBody",
    "BrowserLiveLeaseBody",
    "BrowserTypeBody",
    "BrowserUrlBody",
    "ChatBody",
    "CreateBrowserSourceBody",
    "CreateConversationBody",
    "UpdateBrowserSourceBody",
]
