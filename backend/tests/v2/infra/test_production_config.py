"""Backward-compatible production configuration gates."""

from app.shared.config import Settings, production_config_errors


def _production_settings(**updates):
    values = {
        "environment": "production",
        "database_url": "postgresql://app:strong-password@db:5432/app",
        "secret_key": "0123456789abcdef0123456789abcdef",
        "encryption_key": "",
        "cors_allowed_origins": "",
        "first_admin_password": "strong-admin-password",
        "neo4j_password": "strong-neo4j-password",
        "minio_access_key": "ontology-minio",
        "minio_secret_key": "strong-minio-password",
        "storage_local_fallback": False,
        "allow_public_registration": False,
    }
    values.update(updates)
    return Settings(**values)


def test_existing_production_can_keep_secret_key_derived_encryption():
    assert production_config_errors(_production_settings()) == []


def test_explicit_encryption_key_must_still_be_valid_fernet():
    errors = production_config_errors(
        _production_settings(encryption_key="not-a-fernet-key"))
    assert "ENCRYPTION_KEY must be a valid Fernet key" in errors


def test_wildcard_cors_remains_blocked_but_empty_is_same_origin():
    errors = production_config_errors(
        _production_settings(cors_allowed_origins="*"))
    assert "CORS_ALLOWED_ORIGINS" in errors


def test_secret_key_derived_encryption_remains_decryptable(monkeypatch):
    from app.shared import encryption

    monkeypatch.setattr(encryption.settings, "encryption_key", "")
    monkeypatch.setattr(
        encryption.settings, "secret_key",
        "0123456789abcdef0123456789abcdef")
    ciphertext = encryption.encrypt("existing-connection-password")
    assert encryption.decrypt(ciphertext) == "existing-connection-password"
