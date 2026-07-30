"""Backward-compatible facade for pipeline execution entry points.

New production code should depend on :mod:`route_executor` for transformations
or :mod:`trigger_service` for orchestration, rather than this mixed facade.
"""
from __future__ import annotations

from app.data_channel.pipelines.route_executor import (
    execute_route_a,
    execute_route_b,
    execute_route_c,
)
from app.data_channel.pipelines.trigger_service import execute_pipeline

__all__ = (
    "execute_route_a",
    "execute_route_b",
    "execute_route_c",
    "execute_pipeline",
)
