"""Aggregate administrator settings endpoints."""

from fastapi import APIRouter

from app.platform.observability.router import router as monitoring_router
from app.settings.rules.router import router as rules_router


router = APIRouter()
router.include_router(rules_router)
router.include_router(monitoring_router, prefix="/monitoring")
