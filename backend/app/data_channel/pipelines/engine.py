"""Backward-compatible facade for pipeline execution entry points.

New production code should depend on :mod:`trigger_service` for orchestration
rather than this facade.
"""
from __future__ import annotations

from app.data_channel.pipelines.trigger_service import execute_pipeline

__all__ = (
    "execute_pipeline",
)
