"""MappingService compatibility adapter for the graph projection."""
from __future__ import annotations

from app.ontologies.mappings.projection_rebuild import (
    projection_node_id,
    rebuild_neo4j_projection,
)


class ProjectionAdapterMixin:
    """Keep the historical MappingService patch surface stable."""

    def _rebuild_neo4j_projection(self, ontology_id: str) -> bool:
        return rebuild_neo4j_projection(self._db, ontology_id)


__all__ = ["ProjectionAdapterMixin", "projection_node_id"]
