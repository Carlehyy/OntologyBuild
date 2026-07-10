from cryptography.fernet import Fernet
from app.config import settings

def _get_fernet():
    key = settings.encryption_key
    if not key:
        # Backward-compatible production behavior: existing encrypted
        # connection credentials use this stable SECRET_KEY-derived key.
        # Introducing a standalone key requires an explicit re-encryption job.
        import base64, hashlib
        raw = hashlib.sha256(settings.secret_key.encode()).digest()
        key = base64.urlsafe_b64encode(raw).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)

def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()

def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    return _get_fernet().decrypt(ciphertext.encode()).decode()
