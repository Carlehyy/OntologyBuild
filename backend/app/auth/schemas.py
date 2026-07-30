from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserOut(BaseModel):
    id: str
    username: str
    email: str
    role: str
    is_active: bool
    created_at: datetime
    menu_permissions: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str
