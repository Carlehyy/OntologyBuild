from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.inbox.schemas import DeliveryStateUpdate
from app.inbox.service import (
    drain_outbox,
    get_delivery,
    inbox_summary,
    list_deliveries,
    mark_all_read,
    serialize_delivery,
    update_delivery_state,
)


router = APIRouter()


def _drain_safely(db: Session) -> None:
    # Immediate task dispatch is the normal path. Reading the inbox also repairs
    # durable pending events left by a process crash between commit and dispatch.
    drain_outbox(db, limit=20)


@router.get("/summary")
def summary(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _drain_safely(db)
    return {"data": inbox_summary(db, user_id=current_user.id)}


@router.post("/read-all")
def read_all(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return {"data": {"updated": mark_all_read(db, user_id=current_user.id)}}


@router.get("")
def list_inbox(
    tab: str = "all",
    kind: str | None = None,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _drain_safely(db)
    return {"data": list_deliveries(
        db,
        user_id=current_user.id,
        tab=tab,
        kind=kind,
        cursor=cursor,
        limit=limit,
    )}


@router.get("/{delivery_id}")
def get_inbox_item(
    delivery_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    delivery, item = get_delivery(db, user_id=current_user.id, delivery_id=delivery_id)
    return {"data": serialize_delivery(delivery, item)}


@router.patch("/{delivery_id}")
def update_inbox_item(
    delivery_id: str,
    body: DeliveryStateUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return {"data": update_delivery_state(
        db,
        user_id=current_user.id,
        delivery_id=delivery_id,
        state=body.state,
    )}
