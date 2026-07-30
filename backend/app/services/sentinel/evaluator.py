"""Module-alias compatibility path for the canonical Sentinel evaluator."""
from __future__ import annotations

import sys

from app.ontologies.sentinels import evaluator as _canonical_evaluator


sys.modules[__name__] = _canonical_evaluator
