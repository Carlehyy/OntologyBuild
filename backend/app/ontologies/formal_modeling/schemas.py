"""
正规本体模型 Pydantic Schemas

前端 (palantir-graph/types/ontology.ts) 用 camelCase，后端 DB 用 snake_case。
这里用 alias + populate_by_name 让两边都能用，对外 JSON 输出统一 camelCase。
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, ConfigDict


def _to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# ============ ObjectType ============
class ObjectTypeBase(CamelModel):
    name: str
    display_name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    primary_key: Optional[str] = None
    properties: list[dict[str, Any]] = Field(default_factory=list)
    interfaces: list[dict[str, Any]] = Field(default_factory=list)
    position_x: float = 0.0
    position_y: float = 0.0


class ObjectTypeCreate(ObjectTypeBase):
    pass


class ObjectTypeUpdate(CamelModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    primary_key: Optional[str] = None
    properties: Optional[list[dict[str, Any]]] = None
    interfaces: Optional[list[dict[str, Any]]] = None
    position_x: Optional[float] = None
    position_y: Optional[float] = None


class ObjectTypeOut(ObjectTypeBase):
    id: str
    source: Optional[dict[str, Any]] = None  # 血缘出处（如业务探索草稿）
    created_at: datetime
    updated_at: datetime


# ============ LinkType ============
class LinkTypeBase(CamelModel):
    name: str
    display_name: str
    description: Optional[str] = None
    source_object_type_id: str
    target_object_type_id: str
    cardinality: str = "one-to-many"
    source_role: Optional[str] = None
    target_role: Optional[str] = None
    properties: list[dict[str, Any]] = Field(default_factory=list)


class LinkTypeCreate(LinkTypeBase):
    pass


class LinkTypeUpdate(CamelModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    source_object_type_id: Optional[str] = None
    target_object_type_id: Optional[str] = None
    cardinality: Optional[str] = None
    source_role: Optional[str] = None
    target_role: Optional[str] = None
    properties: Optional[list[dict[str, Any]]] = None


class LinkTypeOut(LinkTypeBase):
    id: str
    source: Optional[dict[str, Any]] = None  # 血缘出处（如业务探索草稿）
    created_at: datetime
    updated_at: datetime


# ============ ActionType ============
class ActionTypeBase(CamelModel):
    name: str
    display_name: str
    description: Optional[str] = None
    object_type_id: Optional[str] = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)
    validation_function_id: Optional[str] = None
    # HITL：真实执行需人工批准（哨兵触发的执行同样挂起等待审批）
    requires_approval: bool = False


class ActionTypeCreate(ActionTypeBase):
    pass


class ActionTypeUpdate(CamelModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    object_type_id: Optional[str] = None
    parameters: Optional[list[dict[str, Any]]] = None
    rules: Optional[list[dict[str, Any]]] = None
    validation_function_id: Optional[str] = None
    requires_approval: Optional[bool] = None


class ActionTypeOut(ActionTypeBase):
    id: str
    source: Optional[dict[str, Any]] = None  # 血缘出处（如业务探索草稿）
    created_at: datetime
    updated_at: datetime


# ============ Function ============
class FunctionBase(CamelModel):
    name: str
    display_name: str
    description: Optional[str] = None
    function_type: str = "query"
    language: str = "expression"
    target_object_type_id: Optional[str] = None
    target_action_id: Optional[str] = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    return_type: str = "string"
    body: str = ""
    cache_strategy: Optional[str] = None
    cache_ttl: Optional[int] = None
    enabled: bool = True


class FunctionCreate(FunctionBase):
    pass


class FunctionUpdate(CamelModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    function_type: Optional[str] = None
    language: Optional[str] = None
    target_object_type_id: Optional[str] = None
    target_action_id: Optional[str] = None
    parameters: Optional[list[dict[str, Any]]] = None
    return_type: Optional[str] = None
    body: Optional[str] = None
    cache_strategy: Optional[str] = None
    cache_ttl: Optional[int] = None
    enabled: Optional[bool] = None


class FunctionOut(FunctionBase):
    id: str
    source: Optional[dict[str, Any]] = None  # 血缘出处（如业务探索草稿）
    created_at: datetime
    updated_at: datetime


# ============ ObjectInstance ============
class ObjectInstanceBase(CamelModel):
    object_type_id: str
    properties: dict[str, Any] = Field(default_factory=dict)
    computed: dict[str, Any] = Field(default_factory=dict)
    source: Optional[str] = "manual"
    external_id: Optional[str] = None


class ObjectInstanceCreate(ObjectInstanceBase):
    id: Optional[str] = None


class ObjectInstanceUpdate(CamelModel):
    properties: Optional[dict[str, Any]] = None
    computed: Optional[dict[str, Any]] = None
    source: Optional[str] = None
    external_id: Optional[str] = None


class ObjectInstanceOut(ObjectInstanceBase):
    id: str
    created_at: datetime
    updated_at: datetime


# ============ LinkInstance ============
class LinkInstanceBase(CamelModel):
    link_type_id: str
    source_object_id: str
    target_object_id: str
    properties: dict[str, Any] = Field(default_factory=dict)


class LinkInstanceCreate(LinkInstanceBase):
    pass


class LinkInstanceOut(LinkInstanceBase):
    id: str
    created_at: datetime


# ============ ActionExecutionLog ============
class ActionLogOut(CamelModel):
    id: str
    action_id: str
    action_name: Optional[str] = None
    object_type_id: Optional[str] = None
    object_instance_id: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: str
    validation_errors: list[str] = Field(default_factory=list)
    effects: list[dict[str, Any]] = Field(default_factory=list)
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None
    dry_run: bool = False
    executed_at: datetime
    # 溯源 + HITL 审批
    actor_id: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_reason: Optional[str] = None
    related_log_id: Optional[str] = None


class DecisionRequest(CamelModel):
    """HITL 审批请求：approved | rejected，可附理由（理由进入决策事实）。"""
    decision: str
    reason: Optional[str] = None


# ============ 整体本体导出 (供图谱编辑页一次性加载) ============
class FullOntologyOut(CamelModel):
    id: str
    name: str
    description: Optional[str] = None
    version: str = "1.0.0"
    # 乐观并发修订戳：客户端保存时原样带回 base_revision，服务端比对检测并发冲突
    revision: Optional[str] = None
    object_types: list[ObjectTypeOut] = Field(default_factory=list)
    link_types: list[LinkTypeOut] = Field(default_factory=list)
    actions: list[ActionTypeOut] = Field(default_factory=list)
    functions: list[FunctionOut] = Field(default_factory=list)
    instances: list[ObjectInstanceOut] = Field(default_factory=list)
    link_instances: list[LinkInstanceOut] = Field(default_factory=list)
    execution_logs: list[ActionLogOut] = Field(default_factory=list)


# ============ 整体保存 (图谱编辑页一次性回写) ============
class _SaveObjectType(ObjectTypeBase):
    id: Optional[str] = None


class _SaveLinkType(LinkTypeBase):
    id: Optional[str] = None


class _SaveAction(ActionTypeBase):
    id: Optional[str] = None


class _SaveFunction(FunctionBase):
    id: Optional[str] = None


class _SaveObjectInstance(ObjectInstanceBase):
    id: Optional[str] = None


class _SaveLinkInstance(LinkInstanceBase):
    id: Optional[str] = None


class SaveFullOntologyRequest(CamelModel):
    """整体回写：用前端图谱编辑页当前状态替换该本体下的全部建模数据。

    只替换建模与实例数据，不动 OntologyProject 元信息（name/domain 等）。
    执行日志 (execution_logs) 不在此回写——由 run-action 落库。
    """
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    # 加载时拿到的 revision；不一致时后端返回 409（None = 跳过检测，兼容旧客户端）
    base_revision: Optional[str] = None
    object_types: list[_SaveObjectType] = Field(default_factory=list)
    link_types: list[_SaveLinkType] = Field(default_factory=list)
    actions: list[_SaveAction] = Field(default_factory=list)
    functions: list[_SaveFunction] = Field(default_factory=list)
    instances: list[_SaveObjectInstance] = Field(default_factory=list)
    link_instances: list[_SaveLinkInstance] = Field(default_factory=list)


# ============ 增量保存 (图谱编辑页 delta 回写) ============
class DeltaUpserts(CamelModel):
    object_types: list[_SaveObjectType] = Field(default_factory=list)
    link_types: list[_SaveLinkType] = Field(default_factory=list)
    actions: list[_SaveAction] = Field(default_factory=list)
    functions: list[_SaveFunction] = Field(default_factory=list)
    instances: list[_SaveObjectInstance] = Field(default_factory=list)
    link_instances: list[_SaveLinkInstance] = Field(default_factory=list)


class DeltaDeletes(CamelModel):
    object_types: list[str] = Field(default_factory=list)
    link_types: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    instances: list[str] = Field(default_factory=list)
    link_instances: list[str] = Field(default_factory=list)


class PatchOntologyRequest(CamelModel):
    """增量回写：只提交自上次保存以来变更/删除的实体。

    与 PUT /full 等价的完整机制（并发检测 / 强制校验 / 事实追加 /
    派生重算 / 哨兵评估）都会执行，但负载从 O(全模型) 降到 O(变更)。
    """
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    base_revision: Optional[str] = None
    upserts: DeltaUpserts = Field(default_factory=DeltaUpserts)
    deletes: DeltaDeletes = Field(default_factory=DeltaDeletes)


# ============ 运行时执行请求 ============
class RunActionRequest(CamelModel):
    action_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    target_instance_id: Optional[str] = None
    dry_run: bool = False


class TestFunctionRequest(CamelModel):
    function_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    object_instance_id: Optional[str] = None
    object_props: Optional[dict[str, Any]] = None
