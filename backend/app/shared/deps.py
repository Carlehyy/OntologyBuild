from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError
from app.shared.database import SessionLocal
from app.auth.service import decode_token, get_user_by_id
from app.auth.models import User

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
        if not any(user_has_menu_access(db, current_user, key) for key in acceptable_keys):
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
