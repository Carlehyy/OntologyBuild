from __future__ import annotations

from sqlalchemy.orm import Session

from app.auth.models import RoleMenuPermission, User


ALL_MENU_KEYS = (
    "overview",
    "super_assistant",
    "explore",
    "ontologies",
    "agent",
    "events",
    "data",
    "data.pipelines",
    "data.sync_tasks",
    "data.structured",
    "api_hub",
    "api_hub.interfaces",
    "api_hub.history",
    "api_hub.authorization",
    "models",
)

MANAGED_ROLES = ("editor", "viewer", "custom")

# Preserve the former non-admin experience on upgrade: regular users keep all
# product areas that were previously visible, while API Hub remains opt-in.
DEFAULT_NON_ADMIN_MENU_KEYS = tuple(
    key for key in ALL_MENU_KEYS if not key.startswith("api_hub")
)

# A newly assigned custom role starts from the smallest useful surface. The
# administrator can then grant its exact menu range from User Management.
DEFAULT_CUSTOM_MENU_KEYS = ("overview",)

PARENT_MENU_KEYS = {
    "data.pipelines": "data",
    "data.sync_tasks": "data",
    "data.structured": "data",
    "api_hub.interfaces": "api_hub",
    "api_hub.history": "api_hub",
    "api_hub.authorization": "api_hub",
}
GROUP_MENU_KEYS = {
    "data": ("data.pipelines", "data.sync_tasks", "data.structured"),
    "api_hub": (
        "api_hub.interfaces",
        "api_hub.history",
        "api_hub.authorization",
    ),
}


def normalize_menu_keys(menu_keys: list[str] | tuple[str, ...]) -> list[str]:
    allowed = set(ALL_MENU_KEYS)
    normalized = {key for key in menu_keys if key in allowed}
    for key in tuple(normalized):
        parent = PARENT_MENU_KEYS.get(key)
        if parent:
            normalized.add(parent)
    for parent, children in GROUP_MENU_KEYS.items():
        if parent in normalized and not any(child in normalized for child in children):
            normalized.remove(parent)
    return [key for key in ALL_MENU_KEYS if key in normalized]


def get_role_menu_keys(db: Session, role: str) -> list[str]:
    if role == "admin":
        return list(ALL_MENU_KEYS)
    record = db.query(RoleMenuPermission).filter(
        RoleMenuPermission.role == role,
    ).first()
    if record is None:
        if role == "custom":
            return list(DEFAULT_CUSTOM_MENU_KEYS)
        return list(DEFAULT_NON_ADMIN_MENU_KEYS)
    return normalize_menu_keys(record.menu_keys or [])


def set_role_menu_keys(
    db: Session,
    *,
    role: str,
    menu_keys: list[str],
    updated_by: str,
) -> RoleMenuPermission:
    if role not in MANAGED_ROLES:
        raise ValueError("Only non-admin roles can be configured")
    normalized = normalize_menu_keys(menu_keys)
    record = db.query(RoleMenuPermission).filter(
        RoleMenuPermission.role == role,
    ).first()
    if record is None:
        record = RoleMenuPermission(role=role, menu_keys=normalized)
        db.add(record)
    else:
        record.menu_keys = normalized
    record.updated_by = updated_by
    db.commit()
    db.refresh(record)
    return record


def user_has_menu_access(db: Session, user: User, menu_key: str) -> bool:
    if user.role == "admin":
        return True
    return menu_key in get_role_menu_keys(db, user.role)
