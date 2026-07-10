"""Compatibility alias for the relocated Markdown extraction step module."""
import sys

from app.data_channel.pipelines.steps import md_to_structured as _implementation

sys.modules[__name__] = _implementation
