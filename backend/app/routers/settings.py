"""Aggregate administrator settings endpoints."""

from fastapi import APIRouter

from app.settings.object_storage.router import router as object_storage_router
from app.settings.rules.router import router as rules_router


router = APIRouter()
router.include_router(rules_router)
router.include_router(object_storage_router)
