"""Compatibility alias for the relocated graph API module.

The router exposes intentionally patchable service factories. A star import
would cause patches to target a different module object.
"""
import sys

from app.ontologies.graph import v2_router as _implementation

sys.modules[__name__] = _implementation
