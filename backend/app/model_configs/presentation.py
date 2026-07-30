"""Serialization and redaction helpers for model configuration responses."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.model_configs.models import ModelConfig
from app.model_configs.schemas import ModelConfigOut


def safe_log_error(raw: str | None) -> str | None:
    """Redact credentials and bound provider error messages."""
    if not raw:
        return None
    scrubbed = re.sub(
        r"(?i)(sk-[A-Za-z0-9_-]{8,}|bearer\s+[A-Za-z0-9._-]+|"
        r"api[_-]?key\s*[=:]\s*[^\s,;]+)",
        "[已隐藏]",
        raw,
    )
    return " ".join(scrubbed.split())[:240] or None


def utc_naive(value: datetime | None) -> datetime | None:
    """Normalize an API timestamp to the database's naive UTC convention."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def iso_utc(value) -> str | None:
    """Serialize a database timestamp with an explicit UTC offset."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def model_out(config: ModelConfig) -> dict:
    data = ModelConfigOut.model_validate(config).model_dump()
    data["has_api_key"] = bool(config.api_key_encrypted)
    data["created_at"] = iso_utc(config.created_at)
    data["updated_at"] = iso_utc(config.updated_at)
    data["last_tested_at"] = iso_utc(config.last_tested_at)
    return data
