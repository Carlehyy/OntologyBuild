"""Shared authorization boundary for the digital asset lake.

Asset schemas, versions, reviews, connectors and schedules are production state;
authentication alone must not grant mutation rights.  Resource-specific guards
(pipeline ownership, review approval admin gate) remain layered on top.
"""
from fastapi import Depends, HTTPException, Request

from app.deps import get_current_user


_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def asset_lake_access_guard(
    request: Request,
    user=Depends(get_current_user),
):
    if request.method.upper() in _READ_METHODS:
        return user
    if str(getattr(user, "role", "viewer") or "viewer") not in {"admin", "editor"}:
        raise HTTPException(403, "Viewer role is read-only for the asset lake")
    return user
