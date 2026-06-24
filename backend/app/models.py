"""
Core data models for the Ontology-Graph-AI Framework.

All domain knowledge lives in these tables, not in code.
This is the physical foundation for self-evolution.
"""

import uuid
import enum
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, DateTime, ForeignKey, Integer, Float,
    Boolean, JSON, Enum, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship, validates
from app.database import Base


def generate_uuid() -> str:
    """Generate a unique identifier."""
    return str(uuid.uuid4())


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────

class RuleActionType(str, enum.Enum):
    """Types of actions a rule can trigger."""
    EMIT_ALERT = "emit_alert"
    CREATE_TASK = "create_task"
    SET_PROPERTY = "set_property"
    REQUIRE_APPROVAL = "require_approval"


class FeedbackType(str, enum.Enum):
    """Types of user feedback."""
    USEFUL = "useful"
    FALSE_POSITIVE = "false_positive"
    NEEDS_CORRECTION = "needs_correction"
    SKIPPED = "skipped"


class ChangeSeverity(str, enum.Enum):
    """Severity levels for proposed changes."""
    LOW = "low"           # Can be auto-applied
    MEDIUM = "medium"     # Suggest to user
    HIGH = "high"         # Require approval
    CRITICAL = "critical" # Always require approval


class ApprovalStatus(str, enum.Enum):
    """Approval status for change proposals."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPLIED = "auto_applied"


class ExtractionStatus(str, enum.Enum):
    """Status of a document extraction task."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REVIEWED = "reviewed"


class UserRole(str, enum.Enum):
    """User roles for RBAC."""
    ADMIN = "admin"
    DOMAIN_EXPERT = "domain_expert"
    RULE_MAINTAINER = "rule_maintainer"
    REVIEWER = "reviewer"


# ──────────────────────────────────────────────
# Core Configuration Models
# ──────────────────────────────────────────────

class Domain(Base):
    """A domain/tenant - all data is scoped to a domain."""
    __tablename__ = "domains"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    object_types = relationship("ObjectType", back_populates="domain", cascade="all, delete-orphan")
    relation_types = relationship("RelationType", back_populates="domain", cascade="all, delete-orphan")
    rules = relationship("Rule", back_populates="domain", cascade="all, delete-orphan")
    mappings = relationship("Mapping", back_populates="domain", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="domain", cascade="all, delete-orphan")
    entities = relationship("Entity", back_populates="domain", cascade="all, delete-orphan")


