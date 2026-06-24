"""
Pydantic schemas for request/response validation.
These define the API contract between frontend and backend.
"""

from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


# ──────────────────────────────────────────────
# Base Response
# ──────────────────────────────────────────────

class BaseResponse(BaseModel):
    """Base response wrapper."""
    success: bool = True
    message: Optional[str] = None


class PaginatedResponse(BaseResponse):
    """Paginated list response."""
    total: int = 0
    page: int = 1
    page_size: int = 20
    items: List[Any] = []


# ──────────────────────────────────────────────
# Domain Schemas
# ──────────────────────────────────────────────

class DomainCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None


class DomainUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class DomainOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────
# ObjectType Schemas
# ──────────────────────────────────────────────

class PropertyTypeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    data_type: Literal["string", "integer", "float", "boolean", "date", "enum", "text"] = "string"
    enum_values: Optional[List[str]] = None
    unit: Optional[str] = None
    is_required: bool = False
    is_unique: bool = False
    default_value: Optional[str] = None
    display_order: int = 0


class PropertyTypeUpdate(PropertyTypeCreate):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    data_type: Optional[Literal["string", "integer", "float", "boolean", "date", "enum", "text"]] = None


class PropertyTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    object_type_id: str
    name: str
    description: Optional[str]
    data_type: str
    enum_values: Optional[List[str]]
    unit: Optional[str]
    is_required: bool
    is_unique: bool
    default_value: Optional[str]
    display_order: int
    created_at: datetime


class ObjectTypeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    color: Optional[str] = "#3b82f6"
    icon: Optional[str] = "box"
    properties: Optional[List[PropertyTypeCreate]] = []


class ObjectTypeUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    is_active: Optional[bool] = None


class ObjectTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    domain_id: str
    name: str
    description: Optional[str]
    color: str
    icon: str
    is_active: bool
    version: int
    properties: List[PropertyTypeOut] = []
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────
# RelationType Schemas
# ──────────────────────────────────────────────

class RelationTypeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    source_type_id: str
    target_type_id: str
    is_directed: bool = True
    cardinality: Literal["one_to_one", "one_to_many", "many_to_many"] = "many_to_many"


class RelationTypeUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    is_directed: Optional[bool] = None
    cardinality: Optional[Literal["one_to_one", "one_to_many", "many_to_many"]] = None
    is_active: Optional[bool] = None


class RelationTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    domain_id: str
    name: str
    description: Optional[str]
    source_type_id: str
    target_type_id: str
    source_type_name: Optional[str] = None
    target_type_name: Optional[str] = None
    is_directed: bool
    cardinality: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────
# Rule Schemas
# ──────────────────────────────────────────────

class RuleCondition(BaseModel):
    """A condition pattern for matching in the graph."""
    pattern: str = Field(..., description="Cypher-like pattern or description")
    parameters: Optional[Dict[str, Any]] = None


class RuleActionConfig(BaseModel):
    """Configuration for the rule action."""
    message_template: Optional[str] = None
    severity: Optional[str] = "medium"
    assign_to: Optional[str] = None
    tags: Optional[List[str]] = None


class RuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    condition: RuleCondition
    action_type: Literal["emit_alert", "create_task", "set_property", "require_approval"] = "emit_alert"
    action_config: Optional[RuleActionConfig] = None
    priority: int = 0


class RuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    condition: Optional[RuleCondition] = None
    action_type: Optional[str] = None
    action_config: Optional[RuleActionConfig] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None
    is_draft: Optional[bool] = None


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    domain_id: str
    name: str
    description: Optional[str]
    condition: Dict[str, Any]
    action_type: str
    action_config: Optional[Dict[str, Any]]
    priority: int
    is_active: bool
    is_draft: bool
    version: int
    hit_count: int
    false_positive_count: int
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────
# Mapping Schemas
# ──────────────────────────────────────────────

