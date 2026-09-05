from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, require_admin
from app.settings.users.schemas import (
    RoleMenuPermissionUpdate,
    UserOut,
    UserCreate,
    UserUpdate,
)
from app.auth.service import hash_password
from app.auth.permissions import MANAGED_ROLES, get_role_menu_keys, set_role_menu_keys
from app.auth.models import User
import uuid

router = APIRouter()


def _user_out(user: User) -> dict:
    return UserOut.model_validate(user).model_dump()


def _active_admin_count(db: Session) -> int:
    return db.query(User).filter(User.role == "admin", User.is_active == True).count()


@router.get("/roles/menu-permissions")
def list_role_menu_permissions(db: Session = Depends(get_db), _=Depends(require_admin)):
    return {
        "data": [
            {"role": role, "menu_keys": get_role_menu_keys(db, role)}
            for role in MANAGED_ROLES
        ],
        "message": "ok",
    }


@router.put("/roles/{role}/menu-permissions")
def update_role_menu_permissions(
    role: str,
    body: RoleMenuPermissionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        record = set_role_menu_keys(
            db,
            role=role,
            menu_keys=body.menu_keys,
            updated_by=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "data": {"role": record.role, "menu_keys": record.menu_keys},
        "message": "ok",
    }

@router.get("")
def list_users(db: Session = Depends(get_db), _=Depends(require_admin)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return {"data": [_user_out(u) for u in users], "message": "ok"}

@router.post("", status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), _=Depends(require_admin)):
    if db.query(User).filter((User.username == body.username) | (User.email == body.email)).first():
        raise HTTPException(status_code=409, detail="Username or email already exists")
    user = User(id=str(uuid.uuid4()), username=body.username, email=body.email,
                password_hash=hash_password(body.password), role=body.role)
    db.add(user); db.commit(); db.refresh(user)
    return {"data": _user_out(user), "message": "ok"}

@router.get("/{user_id}")
def get_user(user_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"data": _user_out(user), "message": "ok"}

@router.put("/{user_id}")
def update_user(
    user_id: str,
    body: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    changes = body.model_dump(exclude_none=True)
    username = changes.get("username")
    email = changes.get("email")
    duplicate_filters = []
    if username:
        duplicate_filters.append(User.username == username)
    if email:
        duplicate_filters.append(User.email == email)
    if duplicate_filters:
        from sqlalchemy import or_

        duplicate = db.query(User).filter(
            User.id != user_id,
            or_(*duplicate_filters),
        ).first()
        if duplicate:
            raise HTTPException(status_code=409, detail="Username or email already exists")

    removes_admin_access = (
        user.role == "admin"
        and (
            changes.get("role", "admin") != "admin"
            or changes.get("is_active") is False
        )
    )
    if removes_admin_access and _active_admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="At least one active admin is required")
    if user_id == current_user.id and changes.get("is_active") is False:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    if user_id == current_user.id and changes.get("role", "admin") != "admin":
        raise HTTPException(status_code=400, detail="Cannot change your own admin role")

    password = changes.pop("password", None)
    if password:
        user.password_hash = hash_password(password)
        # 管理员重置密码同样吊销该用户全部已签发 token
        user.token_version = (user.token_version or 0) + 1
    for key, value in changes.items():
        setattr(user, key, value)
    db.commit(); db.refresh(user)
    return {"data": _user_out(user), "message": "ok"}

@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "admin" and user.is_active and _active_admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="At least one active admin is required")
    db.delete(user); db.commit()
