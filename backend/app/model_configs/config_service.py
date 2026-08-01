"""Model configuration lifecycle and default-selection transactions."""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.model_configs.models import ModelConfig
from app.model_configs.presentation import model_out
from app.model_configs.schemas import (
    ModelConfigCreate,
    ModelConfigImportRequest,
    ModelConfigUpdate,
)
from app.services.encryption_service import encrypt


def set_default(db: Session, config: ModelConfig) -> None:
    if (config.config_type or "llm") != "llm":
        raise HTTPException(409, "只有 LLM 配置可以设为默认模型")
    db.query(ModelConfig).filter(
        ModelConfig.config_type == "llm",
        ModelConfig.id != config.id,
    ).update(
        {ModelConfig.is_default: False},
        synchronize_session=False,
    )
    config.is_default = True


def ensure_default(db: Session) -> None:
    existing = (
        db.query(ModelConfig)
        .filter(
            ModelConfig.config_type == "llm",
            ModelConfig.enabled.is_(True),
            ModelConfig.is_default.is_(True),
        )
        .first()
    )
    if existing:
        return
    fallback = (
        db.query(ModelConfig)
        .filter(
            ModelConfig.config_type == "llm",
            ModelConfig.enabled.is_(True),
        )
        .order_by(
            case((ModelConfig.last_test_status == "success", 0), else_=1),
            ModelConfig.updated_at.desc(),
        )
        .first()
    )
    if fallback:
        fallback.is_default = True


def name_exists(
    db: Session,
    name: str,
    exclude_id: str | None = None,
) -> bool:
    query = db.query(ModelConfig.id).filter(
        func.lower(ModelConfig.name) == name.strip().lower(),
    )
    if exclude_id:
        query = query.filter(ModelConfig.id != exclude_id)
    return query.first() is not None


def require_tested(config: ModelConfig, action: str) -> None:
    if config.last_test_status != "success":
        raise HTTPException(409, f"请先完成连通性测试，再{action}")


def commit_config_change(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409,
            "模型配置状态发生并发变更，请刷新后重试",
        ) from exc


def require_config(db: Session, model_id: str) -> ModelConfig:
    config = (
        db.query(ModelConfig)
        .filter(ModelConfig.id == model_id)
        .first()
    )
    if not config:
        raise HTTPException(404, "Not found")
    return config


def list_models(db: Session) -> list[dict]:
    configs = (
        db.query(ModelConfig)
        .order_by(ModelConfig.updated_at.desc())
        .all()
    )
    return [model_out(config) for config in configs]


def create_model(db: Session, body: ModelConfigCreate, current_user) -> dict:
    if name_exists(db, body.name):
        raise HTTPException(409, "模型配置名称已存在，请使用不同名称")
    config = ModelConfig(
        id=str(uuid.uuid4()),
        name=body.name,
        config_type=body.config_type or "llm",
        provider=body.provider,
        api_base=body.api_base,
        api_key_encrypted=encrypt(body.api_key or ""),
        models=body.models,
        options=body.options or {},
        enabled=body.enabled,
        is_default=body.is_default,
        created_by=current_user.id,
    )
    db.add(config)
    db.flush()
    if config.enabled:
        require_tested(config, "启用")
    commit_config_change(db)
    db.refresh(config)
    return model_out(config)