class FieldMapping(BaseModel):
    """A single field-to-property mapping."""
    source_field: str
    target_property: str
    transform: Optional[str] = None  # e.g., "uppercase", "parse_date"
    required: bool = False


class EntityResolutionRule(BaseModel):
    """Rule for determining if two entities are the same."""
    match_fields: List[str]
    threshold: float = 0.9
    strategy: str = "exact"  # exact, fuzzy, composite


class MappingCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    source_type: str = Field(..., description="e.g., 'csv', 'api', 'database'")
    source_config: Optional[Dict[str, Any]] = None
    field_mappings: List[FieldMapping]
    entity_resolution_rules: Optional[List[EntityResolutionRule]] = []


class MappingUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    source_config: Optional[Dict[str, Any]] = None
    field_mappings: Optional[List[FieldMapping]] = None
    entity_resolution_rules: Optional[List[EntityResolutionRule]] = None
    is_active: Optional[bool] = None


class MappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    domain_id: str
    name: str
    description: Optional[str]
    source_type: str
    source_config: Optional[Dict[str, Any]]
    field_mappings: List[Dict[str, Any]]
    entity_resolution_rules: Optional[List[Dict[str, Any]]]
    is_active: bool
    last_sync_at: Optional[datetime]
    sync_count: int
    error_count: int
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────
# Entity & Relation Instance Schemas
# ──────────────────────────────────────────────

class EntityCreate(BaseModel):
    object_type_id: str
    name: str = Field(..., min_length=1, max_length=200)
    properties: Optional[Dict[str, Any]] = {}
    confidence: Optional[float] = 1.0


class EntityUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    properties: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = None


class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    domain_id: str
    object_type_id: str
    object_type_name: Optional[str] = None
    name: str
    properties: Dict[str, Any]
    confidence: float
    is_verified: bool
    source_document_id: Optional[str]
    created_at: datetime
    updated_at: datetime


class RelationCreate(BaseModel):
    relation_type_id: str
    source_id: str
    target_id: str
    properties: Optional[Dict[str, Any]] = {}
    confidence: Optional[float] = 1.0


class RelationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    domain_id: str
    relation_type_id: str
    relation_type_name: Optional[str] = None
    source_id: str
    source_name: Optional[str] = None
    target_id: str
    target_name: Optional[str] = None
    properties: Dict[str, Any]
    confidence: float
    is_verified: bool
    created_at: datetime


# ──────────────────────────────────────────────
# Document & Extraction Schemas
# ──────────────────────────────────────────────

class DocumentCreate(BaseModel):
    domain_id: str
    filename: str
    original_filename: str
    mime_type: Optional[str] = None
    content_text: Optional[str] = None
    extraction_config: Optional[Dict[str, Any]] = None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    domain_id: str
    filename: str
    original_filename: str
    file_size: Optional[int]
    mime_type: Optional[str]
    content_text: Optional[str]
    content_summary: Optional[str]
    status: str
    extracted_entities_count: int
    extracted_relations_count: int
    reviewed_by: Optional[str]
    uploaded_by: Optional[str]
    created_at: datetime


class ExtractionResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    document_id: str
    result_type: str
    candidate_object_type_id: Optional[str]
    candidate_object_type_name: Optional[str] = None
    candidate_name: Optional[str]
    candidate_properties: Optional[Dict[str, Any]]
    candidate_relation_type_id: Optional[str]
    candidate_relation_type_name: Optional[str] = None
    candidate_source_name: Optional[str]
    candidate_target_name: Optional[str]
    confidence: Optional[float]
    llm_reasoning: Optional[str]
    status: str
    review_action: Optional[str]
    review_comment: Optional[str]
    created_at: datetime


class ExtractionReview(BaseModel):
    result_id: str
    action: Literal["approved", "rejected", "modified"] = "approved"
    modifications: Optional[Dict[str, Any]] = None
    comment: Optional[str] = None


# ──────────────────────────────────────────────
# Feedback Schemas
# ──────────────────────────────────────────────

