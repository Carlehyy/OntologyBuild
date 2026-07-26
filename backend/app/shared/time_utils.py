"""Shared timestamp serialization helpers.

Application timestamps are stored as UTC. SQLite and some PostgreSQL driver /
column combinations can return those values as naive ``datetime`` objects,
though. Serializing such a value directly makes browsers interpret it as
local time and shifts audit history by the client's UTC offset.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import overload


def as_utc(value: datetime) -> datetime:
    """Interpret database-naive timestamps as UTC and normalize aware values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@overload
def utc_iso(value: datetime) -> str: ...


@overload
def utc_iso(value: None) -> None: ...


def utc_iso(value: datetime | None) -> str | None:
    """Return an ISO-8601 UTC timestamp with an explicit ``Z`` designator."""
    if value is None:
        return None
    return as_utc(value).isoformat().replace("+00:00", "Z")
