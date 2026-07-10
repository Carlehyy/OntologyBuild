"""Compatibility alias for the relocated SQL connector module.

This must be a real module alias, rather than a ``from ... import *`` shim.
Callers have historically patched module globals such as ``create_engine`` and
``inspect`` through this import path; copying the public names leaves those
patches disconnected from the globals used by :class:`SQLConnector`.
"""
import sys

from app.data_channel.connections import sql_connector as _implementation

sys.modules[__name__] = _implementation
