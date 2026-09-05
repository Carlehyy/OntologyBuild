from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.service import decode_token, get_user_by_id
from app.database import SessionLocal


bearer = HTTPBearer(auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=403, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
        user = get_user_by_id(db, payload["sub"])
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        # 会话吊销：token 代数落后于用户当前代数（改密/管理员重置过）即失效。
        # 存量 token 无 ver claim 按 0 处理，与列默认一致，升级不强制重登。
        if int(payload.get("ver", 0)) != user.token_version:
            raise HTTPException(status_code=401, detail="Token revoked")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin required")
    return current_user


def require_menu_permission(menu_key: str, *, read_menu_keys: tuple[str, ...] = ()):
    """Create a dependency for a role-configurable product area.

    Some pages consume read-only reference data owned by another area (for
    example, the Agent page reads ontology and model lists). Those declared
    consumers may read the shared API, while mutations still require the API's
    owning menu permission.
    """

    def dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        from app.auth.permissions import user_has_menu_access

        acceptable_keys = (menu_key,)
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            acceptable_keys += read_menu_keys
        if not any(
            user_has_menu_access(db, current_user, key) for key in acceptable_keys
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "MENU_ACCESS_DENIED",
                    "message": "当前角色无权访问此功能",
                    "menu_key": menu_key,
                },
            )
        return current_user

    return dependency
