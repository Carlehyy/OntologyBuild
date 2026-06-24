"""
Rules Management Router

CRUD for rules, including activation/deactivation and version management.
Rules are independent from ontology and have their own versioning.
"""

from typing import List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Rule, RuleExecution, AuditLog
from app.schemas import RuleCreate, RuleUpdate, RuleOut

router = APIRouter(prefix="/rules", tags=["Rules"])


@router.get("/domain/{domain_id}", response_model=List[RuleOut])
def list_rules(
    domain_id: str,
    db: Session = Depends(get_db),
    active_only: bool = False,
    draft_only: bool = False,
):
    """List rules for a domain."""
    query = db.query(Rule).filter(Rule.domain_id == domain_id)
    if active_only:
        query = query.filter(Rule.is_active == True)
    if draft_only:
        query = query.filter(Rule.is_draft == True)
    return query.order_by(Rule.priority.desc(), Rule.created_at.desc()).all()


@router.post("/domain/{domain_id}", response_model=RuleOut)
def create_rule(domain_id: str, data: RuleCreate, db: Session = Depends(get_db)):
    """Create a new rule."""
    rule = Rule(
        domain_id=domain_id,
        **data.model_dump(),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    db.add(AuditLog(
        action="create",
        resource_type="rule",
        resource_id=rule.id,
        domain_id=domain_id,
        details={"name": rule.name, "is_draft": rule.is_draft},
    ))
    db.commit()

    return rule


@router.get("/{rule_id}", response_model=RuleOut)
def get_rule(rule_id: str, db: Session = Depends(get_db)):
    """Get a rule by ID."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.put("/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: str, data: RuleUpdate, db: Session = Depends(get_db)):
    """Update a rule."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)

    rule.version += 1
    db.commit()
    db.refresh(rule)
    return rule


@router.post("/{rule_id}/toggle-active")
def toggle_rule_active(rule_id: str, db: Session = Depends(get_db)):
    """Toggle rule active status."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    rule.is_active = not rule.is_active
    db.commit()

    return {
        "success": True,
        "is_active": rule.is_active,
        "message": f"Rule {'activated' if rule.is_active else 'deactivated'}",
    }


@router.post("/{rule_id}/publish")
def publish_rule(rule_id: str, db: Session = Depends(get_db)):
    """Publish a draft rule."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    rule.is_draft = False
    rule.is_active = True
    db.commit()

    db.add(AuditLog(
        action="publish",
        resource_type="rule",
        resource_id=rule.id,
        domain_id=rule.domain_id,
        details={"name": rule.name},
    ))
    db.commit()

    return {"success": True, "message": "Rule published"}


@router.delete("/{rule_id}")
def delete_rule(rule_id: str, db: Session = Depends(get_db)):
    """Delete a rule."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    db.delete(rule)
    db.commit()
    return {"success": True, "message": "Rule deleted"}


@router.post("/{rule_id}/feedback")
def record_rule_feedback(
    rule_id: str,
    verdict: str,  # "hit", "false_positive"
    db: Session = Depends(get_db),
):
    """Record feedback on a rule hit."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if verdict == "false_positive":
        rule.false_positive_count += 1
    elif verdict == "hit":
        rule.hit_count += 1

    db.commit()
    return {"success": True, "hit_count": rule.hit_count, "fp_count": rule.false_positive_count}


@router.get("/{rule_id}/executions")
def list_rule_executions(
    rule_id: str,
    db: Session = Depends(get_db),
    limit: int = 50,
):
    """Get execution history for a rule."""
    executions = db.query(RuleExecution).filter(
        RuleExecution.rule_id == rule_id
    ).order_by(RuleExecution.created_at.desc()).limit(limit).all()
    return executions


@router.get("/domain/{domain_id}/executions")
def list_domain_executions(
    domain_id: str,
    db: Session = Depends(get_db),
    status: str = None,
    limit: int = 50,
):
    """Get all rule executions for a domain."""
    query = db.query(RuleExecution).filter(RuleExecution.domain_id == domain_id)
    if status:
        query = query.filter(RuleExecution.status == status)
    return query.order_by(RuleExecution.created_at.desc()).limit(limit).all()


@router.post("/{rule_id}/dismiss")
def dismiss_execution(
    rule_id: str,
    execution_id: str,
    reason: str = "",
    dismissed_by: str = "user",
    db: Session = Depends(get_db),
):
    """Dismiss a rule execution."""
    exec_record = db.query(RuleExecution).filter(
        RuleExecution.id == execution_id,
        RuleExecution.rule_id == rule_id,
    ).first()
    if not exec_record:
        raise HTTPException(status_code=404, detail="Execution record not found")

    exec_record.status = "dismissed"
    exec_record.dismissed_by = dismissed_by
    exec_record.dismissed_at = datetime.utcnow()
    exec_record.dismissed_reason = reason
    db.commit()
    return {"success": True, "message": "Execution dismissed"}
