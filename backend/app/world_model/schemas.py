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
    # 推演服务状态（null=未发布）；列表徽标据此展示 草稿/在线/已下线
    service_status: str | None = None
    # 已发布服务的摘要（null=未发布）：列表卡片的服务快捷入口与删除影响提示
    service_name: str | None = None
    service_endpoint: str | None = None
    service_version_no: int | None = None
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


class CallRecordDailyBucket(BaseModel):
    """按日分桶的调用统计（调用记录页趋势图数据源，缺失日期补零）。"""
    date: str  # YYYY-MM-DD（UTC 日）
    total: int = 0
    failed: int = 0
    avg_duration_ms: int = 0


class ServiceOverview(BaseModel):
    """推演服务页概览统计（全局聚合，与分页/筛选条件无关）。"""
    total: int = 0
    online: int = 0
    offline: int = 0
    call_total: int = 0
    call_failed: int = 0
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


class ServiceSummary(BaseModel):
    """推演服务注册表条目（跨项目列表）。"""
    id: str
    project_id: str
    project_name: str
    version_id: str | None
    version_no: int | None = None
    name: str
    description: str
    status: str
    endpoint_path: str | None
    applicable_object_types: dict[str, Any] | None
    preconditions: list[dict[str, Any]] | None
    call_count: int = 0
    failed_count: int = 0
    created_at: datetime | None
    updated_at: datetime | None


class ServiceListResponse(BaseModel):
    items: list[ServiceSummary]
    total: int
