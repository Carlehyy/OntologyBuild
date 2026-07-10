"""Compatibility alias for the relocated graph analytics module."""
import sys

from app.ontologies.graph import graph_analytics as _implementation

sys.modules[__name__] = _implementation
