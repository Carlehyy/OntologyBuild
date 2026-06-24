"""
Feedback Router

Handles all feedback-related operations.
Feedback is the fuel for the evolution flywheel.
"""

from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import FeedbackRecord, ChangeProposal, Domain
from app.schemas import FeedbackCreate, FeedbackOut, ChangeProposalOut, ProposalReview

router = APIRouter(prefix="/feedback", tags=["Feedback"])


@router.get("/domain/{domain_id}", response_model=List[FeedbackOut])
def list_feedback(
    domain_id: str,
    db: Session = Depends(get_db),
    feedback_type: Optional[str] = None,
    verdict: Optional[str] = None,
    days: int = 30,
    limit: int = 100,
):
    """List feedback records for a domain."""
    since = datetime.utcnow() - timedelta(days=days)
    query = db.query(FeedbackRecord).filter(
        FeedbackRecord.domain_id == domain_id,
        FeedbackRecord.created_at >= since,
    )

    if feedback_type:
        query = query.filter(FeedbackRecord.feedback_type == feedback_type)
    if verdict:
        query = query.filter(FeedbackRecord.verdict == verdict)

    return query.order_by(FeedbackRecord.created_at.desc()).limit(limit).all()


@router.post("/domain/{domain_id}", response_model=FeedbackOut)
def create_feedback(domain_id: str, data: FeedbackCreate, db: Session = Depends(get_db)):
    """Create a new feedback record."""
    feedback = FeedbackRecord(
        domain_id=domain_id,
        **data.model_dump(),
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback


@router.get("/stats/{domain_id}")
def get_feedback_stats(domain_id: str, db: Session = Depends(get_db)):
    """Get feedback statistics for a domain."""
    since = datetime.utcnow() - timedelta(days=30)

    total = db.query(FeedbackRecord).filter(
        FeedbackRecord.domain_id == domain_id,
        FeedbackRecord.created_at >= since,
    ).count()

    verdict_counts = db.query(
        FeedbackRecord.verdict,
        func.count(FeedbackRecord.id).label("count")
    ).filter(
        FeedbackRecord.domain_id == domain_id,
        FeedbackRecord.created_at >= since,
    ).group_by(FeedbackRecord.verdict).all()

    type_counts = db.query(
        FeedbackRecord.feedback_type,
        func.count(FeedbackRecord.id).label("count")
    ).filter(
        FeedbackRecord.domain_id == domain_id,
        FeedbackRecord.created_at >= since,
    ).group_by(FeedbackRecord.feedback_type).all()

    return {
        "total": total,
        "by_verdict": {v: c for v, c in verdict_counts},
        "by_type": {t: c for t, c in type_counts},
        "period_days": 30,
    }


# ──────────────────────────────────────────────
# Change Proposals (Stage 2 - preparatory)
# ──────────────────────────────────────────────

@router.get("/proposals/domain/{domain_id}", response_model=List[ChangeProposalOut])
def list_proposals(
    domain_id: str,
    db: Session = Depends(get_db),
    status: Optional[str] = None,
):
    """List change proposals for a domain."""
    query = db.query(ChangeProposal).filter(ChangeProposal.domain_id == domain_id)
    if status:
        query = query.filter(ChangeProposal.status == status)
    return query.order_by(ChangeProposal.created_at.desc()).all()


@router.post("/proposals/{proposal_id}/review")
def review_proposal(proposal_id: str, data: ProposalReview, db: Session = Depends(get_db)):
    """Review a change proposal (approve/reject)."""
    proposal = db.query(ChangeProposal).filter(ChangeProposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    if data.action == "approve":
        proposal.status = "approved"
        # In Stage 2, we would apply the change here
        # For Stage 1, we just mark it approved for manual application
    else:
        proposal.status = "rejected"

    proposal.review_comment = data.comment
    db.commit()

    return {"success": True, "status": proposal.status}


@router.get("/proposals/stats/{domain_id}")
def get_proposal_stats(domain_id: str, db: Session = Depends(get_db)):
    """Get proposal statistics."""
    status_counts = db.query(
        ChangeProposal.status,
        func.count(ChangeProposal.id).label("count")
    ).filter(ChangeProposal.domain_id == domain_id).group_by(ChangeProposal.status).all()

    severity_counts = db.query(
        ChangeProposal.severity,
        func.count(ChangeProposal.id).label("count")
    ).filter(ChangeProposal.domain_id == domain_id).group_by(ChangeProposal.severity).all()

    return {
        "by_status": {s: c for s, c in status_counts},
        "by_severity": {s: c for s, c in severity_counts},
    }
