"""Compatibility facade for the canonical dependencies in :mod:`app.deps`."""

from app.deps import (
    bearer,
    get_current_user,
    get_db,
    require_admin,
    require_menu_permission,
)

__all__ = [
    "bearer",
    "get_current_user",
    "get_db",
    "require_admin",
    "require_menu_permission",
]
