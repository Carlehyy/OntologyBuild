"""Immutable snapshot normalization, hashing, numbering, and model projection.

This leaf contract intentionally has no database, mapping, Sentinel, release, or
Action runtime dependency.  ``evolution_service`` re-exports the exact objects
for historical imports while runtime consumers depend on this canonical seam.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any

from app.ontologies.formal_modeling import schemas as FS


SNAPSHOT_KEYS = (
    "objectTypes", "linkTypes", "actions", "functions",
    "sentinels", "mappings", "linkMappings",
)


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def complete_snapshot(snapshot: dict | None) -> dict:
    """历史快照也归一为包含全部集合的完整结构。"""
    source = snapshot or {}
    return {key: json_safe(source.get(key) or []) for key in SNAPSHOT_KEYS}


def canonical_snapshot(snapshot: dict | None) -> dict:
    normalized = complete_snapshot(snapshot)
    for key in SNAPSHOT_KEYS:
        normalized[key] = sorted(
            normalized[key],
            key=lambda item: str(item.get("id") or item.get("name") or ""),
        )
    return normalized


def snapshot_hash(snapshot: dict | None) -> str:
    payload = json.dumps(
        canonical_snapshot(snapshot), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def next_draft_number(parent_number: str, sibling_numbers: list[str]) -> str:
    prefix = f"{parent_number}."
    used: list[int] = []
    for number in sibling_numbers:
        if not number.startswith(prefix):
            continue
        tail = number[len(prefix):]
        if tail.isdigit():
            used.append(int(tail))
    return f"{parent_number}.{max(used, default=0) + 1}"


def next_release_number(current_number: str | None) -> str:
    raw = str(current_number or "v0")
    head = raw.removeprefix("v").split(".", 1)[0]
    try:
        major = int(head)
    except ValueError:
        major = 0
    return f"v{major + 1}"


def snapshot_models(snapshot: dict) -> dict[str, list[SimpleNamespace]]:
    specs = (
        ("objectTypes", FS.ObjectTypeCreate),
        ("linkTypes", FS.LinkTypeCreate),
        ("actions", FS.ActionTypeCreate),
        ("functions", FS.FunctionCreate),
    )
    result: dict[str, list[SimpleNamespace]] = {}
    for key, schema in specs:
        values: list[SimpleNamespace] = []
        for raw in complete_snapshot(snapshot)[key]:
            parsed = schema.model_validate(raw)
            values.append(SimpleNamespace(
                id=str(raw.get("id") or ""),
                **parsed.model_dump(exclude_none=False),
            ))
        result[key] = values
    return result
