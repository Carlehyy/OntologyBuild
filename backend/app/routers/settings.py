"""Aggregate administrator settings endpoints."""

from fastapi import APIRouter

from app.settings.rules.router import router as rules_router


router = APIRouter()
router.include_router(rules_router)
