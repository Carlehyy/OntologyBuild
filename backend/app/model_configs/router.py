"""HTTP adapter for model configuration management."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.model_configs import (
    config_service,
    connectivity_service,
    presentation,
    usage_query_service,
)
from app.model_configs.schemas import (
    ModelConfigCreate,
    ModelConfigImportRequest,
    ModelConfigUpdate,
    ModelEnabledRequest,
)
from app.models.user import User


router = APIRouter()

# Preserve historical private import/patch targets while implementations live
# in cohesive modules.
_set_default = config_service.set_default
_ensure_default = config_service.ensure_default
_name_exists = config_service.name_exists
_require_tested = config_service.require_tested
_safe_test_error = connectivity_service.safe_test_error
_safe_log_error = presentation.safe_log_error
_utc_naive = presentation.utc_naive
_save_test_result = connectivity_service.save_test_result
_commit_config_change = config_service.commit_config_change
_model_out = presentation.model_out
_iso_utc = presentation.iso_utc


@router.get("")
def list_models(
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return {"data": config_service.list_models(db)}


@router.post("", status_code=201)
def create_model(
    body: ModelConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {
        "data": config_service.create_model(db, body, current_user),
    }


@router.post("/import", status_code=201)
def import_models(
    body: ModelConfigImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """原子导入不含密钥的配置。为安全起见，所有导入项均保持停用和待测试。"""
    return {
        "data": config_service.import_models(db, body, current_user),
    }


@router.get("/{model_id}")
def get_model(
    model_id: str,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return {"data": config_service.get_model(db, model_id)}


@router.put("/{model_id}")
def update_model(
    model_id: str,
    body: ModelConfigUpdate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return {
        "data": config_service.update_model(db, model_id, body),
    }


@router.delete("/{model_id}", status_code=204)
def delete_model(
    model_id: str,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    config_service.delete_model(db, model_id)


@router.post("/{model_id}/default")
def set_default_model(
    model_id: str,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return {
        "data": config_service.select_default(db, model_id),
    }


@router.post("/{model_id}/enabled")
def set_model_enabled(
    model_id: str,
    body: ModelEnabledRequest,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return {
        "data": config_service.set_enabled(
            db,
            model_id,
            body.enabled,
        ),
    }


@router.post("/{model_id}/test")
def test_model(
    model_id: str,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return {
        "data": connectivity_service.test_model(db, model_id),
    }


@router.get("/{model_id}/stats")
def get_model_stats(
    model_id: str,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """返回模型调用统计 — 用于模型卡片展示。"""
    return {
        "data": usage_query_service.get_model_stats(db, model_id),
    }


@router.get("/{model_id}/calls")
def list_model_calls(
    model_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str = "",
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """按模型配置分页查询业务调用日志，最新调用优先。"""
    return {
        "data": usage_query_service.list_model_calls(
            db,
            model_id,
            page=page,
            page_size=page_size,
            status=status,
            start=start,
            end=end,
        ),
    }
