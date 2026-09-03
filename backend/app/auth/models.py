import uuid
from datetime import datetime, timezone
from sqlalchemy import JSON, String, Boolean, DateTime, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 隐私变量上报 token（用户级，Fernet 密文）。为空表示该用户尚未启用
    # 隐私变量上报；创建首个隐私变量或显式重置时生成。nullable 以兼容存量用户。
    report_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class RoleMenuPermission(Base):
    """Navigable product areas granted to a non-admin role."""

    __tablename__ = "role_menu_permissions"

    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    menu_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class UserEnvVar(Base):
    """用户私有环境变量（MYW-56）。

    key 明文存储用于列表展示与 (user_id, key) 唯一约束；value 为 Fernet
    密文（与 MCP 服务器配置同一加密设施）。接口代理的 URL/Header/Body 里
    可以 ``{{env:KEY}}`` 占位符引用，UI 调用链路以本人身份解析（见
    app.api_hub.personal_ref）；无用户身份的链路（公开代理 / n8n）不解析。
    """

    __tablename__ = "user_env_vars"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_env_vars_user_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class UserPrivacyKeypair(Base):
    """用户隐私变量 RSA 密钥对（每用户一行）。

    公钥 PEM 明文存储——下发给用户的上报脚本只用公钥加密，泄露公钥无
    风险。私钥 PEM 经平台 Fernet 再加密后落库（DB 拖库时私钥仍不可读，
    还需要平台 Fernet 密钥才能解出私钥）。创建首个隐私变量时按需生成。
    """

    __tablename__ = "user_privacy_keypairs"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_pem_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class UserPrivacyVar(Base):
    """用户隐私变量。

    与 UserEnvVar 的差异：value 由用户本地脚本用该用户的 RSA 公钥加密后
    上报，平台用对应私钥解密后再以 Fernet 包一层落库（双层加密）。key
    明文用于列表展示与 (user_id, key) 唯一约束。本期仅存储与维护，不注入
    任何执行链路（与 UserEnvVar 同一克制边界）。
    """

    __tablename__ = "user_privacy_vars"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_privacy_vars_user_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    # 最终落库值：RSA 解密后的明文再经 Fernet 加密（双层保险）。
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_reported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
