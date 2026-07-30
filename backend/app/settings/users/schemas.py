from pydantic import BaseModel, EmailStr, Field
from typing import Literal, Optional

from app.auth.schemas import UserOut


UserRole = Literal["admin", "editor", "viewer", "custom"]

class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    role: UserRole = "viewer"

class UserUpdate(BaseModel):
    username: Optional[str] = Field(default=None, min_length=2, max_length=50)
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class RoleMenuPermissionUpdate(BaseModel):
    menu_keys: list[str] = Field(default_factory=list)
