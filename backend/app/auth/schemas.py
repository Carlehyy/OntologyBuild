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


class ProfileUpdate(BaseModel):
    """自助资料更新（MYW-56）：只开放 email。

    username 是账号唯一标识，刻意不在此模型中——请求即使携带也会被忽略，
    从协议层面保证用户名不可自改。
    """

    email: EmailStr


class UserEnvVarItem(BaseModel):
    # key：非空、≤128 字符，仅允许字母/数字/下划线/连字符/点；value：字符串
    #（可空），长度上限 4096 用于防止无界写入。pattern 锚定全串
    #（pydantic v2 的 pattern 是 search 语义，不锚定会放过含非法字符的 key）。
    key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.\-]+$")
    value: str = Field(default="", max_length=4096)


class UserEnvVarsReplace(BaseModel):
    items: list[UserEnvVarItem] = Field(default_factory=list, max_length=50)
