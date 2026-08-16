"""流水线调度任务路由：CRUD、手动触发、执行历史、统计"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.data_channel.datasets.service import version_has_content
from app.data_channel.pipeline_tasks import (
    execution_service as _execution_service,
)
from app.data_channel.pipeline_tasks import (
    history_service as _history_service,
)
from app.data_channel.pipeline_tasks import (
    lifecycle_service as _lifecycle_service,
)
from app.data_channel.pipeline_tasks import cache as _cache
from app.data_channel.pipeline_tasks import query_service as _query_service
from app.data_channel.pipeline_tasks.contracts import (
    HistoryStatus,
    HistoryTriggerType,
    PipelineTaskCreate,
    PipelineTaskUpdate,
    WRITE_MODES,
)
from app.data_channel.pipeline_tasks.history_service import (
    _apply_history_filters,
    _history_item,
    _validate_history_query,
)
from app.data_channel.pipeline_tasks.lifecycle_service import (
    _refresh_scheduler,
)
from app.data_channel.pipeline_tasks.models import PipelineTask
from app.data_channel.pipeline_tasks.query_service import (
    SHANGHAI_TZ,
    _as_utc,
    _computed_next_run,
    _curated_columns,
    _last_impact_map,
    _live_next_run_map,
    _now_utc,
    _shanghai_date,
    _shanghai_day_start_utc,
    _utc_iso,
    _with_pipeline_info,
)
from app.data_channel.pipeline_tasks.validation_service import _validate
from app.config import settings
from app.deps import get_current_user, get_db
from app.models.v2.pipeline import Pipeline, PipelineRun


router = APIRouter(dependencies=[Depends(get_current_user)])


# ========== 固定路径（必须放在 /{task_id} 之前） ==========


@router.get("/selectable-pipelines")
def selectable_pipelines(db: Session = Depends(get_db)):
    """新建任务「选择流水线」阶段的候选：**已发布且已启用** 的流水线。

    每条流水线附带两份信息，前端一次拿齐：
      - contract：流水线字段契约（发布封版）——列清单与主键，任务侧只读消费
      - curated_datasets：已产出的成品数据集（id/名称/行数/湖中主键/列清单）
    有契约的流水线即使还没产出过数据也可选（首次入湖正是任务的职责）；
    没有契约的旧流水线仍要求已产出数据，否则无从得知列与主键范围。
    """
    return _query_service.selectable_pipelines(
        db,
        curated_columns_fn=_curated_columns,
        version_has_content_fn=version_has_content,
    )


@router.get("/stats")
def stats_overview(db: Session = Depends(get_db)):
    # 轮询热点：短 TTL 缓存，多客户端共享一次统计计算（fail-open 可降级）。
    return _cache.cached_call(
        _cache.stats_cache_key(),
        settings.pipeline_task_stats_cache_ttl_seconds,
        lambda: _query_service.stats_overview(
            db, now_utc_fn=_now_utc,
            shanghai_day_start_utc_fn=_shanghai_day_start_utc,
            shanghai_date_fn=_shanghai_date, utc_iso_fn=_utc_iso,
        ),
    )


# ========== CRUD ==========


@router.post("", status_code=201)
def create_task(
    body: PipelineTaskCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _lifecycle_service.create_task(
        body,
        db,
        current_user,
        validate_fn=_validate,
        refresh_scheduler_fn=_refresh_scheduler,
        with_pipeline_info_fn=_with_pipeline_info,
    )


@router.get("")
def list_tasks(
    search: Optional[str] = None,
    status: Optional[str] = None,
    enabled: Optional[bool] = None,
    pipeline_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    # 轮询热点：按参数指纹 + 版本键缓存，写操作 bump 版本即整体失效。
    return _cache.cached_call(
        _cache.list_cache_key({
            "search": search, "status": status, "enabled": enabled,
            "pipeline_id": pipeline_id, "page": page,
            "page_size": page_size,
        }),
        settings.pipeline_task_list_cache_ttl_seconds,
        lambda: _query_service.list_tasks(
            search, status, enabled, pipeline_id, page, page_size, db,
            with_pipeline_info_fn=_with_pipeline_info,
        ),
    )


@router.get("/pipeline-options")
def pipeline_filter_options(db: Session = Depends(get_db)):
    """任务池筛选候选：仅返回实际有关联任务的流水线及其任务数。"""
    return _cache.cached_call(
        _cache.options_cache_key(),
        settings.pipeline_task_options_cache_ttl_seconds,
        lambda: _query_service.pipeline_filter_options(db),
    )


@router.get("/histories")
def list_all_histories(
    search: Optional[str] = None,
    pipeline_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    status: Optional[HistoryStatus] = None,
    trigger_type: Optional[HistoryTriggerType] = None,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    db: Session = Depends(get_db),
):
    """分页查询任务池的全部执行记录，供全局历史弹窗使用。"""
    return _history_service.list_all_histories(
        search,
        pipeline_id,
        page,
        page_size,
        status,
        trigger_type,
        created_from,
        created_to,
        db,
        validate_history_query_fn=_validate_history_query,
        apply_history_filters_fn=_apply_history_filters,
        history_item_fn=_history_item,
    )


@router.get("/{task_id}")
def get_task(
    task_id: str,
    db: Session = Depends(get_db),
):
    return _query_service.get_task(
        task_id,
        db,
        with_pipeline_info_fn=_with_pipeline_info,
    )


@router.put("/{task_id}")
def update_task(
    task_id: str,
    body: PipelineTaskUpdate,
    db: Session = Depends(get_db),
):
    return _lifecycle_service.update_task(
        task_id,
        body,
        db,
        validate_fn=_validate,
        refresh_scheduler_fn=_refresh_scheduler,
        with_pipeline_info_fn=_with_pipeline_info,
    )


@router.delete("/{task_id}")
def delete_task(
    task_id: str,
    db: Session = Depends(get_db),
):
    return _lifecycle_service.delete_task(
        task_id,
        db,
        refresh_scheduler_fn=_refresh_scheduler,
    )


@router.post("/{task_id}/toggle")
def toggle_task(
    task_id: str,
    enabled: bool,
    db: Session = Depends(get_db),
):
    return _lifecycle_service.toggle_task(
        task_id,
        enabled,
        db,
        refresh_scheduler_fn=_refresh_scheduler,
    )


@router.post("/{task_id}/trigger")
def trigger_task(
    task_id: str,
    background: BackgroundTasks,
    sync: bool = False,
    db: Session = Depends(get_db),
    full_refresh: bool = False,
):
    return _execution_service.trigger_task(
        task_id,
        background,
        sync,
        db,
        full_refresh,
    )


@router.get("/{task_id}/histories")
def list_histories(
    task_id: str,
    page: int = 1,
    page_size: int = 10,
    status: Optional[HistoryStatus] = None,
    trigger_type: Optional[HistoryTriggerType] = None,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    db: Session = Depends(get_db),
):
    return _history_service.list_histories(
        task_id,
        page,
        page_size,
        status,
        trigger_type,
        created_from,
        created_to,
        db,
        validate_history_query_fn=_validate_history_query,
        apply_history_filters_fn=_apply_history_filters,
        history_item_fn=_history_item,
    )


@router.get("/{task_id}/runs/{run_id}/audit")
def run_audit(
    task_id: str,
    run_id: str,
    db: Session = Depends(get_db),
):
    """单次执行的完整审计明细：配置快照、流水线输出样本、资产湖行级影响。

    支撑「逐个追溯审计」：调用哪条流水线、输出是什么、当时配置是什么、
    最终对资产湖新增/更新/删除了哪些行。
    """
    return _history_service.run_audit(task_id, run_id, db)
