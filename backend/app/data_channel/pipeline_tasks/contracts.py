"""HTTP request contracts and shared literals for Pipeline Tasks."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


WRITE_MODES = ("overwrite", "append", "upsert", "append_dedup")
HistoryStatus = Literal[
    "pending",
    "running",
    "success",
    "failed",
    "cancelled",
]
HistoryTriggerType = Literal["manual", "scheduled"]


class PipelineTaskCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=1000)
    pipeline_id: str
    write_mode: Literal[
        "overwrite",
        "append",
        "upsert",
        "append_dedup",
    ] = "overwrite"
    # deprecated：只为兼容旧客户端；服务端只接受与流水线发布契约一致的值，
    # 最终始终从流水线契约派生。
    primary_key: Optional[str] = ""
    soft_delete_column: Optional[str] = ""
    skip_empty: bool = True
    schedule_type: Literal["MANUAL", "CRON", "INTERVAL"] = "MANUAL"
    cron_expression: Optional[str] = ""
    interval_seconds: Optional[int] = 0
    enabled: bool = True

    @field_validator("name", "description")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


class PipelineTaskUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(
        None,
        min_length=1,
        max_length=1000,
    )
    pipeline_id: Optional[str] = None
    write_mode: Optional[
        Literal["overwrite", "append", "upsert", "append_dedup"]
    ] = None
    primary_key: Optional[str] = None
    soft_delete_column: Optional[str] = None
    skip_empty: Optional[bool] = None
    schedule_type: Optional[
        Literal["MANUAL", "CRON", "INTERVAL"]
    ] = None
    cron_expression: Optional[str] = None
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None

    @field_validator("name", "description")
    @classmethod
    def required_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value
