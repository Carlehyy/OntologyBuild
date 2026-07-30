"""Stable payload contract for ontology publication gate errors."""

from __future__ import annotations


def gate_error(
    code: str,
    kind: str,
    message: str,
    *,
    item_id: str = "",
    name: str = "",
    field: str = "",
) -> dict:
    error = {
        "code": code,
        "kind": kind,
        "id": item_id,
        "name": name,
        "message": message,
    }
    if field:
        error["field"] = field
    return error
