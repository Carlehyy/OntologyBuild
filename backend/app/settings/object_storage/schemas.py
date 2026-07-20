from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class MinioConfigResponse(BaseModel):
    enabled: bool = False
    endpoint: str = ""
    secure: bool = False
    region: str = "us-east-1"
    default_bucket: str = "openontology"
    has_access_key: bool = False
    has_secret_key: bool = False
    read_enabled: bool = True
    write_enabled: bool = True
    delete_enabled: bool = False
    mcp_enabled: bool = True
    has_mcp_token: bool = False
    mcp_token_hint: str = ""
    connected: bool = False
    last_test_status: str | None = None
    last_test_message: str | None = None
    last_tested_at: datetime | None = None
    mcp_path: str = "/mcp/minio"


class MinioConnectionTestRequest(BaseModel):
    enabled: bool = True
    endpoint: str = Field(min_length=1, max_length=500)
    secure: bool = False
    region: str = Field(default="us-east-1", max_length=100)
    default_bucket: str = Field(default="openontology", min_length=3, max_length=63)
    access_key: str = Field(default="", max_length=500)
    secret_key: str = Field(default="", max_length=2000)
    read_enabled: bool = True
    write_enabled: bool = True
    delete_enabled: bool = False
    mcp_enabled: bool = True
    create_default_bucket: bool = True
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class MinioConnectionTestResponse(BaseModel):
    ok: bool
    message: str
    endpoint: str = ""
    bucket_count: int = 0
    default_bucket_ready: bool = False
    mcp_path: str = "/mcp/minio"
    mcp_token: str | None = None


class MinioTokenResponse(BaseModel):
    token: str
    token_hint: str
    mcp_path: str = "/mcp/minio"


class BucketCreateRequest(BaseModel):
    bucket: str = Field(min_length=3, max_length=63)
    region: str | None = Field(default=None, max_length=100)


class TextObjectUploadRequest(BaseModel):
    bucket: str = Field(min_length=3, max_length=63)
    key: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=2_000_000)
    content_type: str = Field(default="text/plain; charset=utf-8", max_length=200)


class ObjectTransferRequest(BaseModel):
    source_bucket: str = Field(min_length=3, max_length=63)
    source_key: str = Field(min_length=1, max_length=1024)
    destination_bucket: str = Field(min_length=3, max_length=63)
    destination_key: str = Field(min_length=1, max_length=1024)


class PresignRequest(BaseModel):
    bucket: str = Field(min_length=3, max_length=63)
    key: str = Field(min_length=1, max_length=1024)
    method: Literal["GET", "PUT"] = "GET"
    expires_seconds: int = Field(default=3600, ge=60, le=604800)


class OperationAuditOut(BaseModel):
    id: str
    actor_type: str
    actor_id: str | None
    operation: str
    bucket: str | None
    object_key: str | None
    success: bool
    details: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}
