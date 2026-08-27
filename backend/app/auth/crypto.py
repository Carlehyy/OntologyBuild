"""Auth 域私有的对称加密薄封装（MYW-56 用户私有环境变量）。

架构边界（tests/architecture/test_foundation_dependency_direction.py）要求
auth 不得依赖 app.settings / app.shared 兼容门面；与该域持有 passlib/jose
封装的方式一致，这里直接使用 cryptography 库并从 canonical 的 app.config
settings 取密钥材料。

密钥派生逻辑与 app.shared.encryption 保持一致：优先显式 ENCRYPTION_KEY，
否则由 SECRET_KEY 派生。两处实现因此产出同一把 Fernet 密钥，密文互通。
"""

import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import settings


def _get_fernet() -> Fernet:
    key = settings.encryption_key
    if not key:
        raw = hashlib.sha256(settings.secret_key.encode()).digest()
        key = base64.urlsafe_b64encode(raw).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_value(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    return _get_fernet().decrypt(ciphertext.encode()).decode()
