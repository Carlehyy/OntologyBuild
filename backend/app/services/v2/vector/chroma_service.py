"""Compatibility alias for the relocated Chroma service module."""
import sys

from app.shared import chroma_service as _implementation

sys.modules[__name__] = _implementation
