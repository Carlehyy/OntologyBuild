"""Aggregate administrator settings endpoints."""

from fastapi import APIRouter

from app.platform.observability.router import router as monitoring_router


router = APIRouter()
router.include_router(monitoring_router, prefix="/monitoring")