class ObjectType(Base):
    """
    A type of object in the ontology (e.g., 'Person', 'Organization', 'Product').
    This is schema-level configuration, not instance data.
    """
    __tablename__ = "object_types"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    domain_id = Column(String(36), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    color = Column(String(7), default="#3b82f6")
    icon = Column(String(50), default="box")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    version = Column(Integer, default=1)

    # Relationships
    domain = relationship("Domain", back_populates="object_types")
    properties = relationship("PropertyType", back_populates="object_type", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("domain_id", "name", name="uq_object_type_domain_name"),
        Index("ix_object_types_domain", "domain_id"),
    )


class PropertyType(Base):
    """
    A property/attribute of an ObjectType.
    Defines the schema for entity properties.
    """
    __tablename__ = "property_types"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    object_type_id = Column(String(36), ForeignKey("object_types.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    data_type = Column(String(20), nullable=False)
    enum_values = Column(JSON)
    unit = Column(String(50))
    is_required = Column(Boolean, default=False)
    is_unique = Column(Boolean, default=False)
    default_value = Column(Text)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    object_type = relationship("ObjectType", back_populates="properties")

    __table_args__ = (
        UniqueConstraint("object_type_id", "name", name="uq_property_type_name"),
    )


class RelationType(Base):
    """
    A type of relationship between objects.
    Direction: directed or undirected.
    Cardinality: one_to_one, one_to_many, many_to_many.
    """
    __tablename__ = "relation_types"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    domain_id = Column(String(36), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    source_type_id = Column(String(36), ForeignKey("object_types.id"), nullable=False)
    target_type_id = Column(String(36), ForeignKey("object_types.id"), nullable=False)
    is_directed = Column(Boolean, default=True)
    cardinality = Column(String(20), default="many_to_many")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    domain = relationship("Domain", back_populates="relation_types")
    source_type = relationship("ObjectType", foreign_keys=[source_type_id])
    target_type = relationship("ObjectType", foreign_keys=[target_type_id])

    __table_args__ = (
        UniqueConstraint("domain_id", "name", name="uq_relation_type_domain_name"),
        Index("ix_relation_types_domain", "domain_id"),
    )


class Rule(Base):
    """
    A declarative rule: condition pattern + action.
    Rules reference ontology vocabulary and exist independently.
    """
    __tablename__ = "rules"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    domain_id = Column(String(36), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    condition = Column(JSON, nullable=False)
    action_type = Column(String(30), nullable=False)
    action_config = Column(JSON, default=dict)
    priority = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    is_draft = Column(Boolean, default=True)
    version = Column(Integer, default=1)
    hit_count = Column(Integer, default=0)
    false_positive_count = Column(Integer, default=0)
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    domain = relationship("Domain", back_populates="rules")

    __table_args__ = (
        Index("ix_rules_domain", "domain_id"),
        Index("ix_rules_active", "domain_id", "is_active"),
    )


class RuleExecution(Base):
    """A record of a rule being triggered/matched against an entity."""
    __tablename__ = "rule_executions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    rule_id = Column(String(36), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False)
    domain_id = Column(String(36), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False)
    entity_id = Column(String(36), nullable=True)
    entity_name = Column(String(200))
    entity_type = Column(String(100))
    action_type = Column(String(30), nullable=False)
    action_result = Column(JSON, default=dict)
    message = Column(Text)
    severity = Column(String(20), default="medium")
    status = Column(String(20), default="triggered")  # triggered, dismissed, resolved
    triggered_by = Column(String(100))  # user or system
    dismissed_by = Column(String(100))
    dismissed_at = Column(DateTime)
    dismissed_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_rule_exec_rule", "rule_id"),
        Index("ix_rule_exec_domain", "domain_id"),
        Index("ix_rule_exec_status", "status"),
        Index("ix_rule_exec_created", "created_at"),
    )


class Mapping(Base):
    """
    A declarative mapping: source data field -> ontology property.
    Defines how external data maps into the ontology.
    """
    __tablename__ = "mappings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    domain_id = Column(String(36), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    source_type = Column(String(50), nullable=False)
    source_config = Column(JSON, default=dict)
    field_mappings = Column(JSON, nullable=False)
    entity_resolution_rules = Column(JSON, default=list)
    is_active = Column(Boolean, default=True)
    last_sync_at = Column(DateTime)
    sync_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    domain = relationship("Domain", back_populates="mappings")

    __table_args__ = (
        Index("ix_mappings_domain", "domain_id"),
    )


# ──────────────────────────────────────────────
# Instance Data Models
# ──────────────────────────────────────────────

class Entity(Base):
    """
    An instance of an ObjectType in the graph.
    Properties are stored as JSON for flexibility.
    """
    __tablename__ = "entities"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    domain_id = Column(String(36), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False)
    object_type_id = Column(String(36), ForeignKey("object_types.id"), nullable=False)
    name = Column(String(200), nullable=False)
    properties = Column(JSON, default=dict)
    source_document_id = Column(String(36), ForeignKey("documents.id"))
    confidence = Column(Float, default=1.0)
    is_verified = Column(Boolean, default=False)
    verified_by = Column(String(100))
    verified_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    domain = relationship("Domain", back_populates="entities")
    object_type = relationship("ObjectType")
    source_relations = relationship("Relation", foreign_keys="Relation.source_id", cascade="all, delete-orphan")
    target_relations = relationship("Relation", foreign_keys="Relation.target_id", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_entities_domain_type", "domain_id", "object_type_id"),
        Index("ix_entities_name", "domain_id", "name"),
    )


class Relation(Base):
    """
    An instance of a RelationType between two Entities.
    """
    __tablename__ = "relations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    domain_id = Column(String(36), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False)
    relation_type_id = Column(String(36), ForeignKey("relation_types.id"), nullable=False)
    source_id = Column(String(36), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    target_id = Column(String(36), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    properties = Column(JSON, default=dict)
    confidence = Column(Float, default=1.0)
    is_verified = Column(Boolean, default=False)
    verified_by = Column(String(100))
    verified_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    relation_type = relationship("RelationType")
    source = relationship("Entity", foreign_keys=[source_id])
    target = relationship("Entity", foreign_keys=[target_id])

    __table_args__ = (
        UniqueConstraint("source_id", "target_id", "relation_type_id", name="uq_relation"),
        Index("ix_relations_domain", "domain_id"),
    )


# ──────────────────────────────────────────────
# Document & Extraction Models
# ──────────────────────────────────────────────

class Document(Base):
    """
    A document uploaded for processing/extraction.
    """
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    domain_id = Column(String(36), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(500))
    file_size = Column(Integer)
    mime_type = Column(String(100))
    content_text = Column(Text)
    content_summary = Column(Text)
    status = Column(String(20), default=ExtractionStatus.PENDING)
    extraction_config = Column(JSON, default=dict)
    extracted_entities_count = Column(Integer, default=0)
    extracted_relations_count = Column(Integer, default=0)
    reviewed_by = Column(String(100))
    reviewed_at = Column(DateTime)
    uploaded_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    domain = relationship("Domain", back_populates="documents")
    extraction_results = relationship("ExtractionResult", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_documents_domain_status", "domain_id", "status"),
    )


class ExtractionResult(Base):
    """
    A candidate entity or relation extracted from a document by LLM.
    Awaits human review before being promoted to the graph.
    """
    __tablename__ = "extraction_results"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    result_type = Column(String(20), nullable=False)  # "entity" or "relation"
    # For entity candidates
    candidate_object_type_id = Column(String(36), ForeignKey("object_types.id"))
    candidate_name = Column(String(200))
    candidate_properties = Column(JSON, default=dict)
    # For relation candidates
    candidate_relation_type_id = Column(String(36), ForeignKey("relation_types.id"))
    candidate_source_name = Column(String(200))
    candidate_target_name = Column(String(200))
    # Common
    confidence = Column(Float)
    llm_reasoning = Column(Text)
    status = Column(String(20), default="pending")
    reviewed_by = Column(String(100))
    review_action = Column(String(20))  # "approved", "rejected", "modified"
    review_comment = Column(Text)
    reviewed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    document = relationship("Document", back_populates="extraction_results")
    candidate_object_type = relationship("ObjectType", foreign_keys=[candidate_object_type_id])
    candidate_relation_type = relationship("RelationType", foreign_keys=[candidate_relation_type_id])

    __table_args__ = (
        Index("ix_extraction_doc", "document_id"),
        Index("ix_extraction_status", "status"),
    )


# ──────────────────────────────────────────────
# Feedback & Evolution Models
# ──────────────────────────────────────────────

class FeedbackRecord(Base):
    """
    A structured feedback record from a user.
    This is the fuel for the evolution flywheel.
    """
    __tablename__ = "feedback_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    domain_id = Column(String(36), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False)
    feedback_type = Column(String(20), nullable=False)  # extraction, inference, action
    target_id = Column(String(36), nullable=False)  # ID of the target (extraction, inference, etc.)
    target_type = Column(String(30), nullable=False)
    verdict = Column(String(20), nullable=False)  # useful, false_positive, needs_correction, skipped
    correction_data = Column(JSON)
    context = Column(JSON)
    user_id = Column(String(100))
    user_role = Column(String(30))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_feedback_domain", "domain_id"),
        Index("ix_feedback_target", "target_type", "target_id"),
        Index("ix_feedback_user", "user_id"),
    )


class ChangeProposal(Base):
    """
    A proposed change to ontology/rules/mappings generated by the improver.
    Requires approval based on severity and the grading gate policy.
    """
    __tablename__ = "change_proposals"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    domain_id = Column(String(36), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False)
    target_type = Column(String(20), nullable=False)  # ontology, rule, mapping
    target_id = Column(String(36), nullable=False)
    proposal_type = Column(String(30), nullable=False)  # add, modify, delete
    description = Column(Text, nullable=False)
    current_value = Column(JSON)
    proposed_value = Column(JSON)
    severity = Column(String(20), default=ChangeSeverity.MEDIUM)
    confidence_score = Column(Float)
    sample_count = Column(Integer, default=0)
    status = Column(String(20), default=ApprovalStatus.PENDING)
    reviewed_by = Column(String(100))
    review_comment = Column(Text)
    reviewed_at = Column(DateTime)
    applied_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_proposals_domain_status", "domain_id", "status"),
        Index("ix_proposals_severity", "severity"),
    )


# ──────────────────────────────────────────────
# User & Audit Models
# ──────────────────────────────────────────────

class User(Base):
    """
    A user of the system with RBAC role.
    """
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    username = Column(String(100), nullable=False, unique=True)
    email = Column(String(200), nullable=False, unique=True)
    display_name = Column(String(200))
    role = Column(String(30), default=UserRole.REVIEWER)
    password_hash = Column(String(255))
    is_active = Column(Boolean, default=True)
    last_login_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_users_role", "role"),
    )


class AuditLog(Base):
    """
    An audit log entry for every significant action in the system.
    """
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    action = Column(String(50), nullable=False)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(36))
    domain_id = Column(String(36))
    user_id = Column(String(100))
    user_role = Column(String(30))
    details = Column(JSON)
    ip_address = Column(String(45))
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_audit_resource", "resource_type", "resource_id"),
        Index("ix_audit_user", "user_id"),
        Index("ix_audit_created", "created_at"),
    )