class FeedbackCreate(BaseModel):
    domain_id: str
    feedback_type: Literal["extraction", "inference", "action", "rule_hit"]
    target_id: str
    target_type: str
    verdict: Literal["useful", "false_positive", "needs_correction", "skipped"]
    correction_data: Optional[Dict[str, Any]] = None
    context: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    domain_id: str
    feedback_type: str
    target_id: str
    target_type: str
    verdict: str
    correction_data: Optional[Dict[str, Any]]
    context: Optional[Dict[str, Any]]
    user_id: Optional[str]
    notes: Optional[str]
    created_at: datetime


# ──────────────────────────────────────────────
# Change Proposal Schemas
# ──────────────────────────────────────────────

class ChangeProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    domain_id: str
    target_type: str
    target_id: str
    proposal_type: str
    description: str
    current_value: Optional[Dict[str, Any]]
    proposed_value: Optional[Dict[str, Any]]
    severity: str
    confidence_score: Optional[float]
    sample_count: int
    status: str
    reviewed_by: Optional[str]
    review_comment: Optional[str]
    created_at: datetime


class ProposalReview(BaseModel):
    proposal_id: str
    action: Literal["approve", "reject"] = "approve"
    comment: Optional[str] = None


# ──────────────────────────────────────────────
# User Schemas
# ──────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: str = Field(..., max_length=200)
    display_name: Optional[str] = None
    role: Literal["admin", "domain_expert", "rule_maintainer", "reviewer"] = "reviewer"
    password: Optional[str] = None


class UserUpdate(BaseModel):
    email: Optional[str] = None
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    email: str
    display_name: Optional[str]
    role: str
    is_active: bool
    last_login_at: Optional[datetime]
    created_at: datetime


# ──────────────────────────────────────────────
# Audit Log Schemas
# ──────────────────────────────────────────────

class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    action: str
    resource_type: str
    resource_id: Optional[str]
    user_id: Optional[str]
    details: Optional[Dict[str, Any]]
    created_at: datetime


# ──────────────────────────────────────────────
# Dashboard / Stats Schemas
# ──────────────────────────────────────────────

class DomainStats(BaseModel):
    domain_id: str
    domain_name: str
    object_types_count: int
    relation_types_count: int
    rules_count: int
    rules_active_count: int
    entities_count: int
    relations_count: int
    documents_count: int
    documents_pending_review: int
    feedback_count: int
    proposals_pending: int


class DashboardStats(BaseModel):
    total_domains: int
    total_entities: int
    total_relations: int
    total_documents: int
    pending_reviews: int
    recent_feedback: List[FeedbackOut] = []
    domain_stats: List[DomainStats] = []


# ──────────────────────────────────────────────
# Graph Query Schemas
# ──────────────────────────────────────────────

class GraphQuery(BaseModel):
    query: str = Field(..., description="Natural language or Cypher-like query")
    domain_id: str
    limit: int = 100


class GraphNode(BaseModel):
    id: str
    label: str
    type: str
    type_id: str
    properties: Dict[str, Any]
    color: str


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str
    relation_type_id: str
    properties: Dict[str, Any]


class GraphData(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]


# ──────────────────────────────────────────────
# Inference / QA Schemas
# ──────────────────────────────────────────────

class InferenceRequest(BaseModel):
    domain_id: str
    query: str = Field(..., min_length=1)
    context_entity_ids: Optional[List[str]] = None
    use_rules: bool = True
    use_llm: bool = True


class InferenceResult(BaseModel):
    query: str
    answer: Optional[str] = None
    rule_hits: List[Dict[str, Any]] = []
    referenced_entities: List[str] = []
    confidence: float
    reasoning: Optional[str] = None


class ActionEmit(BaseModel):
    """An action emitted by the system."""
    action_type: str
    rule_id: Optional[str]
    entity_id: Optional[str]
    message: str
    severity: str
    created_at: datetime
