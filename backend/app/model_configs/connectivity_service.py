"""Connectivity checks for LLM, OCR, and other configured providers."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.model_configs.config_service import require_config
from app.model_configs.models import ModelConfig
from app.model_configs.presentation import safe_log_error


def safe_test_error(exc: Exception) -> tuple[str, str]:
    """Map provider errors to bounded, actionable, credential-safe messages."""
    raw = str(exc)
    lower = raw.lower()
    if any(
        token in lower
        for token in (
            "401",
            "unauthorized",
            "authentication",
            "invalid api key",
        )
    ):
        return (
            "AUTH_FAILED",
            "认证失败，请检查 API Key 是否正确且仍然有效",
        )
    if any(
        token in lower
        for token in (
            "404",
            "model_not_found",
            "model not found",
            "does not exist",
        )
    ):
        return (
            "MODEL_NOT_FOUND",
            "模型不存在或当前账号无权访问，请检查模型名",
        )
    if any(
        token in lower
        for token in ("429", "rate limit", "too many requests")
    ):
        return "RATE_LIMITED", "请求被限流，请稍后重试或检查账号额度"
    if any(token in lower for token in ("timeout", "timed out")):
        return "TIMEOUT", "连接超时，请检查接入地址和网络后重试"
    if any(
        token in lower
        for token in (
            "connection",
            "connect",
            "dns",
            "name or service not known",
        )
    ):
        return "NETWORK_ERROR", "无法连接到服务，请检查 API Base 和网络"
    scrubbed = safe_log_error(raw)
    return (
        "CONNECTION_FAILED",
        f"连接失败：{scrubbed or '服务未返回可识别的错误信息'}",
    )


def save_test_result(
    db: Session,
    config: ModelConfig,
    ok: bool,
    message: str,
) -> str:
    tested_at = datetime.now(timezone.utc)
    config.last_test_status = "success" if ok else "error"
    config.last_tested_at = tested_at
    config.last_test_message = message[:500]
    db.commit()
    return tested_at.isoformat()


def _result(
    db: Session,
    config: ModelConfig,
    *,
    ok: bool,
    message: str,
    code: str,
) -> dict:
    return {
        "ok": ok,
        "response": message,
        "code": code,
        "tested_at": save_test_result(db, config, ok, message),
    }


def _test_ocr(db: Session, config: ModelConfig) -> dict:
    if config.provider == "easyocr":
        enabled = (
            os.getenv("ENABLE_OCR", "").lower() in ("1", "true", "yes")
            or bool((config.options or {}).get("enabled"))
        )
        if not enabled:
            return _result(
                db,
                config,
                ok=False,
                message="EasyOCR 运行开关未开启",
                code="OCR_DISABLED",
            )
        import easyocr  # noqa: F401

        return _result(
            db,
            config,
            ok=True,
            message="连接成功，EasyOCR 运行环境正常",
            code="OK",
        )
    if config.provider == "paddleocr":
        enabled = (
            os.getenv("ENABLE_OCR", "").lower() in ("1", "true", "yes")
            or os.getenv("ENABLE_PADDLEOCR", "").lower()
            in ("1", "true", "yes")
            or bool((config.options or {}).get("enabled"))
        )
        if not enabled:
            return _result(
                db,
                config,
                ok=False,
                message="PaddleOCR 运行开关未开启",
                code="OCR_DISABLED",
            )
        from paddleocr import PaddleOCR  # noqa: F401

        return _result(
            db,
            config,
            ok=True,
            message="连接成功，PaddleOCR 运行环境正常",
            code="OK",
        )
    if config.provider == "external_api":
        if not config.api_base:
            raise ValueError("External OCR requires API Base")
        message = "配置检查通过，外部 OCR 接入地址有效"
    else:
        message = f"配置检查通过，OCR Provider：{config.provider}"
    return _result(
        db,
        config,
        ok=True,
        message=message,
        code="OK",
    )


def test_model(db: Session, model_id: str) -> dict:
    config = require_config(db, model_id)
    try:
        if (config.config_type or "llm") == "ocr":
            return _test_ocr(db, config)
        if (config.config_type or "llm") != "llm":
            return _result(
                db,
                config,
                ok=True,
                message=f"配置检查通过：{config.config_type}",
                code="OK",
            )

        from app.model_configs.llm_gateway import chat as llm_chat
        from app.services.model_config_selector import llm_call_kwargs

        call_kwargs = llm_call_kwargs(config)
        if not call_kwargs:
            raise ValueError(
                "Model config must include at least one model name",
            )
        call_kwargs.pop("model_config_id", None)
        call_kwargs["max_output_tokens"] = min(
            int(call_kwargs.get("max_output_tokens") or 16),
            16,
        )
        call_kwargs["timeout_seconds"] = 30
        llm_chat(
            call_kwargs,
            [{
                "role": "user",
                "content": "Connectivity check. Reply with exactly PONG.",
            }],
            [],
        )
        return _result(
            db,
            config,
            ok=True,
            message="连接成功，模型响应正常",
            code="OK",
        )
    except Exception as exc:
        code, message = safe_test_error(exc)
        return _result(
            db,
            config,
            ok=False,
            message=message,
            code=code,
        )
