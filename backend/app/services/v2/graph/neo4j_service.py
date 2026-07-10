"""Compatibility alias for the relocated Neo4j service module."""
import sys

from app.ontologies.graph import neo4j_service as _implementation

sys.modules[__name__] = _implementation
