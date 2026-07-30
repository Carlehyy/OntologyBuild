"""Transport-neutral failures raised by Agent Runtime application services."""
from __future__ import annotations

from typing import Any, Literal


ErrorKind = Literal["not_found", "forbidden", "conflict", "invalid"]


class AgentRuntimeApplicationError(Exception):
    """A workflow failure whose HTTP representation belongs to the router."""

    def __init__(self, kind: ErrorKind, detail: Any):
        super().__init__(str(detail))
        self.kind = kind
        self.detail = detail


def not_found(detail: Any) -> AgentRuntimeApplicationError:
    return AgentRuntimeApplicationError("not_found", detail)


def forbidden(detail: Any) -> AgentRuntimeApplicationError:
    return AgentRuntimeApplicationError("forbidden", detail)


def conflict(detail: Any) -> AgentRuntimeApplicationError:
    return AgentRuntimeApplicationError("conflict", detail)


def invalid(detail: Any) -> AgentRuntimeApplicationError:
    return AgentRuntimeApplicationError("invalid", detail)