def import_models(
    db: Session,
    body: ModelConfigImportRequest,
    current_user,
) -> dict:
    """Atomically import keyless configurations in disabled, untested state."""
    names = [item.name.strip().lower() for item in body.configs]
    if len(names) != len(set(names)):
        raise HTTPException(409, "导入文件中存在重复的配置名称")
    conflicts = {
        name
        for (name,) in (
            db.query(func.lower(ModelConfig.name))
            .filter(func.lower(ModelConfig.name).in_(names))
            .all()
        )
    }
    if conflicts:
        raise HTTPException(
            409,
            f"以下配置名称已存在：{', '.join(sorted(conflicts))}",
        )

    created: list[ModelConfig] = []
    try:
        for item in body.configs:
            config = ModelConfig(
                id=str(uuid.uuid4()),
                name=item.name,
                config_type=item.config_type or "llm",
                provider=item.provider,
                api_base=item.api_base,
                api_key_encrypted=encrypt(""),
                models=item.models,
                options=item.options or {},
                enabled=False,
                is_default=False,
                created_by=current_user.id,
            )
            db.add(config)
            created.append(config)
        db.flush()
        commit_config_change(db)
    except Exception:
        db.rollback()
        raise
    for config in created:
        db.refresh(config)
    return {
        "imported": len(created),
        "configs": [model_out(config) for config in created],
        "warning": (
            "API Key 不会随配置文件导入，所有导入项已保持停用，"
            "请补充密钥并测试后启用。"
        ),
    }


def get_model(db: Session, model_id: str) -> dict:
    return model_out(require_config(db, model_id))


def update_model(
    db: Session,
    model_id: str,
    body: ModelConfigUpdate,
) -> dict:
    config = require_config(db, model_id)
    original_connection = (
        config.config_type,
        config.provider,
        config.api_base or "",
        tuple(config.models or []),
        config.api_key_encrypted or "",
    )
    if (
        body.name is not None
        and body.name.strip().lower() != config.name.strip().lower()
        and name_exists(db, body.name, config.id)
    ):
        raise HTTPException(409, "模型配置名称已存在，请使用不同名称")
    if body.name is not None:
        config.name = body.name
    if body.config_type is not None:
        config.config_type = body.config_type
    if body.provider is not None:
        config.provider = body.provider
    if body.api_key is not None:
        config.api_key_encrypted = encrypt(body.api_key)
    if body.api_base is not None:
        config.api_base = body.api_base
    if body.models is not None:
        config.models = body.models
    if body.options is not None:
        config.options = body.options
    current_connection = (
        config.config_type,
        config.provider,
        config.api_base or "",
        tuple(config.models or []),
        config.api_key_encrypted or "",
    )
    connection_changed = current_connection != original_connection
    if (config.config_type or "llm") == "llm" and not (config.models or []):
        raise HTTPException(422, "LLM 配置必须填写模型名")
    if connection_changed:
        config.last_test_status = None
        config.last_tested_at = None
        config.last_test_message = "连接参数已变更，请重新测试"
        if config.enabled:
            config.enabled = False
        if config.is_default:
            config.is_default = False
            ensure_default(db)
    if body.enabled is not None:
        if body.enabled:
            require_tested(config, "启用")
        config.enabled = body.enabled
    if body.is_default is True:
        if not config.enabled:
            raise HTTPException(409, "请先启用模型，再设为默认")
        require_tested(config, "设为默认")
        set_default(db, config)
    elif body.is_default is False and config.is_default:
        config.is_default = False
        ensure_default(db)
    if (
        config.is_default
        and (
            (config.config_type or "llm") != "llm"
            or not config.enabled
        )
    ):
        config.is_default = False
        ensure_default(db)
    commit_config_change(db)
    db.refresh(config)
    return model_out(config)


def delete_model(db: Session, model_id: str) -> None:
    config = require_config(db, model_id)
    was_default = config.is_default
    db.delete(config)
    if was_default:
        ensure_default(db)
    commit_config_change(db)


def select_default(db: Session, model_id: str) -> dict:
    config = require_config(db, model_id)
    if not config.enabled:
        raise HTTPException(409, "请先启用模型，再设为默认")
    require_tested(config, "设为默认")
    set_default(db, config)
    commit_config_change(db)
    db.refresh(config)
    return model_out(config)


def set_enabled(db: Session, model_id: str, enabled: bool) -> dict:
    config = require_config(db, model_id)
    if enabled:
        require_tested(config, "启用")
    config.enabled = enabled
    if not config.enabled and config.is_default:
        config.is_default = False
        ensure_default(db)
    commit_config_change(db)
    db.refresh(config)
    return model_out(config)
