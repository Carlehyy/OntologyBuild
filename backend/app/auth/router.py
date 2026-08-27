from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.deps import bearer, get_db, get_current_user
from app.config import settings
from app.auth.schemas import (
    LoginRequest,
    PasswordChangeRequest,
    ProfileUpdate,
    RegisterRequest,
    TokenResponse,
    UserEnvVarsReplace,
    UserOut,
)
from app.auth.service import authenticate_user, create_access_token, hash_password, verify_password
from app.auth.models import User, UserEnvVar
from app.auth.permissions import get_role_menu_keys
from app.auth.crypto import decrypt_value, encrypt_value
import uuid

router = APIRouter()

@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token({"sub": user.id, "role": user.role})
    return {"data": {"access_token": token, "token_type": "bearer"}, "message": "ok"}

@router.post("/register", status_code=201)
def register(
    body: RegisterRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
):
    if not settings.allow_public_registration:
        current_user = get_current_user(credentials=credentials, db=db)
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Admin required")
    if db.query(User).filter((User.username == body.username) | (User.email == body.email)).first():
        raise HTTPException(status_code=409, detail="Username or email already exists")
    user = User(
        id=str(uuid.uuid4()),
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        role="viewer",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"data": UserOut.model_validate(user).model_dump(), "message": "ok"}

@router.get("/profile")
def profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = UserOut.model_validate(current_user).model_dump()
    data["menu_permissions"] = get_role_menu_keys(db, current_user.role)
    return {"data": data, "message": "ok"}

@router.put("/password")
def change_password(body: PasswordChangeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password incorrect")
    current_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"message": "Password updated"}

# 个人资料自助更新（MYW-56）：用户名是账号唯一标识，不允许自改；这里只
# 开放邮箱。响应结构与 GET /profile 一致，前端可直接用返回值刷新登录态。

@router.put("/profile")
def update_profile(
    body: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    duplicate = db.query(User).filter(User.email == body.email, User.id != current_user.id).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="Email already exists")
    current_user.email = body.email
    db.commit()
    db.refresh(current_user)
    data = UserOut.model_validate(current_user).model_dump()
    data["menu_permissions"] = get_role_menu_keys(db, current_user.role)
    return {"data": data, "message": "ok"}


# 用户私有环境变量（MYW-56）：仅本人可见可改，value 加密落库。PUT 为全量
# 保存语义——请求中的列表即该用户的完整变量集（条数上限等约束在 schema 层）。

def _env_var_out(row: UserEnvVar) -> dict:
    return {"key": row.key, "value": decrypt_value(row.value_encrypted)}


@router.get("/env-vars")
def list_env_vars(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = (
        db.query(UserEnvVar)
        .filter(UserEnvVar.user_id == current_user.id)
        .order_by(UserEnvVar.key)
        .all()
    )
    return {"data": [_env_var_out(row) for row in rows], "message": "ok"}

@router.put("/env-vars")
def replace_env_vars(
    body: UserEnvVarsReplace,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    seen: set[str] = set()
    for item in body.items:
        if item.key in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate env var key: {item.key}")
        seen.add(item.key)

    db.query(UserEnvVar).filter(UserEnvVar.user_id == current_user.id).delete(synchronize_session=False)
    for item in body.items:
        db.add(UserEnvVar(
            user_id=current_user.id,
            key=item.key,
            value_encrypted=encrypt_value(item.value),
        ))
    db.commit()

    rows = (
        db.query(UserEnvVar)
        .filter(UserEnvVar.user_id == current_user.id)
        .order_by(UserEnvVar.key)
        .all()
    )
    return {"data": [_env_var_out(row) for row in rows], "message": "ok"}
