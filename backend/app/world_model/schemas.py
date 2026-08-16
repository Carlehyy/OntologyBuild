"""世界模型 API 的请求/响应模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EngineType = Literal["statistical", "mechanistic", "state_machine", "learned"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    engine_type: EngineType = "statistical"


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    engine_type: EngineType | None = None


class ProjectSummary(BaseModel):
    id: str
    name: str
    description: str
    engine_type: str
    status: str
    version_count: int = 0
    created_at: datetime | None
    updated_at: datetime | None


class ProjectListResponse(BaseModel):
    items: list[ProjectSummary]
    total: int


class ProjectDetail(ProjectSummary):
    script: str


class ScriptExecuteRequest(BaseModel):
    """调试执行：脚本 + 测试入参。不落库。"""
    script: str = Field(min_length=1)
    test_input: dict[str, Any] = Field(default_factory=dict)


class ScriptSaveRequest(ScriptExecuteRequest):
    """保存：服务端重新执行复核，通过才落库并冻结版本（双重保障）。"""


class ScriptExecutionResult(BaseModel):
    ok: bool
    payload: Any = None            # simulate() 的返回值（JSON）
    stdout: str = ""
    error: str | None = None
    traceback: str = ""
    duration_ms: int = 0
    kernel_id: str = ""


class ScriptSaveResult(BaseModel):
    ok: bool
    execution: ScriptExecutionResult
    version_no: int | None = None


class ScriptVersionItem(BaseModel):
    id: str
    version_no: int
    test_input: dict[str, Any] | None
    duration_ms: int
    created_by: str | None
    created_at: datetime | None


class ScriptVersionDetail(ScriptVersionItem):
    script: str


class CallRecordItem(BaseModel):
    id: str
    project_id: str | None
    service_name: str
    caller: str
    ok: bool
    duration_ms: int
    error: str | None
    created_at: datetime | None


class CallRecordListResponse(BaseModel):
    items: list[CallRecordItem]
    total: int


class CallRecordDetail(CallRecordItem):
    request_payload: dict[str, Any] | None
    response_payload: dict[str, Any] | None


class CallRecordOverview(BaseModel):
    total: int = 0
    failed: int = 0
    avg_duration_ms: int = 0


# ---------- 推演服务（发布 / 状态 / 调用） ----------


class PreconditionItem(BaseModel):
    """前置条件（最小结构化表达）：某类对象的事实数量下限。"""
    object_type_id: str = Field(min_length=1, max_length=100)
    min_count: int = Field(default=1, ge=1)


class ServicePublishRequest(BaseModel):
    """发布为推演服务：选定冻结版本 + 本体语义注册。"""
    version_id: str | None = None  # 缺省发布最新冻结版本
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    applicable_ontology_id: str = Field(min_length=1)
    applicable_object_type_ids: list[str] = Field(min_length=1)
    preconditions: list[PreconditionItem] = Field(default_factory=list)


class ServiceStatusRequest(BaseModel):
    status: Literal["online", "offline"]


class ServiceOut(BaseModel):
    id: str
    project_id: str
    version_id: str | None
    version_no: int | None = None
    name: str
    description: str
    status: str
    endpoint_path: str | None
    applicable_object_types: dict[str, Any] | None
    preconditions: list[dict[str, Any]] | None
    created_at: datetime | None
    updated_at: datetime | None


class InvokeRequest(BaseModel):
    """调用推演服务：与 simulate(context, actions, horizon) 契约对齐。"""
    context: dict[str, Any] = Field(default_factory=dict)
    actions: list[Any] = Field(default_factory=list)
    horizon: int = Field(default=1, ge=0)


class InvokeResult(BaseModel):
    ok: bool
    payload: Any = None
    error: str | None = None
    duration_ms: int = 0
    call_id: str | None = None


class TemplateOut(BaseModel):
    """开发页可插入的官方脚本模板（唯一事实源在后端，前端不复制副本）。"""
    key: str
    name: str
    description: str
    script: str
    test_input: dict[str, Any]
