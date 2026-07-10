"""Compatibility alias for the relocated graph API module.

The router exposes a few intentionally patchable factories and a private
SQLite fallback used by operational tests.  A star import loses the private
helper and causes patches to target a different module object.
"""
import sys

from app.ontologies.graph import v2_router as _implementation

sys.modules[__name__] = _implementation
