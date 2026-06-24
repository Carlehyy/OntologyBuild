"""
Admin Router

User management, audit logs, dashboard statistics, and system configuration.
"""

from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import (
    User, AuditLog, Domain, ObjectType, RelationType, Rule,
    Entity, Relation, Document, FeedbackRecord, ChangeProposal,
    UserRole,
)
from app.schemas import (
    UserCreate, UserUpdate, UserOut,
    AuditLogOut, DashboardStats, DomainStats,
)

router = APIRouter(prefix="/admin", tags=["Admin"])


# ──────────────────────────────────────────────
# User Management
# ──────────────────────────────────────────────

@router.get("/users", response_model=List[UserOut])
def list_users(
    db: Session = Depends(get_db),
    role: Optional[str] = None,
    active_only: bool = True,
):
    """List all users."""
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    if active_only:
        query = query.filter(User.is_active == True)
    return query.all()


@router.post("/users", response_model=UserOut)
def create_user(data: UserCreate, db: Session = Depends(get_db)):
    """Create a new user."""
    # Check for duplicate
    existing = db.query(User).filter(
        (User.username == data.username) | (User.email == data.email)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username or email already exists")

    # Hash password if provided
    password_hash = None
    if data.password:
        from passlib.hash import bcrypt
        password_hash = bcrypt.hash(data.password)

    user = User(
        username=data.username,
        email=data.email,
        display_name=data.display_name or data.username,
        role=data.role,
        password_hash=password_hash,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: str, db: Session = Depends(get_db)):
    """Get a user by ID."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, data: UserUpdate, db: Session = Depends(get_db)):
    """Update a user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)

    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(user_id: str, db: Session = Depends(get_db)):
    """Delete a user (soft delete by deactivating)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    db.commit()
    return {"success": True, "message": "User deactivated"}


# ──────────────────────────────────────────────
# Dashboard Statistics
# ──────────────────────────────────────────────

@router.get("/dashboard", response_model=DashboardStats)
def get_dashboard_stats(db: Session = Depends(get_db)):
    """Get comprehensive dashboard statistics."""
    total_domains = db.query(Domain).count()
    total_entities = db.query(Entity).count()
    total_relations = db.query(Relation).count()
    total_documents = db.query(Document).count()

    # Pending reviews
    pending_reviews = db.query(Document).filter(
        Document.status == "completed"
    ).count()

    # Recent feedback
    recent_feedback = db.query(FeedbackRecord).order_by(
        FeedbackRecord.created_at.desc()
    ).limit(10).all()

    # Per-domain stats
    domain_stats = []
    domains = db.query(Domain).all()
    for domain in domains:
        stats = _get_domain_stats(db, domain.id)
        domain_stats.append(stats)

    return {
        "total_domains": total_domains,
        "total_entities": total_entities,
        "total_relations": total_relations,
        "total_documents": total_documents,
        "pending_reviews": pending_reviews,
        "recent_feedback": recent_feedback,
        "domain_stats": domain_stats,
    }


@router.get("/domains/{domain_id}/stats", response_model=DomainStats)
def get_domain_statistics(domain_id: str, db: Session = Depends(get_db)):
    """Get statistics for a specific domain."""
    return _get_domain_stats(db, domain_id)


def _get_domain_stats(db: Session, domain_id: str) -> dict:
    """Helper to compute domain statistics."""
    domain = db.query(Domain).filter(Domain.id == domain_id).first()
    if not domain:
        return {
            "domain_id": domain_id,
            "domain_name": "Unknown",
            "object_types_count": 0,
            "relation_types_count": 0,
            "rules_count": 0,
            "rules_active_count": 0,
            "entities_count": 0,
            "relations_count": 0,
            "documents_count": 0,
            "documents_pending_review": 0,
            "feedback_count": 0,
            "proposals_pending": 0,
        }

    return {
        "domain_id": domain_id,
        "domain_name": domain.name,
        "object_types_count": db.query(ObjectType).filter(ObjectType.domain_id == domain_id).count(),
        "relation_types_count": db.query(RelationType).filter(RelationType.domain_id == domain_id).count(),
        "rules_count": db.query(Rule).filter(Rule.domain_id == domain_id).count(),
        "rules_active_count": db.query(Rule).filter(
            Rule.domain_id == domain_id, Rule.is_active == True, Rule.is_draft == False
        ).count(),
        "entities_count": db.query(Entity).filter(Entity.domain_id == domain_id).count(),
        "relations_count": db.query(Relation).filter(Relation.domain_id == domain_id).count(),
        "documents_count": db.query(Document).filter(Document.domain_id == domain_id).count(),
        "documents_pending_review": db.query(Document).filter(
            Document.domain_id == domain_id, Document.status == "completed"
        ).count(),
        "feedback_count": db.query(FeedbackRecord).filter(FeedbackRecord.domain_id == domain_id).count(),
        "proposals_pending": db.query(ChangeProposal).filter(
            ChangeProposal.domain_id == domain_id, ChangeProposal.status == "pending"
        ).count(),
    }


# ──────────────────────────────────────────────
# Audit Logs
# ──────────────────────────────────────────────

@router.get("/audit-logs", response_model=List[AuditLogOut])
def list_audit_logs(
    db: Session = Depends(get_db),
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    user_id: Optional[str] = None,
    days: int = 7,
    limit: int = 100,
):
    """List audit logs with filters."""
    since = datetime.utcnow() - timedelta(days=days)
    query = db.query(AuditLog).filter(AuditLog.created_at >= since)

    if action:
        query = query.filter(AuditLog.action == action)
    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type)
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)

    return query.order_by(AuditLog.created_at.desc()).limit(limit).all()


@router.get("/audit-logs/stats")
def get_audit_stats(db: Session = Depends(get_db)):
    """Get audit log statistics."""
    since = datetime.utcnow() - timedelta(days=7)

    action_counts = db.query(
        AuditLog.action,
        func.count(AuditLog.id).label("count")
    ).filter(AuditLog.created_at >= since).group_by(AuditLog.action).all()

    resource_counts = db.query(
        AuditLog.resource_type,
        func.count(AuditLog.id).label("count")
    ).filter(AuditLog.created_at >= since).group_by(AuditLog.resource_type).all()

    return {
        "total_last_7_days": db.query(AuditLog).filter(AuditLog.created_at >= since).count(),
        "by_action": {a: c for a, c in action_counts},
        "by_resource": {r: c for r, c in resource_counts},
    }


# ──────────────────────────────────────────────
# System Configuration
# ──────────────────────────────────────────────

@router.get("/config")
def get_system_config():
    """Get public system configuration."""
    from app.config import get_settings
    from app.services.llm_service import get_llm_service

    settings = get_settings()
    llm = get_llm_service()

    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "llm_provider": settings.llm_provider,
        "llm_available": llm.is_available,
        "llm_model": settings.openai_model if settings.llm_provider == "openai" else settings.ollama_model,
        "features": {
            "extraction": True,
            "inference": True,
            "feedback": True,
            "proposals": True,  # Stage 2 prep
            "evolution": False,  # Stage 2+
        },
    }


@router.get("/health")
def health_check():
    """System health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "api": "ok",
            "database": "ok",
        },
    }
