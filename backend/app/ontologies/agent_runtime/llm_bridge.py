"""Backward-compatible module alias for :mod:`app.model_configs.llm_gateway`."""
import sys

from app.model_configs import llm_gateway as _implementation

sys.modules[__name__] = _implementation
