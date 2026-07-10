"""Backward-compatible module alias for :mod:`app.shared.storage`.

A star re-export copied ``Minio`` into this namespace while ``StorageService``
continued to resolve globals in ``app.shared.storage``. Patching the established
legacy import path therefore patched a dead name and real network I/O still ran.
"""
import sys

from app.shared import storage as _implementation

sys.modules[__name__] = _implementation
