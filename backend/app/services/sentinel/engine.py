"""Module-alias compatibility path for the canonical Sentinel engine."""
from __future__ import annotations

import sys

from app.ontologies.sentinels import engine as _canonical_engine


sys.modules[__name__] = _canonical_engine
