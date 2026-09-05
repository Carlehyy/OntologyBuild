"""Pipeline catalog, authoring, archival, and version-query workflows.

The HTTP router owns authentication/dependency wiring.  This module owns the
transactional management use cases so CRUD and lifecycle rules do not drift
back into route handlers.  Patch-sensitive helpers are injected explicitly by
the router to preserve existing extension and test contracts.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.data_channel.pipelines.contracts import PipelineCreate, PipelineUpdate
from app.data_channel.steward.models import STATUS_ARCHIVED, N8nPipeline
from app.data_channel.pipelines.models import Pipeline, PipelineRun, PipelineVersion


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """数据库裸时间统一按 UTC 解释；带时区值统一转换为 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _shanghai_day_start_utc(local_day) -> datetime:
    """上海自然日零点转换为数据库使用的 UTC naive 边界。"""
    local_start = datetime.combine(
        local_day,
        datetime.min.time(),
        tzinfo=SHANGHAI_TZ,
    )
    return local_start.astimezone(timezone.utc).replace(tzinfo=None)


def _shanghai_date(value: datetime):
    return _as_utc(value).astimezone(SHANGHAI_TZ).date()


def create_pipeline(
    body: PipelineCreate,
    db: Session,
    current_user,
    *,
    format_pipeline_fn: Callable[[Pipeline], dict],
):
    """Create a draft pipeline while preserving the domain/name identity."""
    existing = db.query(Pipeline).filter(
        Pipeline.name == body.name,
        Pipeline.domain == body.domain,
    ).first()
    if existing:
        raise HTTPException(400, "已存在同名 Pipeline，请更换名称。")

    # 系统自定义（canvas）与 route A/B/C 已下线：新建必须声明受支持引擎
    engine = (body.definition or {}).get("engine")
    if engine not in {"n8n", "python"}:
        raise HTTPException(
            400,
            "仅支持创建 n8n 流水线或 Python 脚本流水线；"
            "系统自定义（canvas）流水线已下线。",
        )
    pipeline = Pipeline(
        name=body.name,
        domain=body.domain or "通用",
        description=body.description or "",
        source_dataset_id=body.source_dataset_id,
        route=body.route or "A",
        spec=body.spec or {},
        definition=body.definition,
        status="draft",
        branch="main",
        version=1,
        created_by=current_user.id,
    )
    db.add(pipeline)
    db.commit()
    db.refresh(pipeline)
    return format_pipeline_fn(pipeline)


def list_pipelines(
    *,
    search: str,
    domain: str,
    status: str,
    engine: str,
    enabled: bool | None,
    page: int,
    page_size: int,
    paginated: bool,
    db: Session,
    is_n8n_pipeline_fn: Callable[[Pipeline], bool],
    format_pipeline_fn: Callable[[Pipeline], dict],
):
    """Query the working catalog and attach task, n8n, and latest-run facts."""
    query = db.query(Pipeline)
    if search:
        query = query.filter(
            Pipeline.name.ilike(f"%{search}%")
            | Pipeline.id.ilike(f"%{search}%")
        )
    if domain:
        query = query.filter(Pipeline.domain == domain)
    if engine in {"n8n", "python"}:
        engine_value = Pipeline.definition["engine"].as_string()
        query = query.filter(engine_value == engine)
    if enabled is not None:
        query = query.filter(Pipeline.enabled.is_(enabled))
    if status:
        query = query.filter(Pipeline.status == status)
    else:
        query = query.filter(Pipeline.status != "archived")

    total = query.count()
    query = query.order_by(Pipeline.created_at.desc(), Pipeline.id.desc())
    if paginated:
        query = query.offset((page - 1) * page_size).limit(page_size)
    else:
        query = query.limit(100)
    pipeline_rows = query.all()
    pipeline_ids = [pipeline.id for pipeline in pipeline_rows]
    task_counts: dict[str, int] = {}
    if pipeline_ids:
        from app.data_channel.pipeline_tasks.models import PipelineTask

        task_counts = dict(
            db.query(PipelineTask.pipeline_id, func.count(PipelineTask.id))
            .filter(PipelineTask.pipeline_id.in_(pipeline_ids))
            .group_by(PipelineTask.pipeline_id)
            .all()
        )

    # 当前页流水线的 n8n 治理记录与最近运行各用一次批量查询取回，
    # 避免逐条流水线一次 N8nPipeline + 一次 last_run 的 N+1。
    steward_ids = []
    for pipeline in pipeline_rows:
        if is_n8n_pipeline_fn(pipeline):
            steward_id = (
                (pipeline.definition or {}).get("n8n") or {}
            ).get("steward_id")
            if steward_id:
                steward_ids.append(steward_id)
    n8n_records = {}
    if steward_ids:
        n8n_records = {
            record.id: record
            for record in db.query(N8nPipeline)
            .filter(N8nPipeline.id.in_(steward_ids))
            .all()
        }
    last_runs = {}
    if pipeline_ids:
        latest_created = (
            db.query(
                PipelineRun.pipeline_id,
                func.max(PipelineRun.created_at).label("mx"),
            )
            .filter(PipelineRun.pipeline_id.in_(pipeline_ids))
            .group_by(PipelineRun.pipeline_id)
            .subquery()
        )
        # 只取展示列：stats 是重 JSON 列，不参与最近运行摘要
        last_run_rows = (
            db.query(
                PipelineRun.pipeline_id,
                PipelineRun.status,
                PipelineRun.started_at,
                PipelineRun.error_log,
            )
            .join(
                latest_created,
                (PipelineRun.pipeline_id == latest_created.c.pipeline_id)
                & (PipelineRun.created_at == latest_created.c.mx),
            )
            .all()
        )
        for row in last_run_rows:
            last_runs.setdefault(row.pipeline_id, row)

    results = []
    for pipeline in pipeline_rows:
        item = format_pipeline_fn(pipeline)
        item["task_count"] = int(task_counts.get(pipeline.id, 0))
        if is_n8n_pipeline_fn(pipeline):
            n8n_definition = (pipeline.definition or {}).get("n8n") or {}
            steward_id = n8n_definition.get("steward_id")
            record = n8n_records.get(steward_id) if steward_id else None
            if record:
                item["definition"] = dict(item.get("definition") or {})
                item["definition"]["n8n"] = {
                    **n8n_definition,
                    "n8n_workflow_id": record.n8n_workflow_id,
                }
        last_run = last_runs.get(pipeline.id)
        if last_run:
            item["last_run_status"] = last_run.status
            item["last_run_at"] = (
                last_run.started_at.isoformat()
                if last_run.started_at
                else None
            )
            item["last_run_error"] = last_run.error_log or ""
        results.append(item)
    if paginated:
        return {
            "items": results,
            "total": total,
            "page": page,
            "page_size": page_size,
            "overview": pipeline_overview(db),
        }
    return results


def pipeline_overview(
    db: Session,
    *,
    now_utc_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Return unfiltered catalog health counters for the list-page header.

    The overview deliberately ignores the current table filters: it is the
    user's stable system-health anchor while the paginated rows below it may be
    narrowed to a subset.  ``latest_failed`` counts pipelines whose most recent
    run failed, rather than every historical failed run.  ``trend_7d`` counts
    every run of a non-archived pipeline (manual and task-triggered alike)
    over the last 7 Shanghai calendar days — real observations only, never
    synthesized series.
    """
    now_fn = now_utc_fn or _now_utc
    active = Pipeline.status != "archived"
    totals = (
        db.query(
            func.count(Pipeline.id).label("total"),
            func.coalesce(func.sum(case(
                (Pipeline.status == "published", 1), else_=0,
            )), 0).label("published"),
            func.coalesce(func.sum(case(
                (Pipeline.enabled.is_(True), 1), else_=0,
            )), 0).label("enabled"),
        )
        .filter(active)
        .one()
    )

    latest_created = (
        db.query(
            PipelineRun.pipeline_id,
            func.max(PipelineRun.created_at).label("mx"),
        )
        .group_by(PipelineRun.pipeline_id)
        .subquery()
    )
    latest_failed = (
        db.query(func.count(func.distinct(PipelineRun.pipeline_id)))
        .join(
            latest_created,
            (PipelineRun.pipeline_id == latest_created.c.pipeline_id)
            & (PipelineRun.created_at == latest_created.c.mx),
        )
        .join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
        .filter(active, PipelineRun.status == "failed")
        .scalar()
        or 0
    )

    local_today = now_fn().astimezone(SHANGHAI_TZ).date()
    first_day = local_today - timedelta(days=6)
    trend = {
        (first_day + timedelta(days=index)).isoformat(): {
            "runs": 0,
            "errors": 0,
        }
        for index in range(7)
    }
    recent = (
        db.query(PipelineRun.created_at, PipelineRun.status)
        .join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
        .filter(
            active,
            PipelineRun.created_at >= _shanghai_day_start_utc(first_day),
        )
        .all()
    )
    for created_at, run_status in recent:
        if not created_at:
            continue
        key = _shanghai_date(created_at).isoformat()
        if key not in trend:
            continue
        trend[key]["runs"] += 1
        if run_status == "failed":
            trend[key]["errors"] += 1

    return {
        "total": int(totals.total or 0),
        "published": int(totals.published or 0),
        "enabled": int(totals.enabled or 0),
        "latest_failed": int(latest_failed),
        "trend_7d": [
            {
                "date": day,
                "runs": counts["runs"],
                "errors": counts["errors"],
            }
            for day, counts in trend.items()
        ],
    }


def get_pipeline(
    pipeline_id: str,
    db: Session,
    *,
    format_pipeline_fn: Callable[[Pipeline], dict],
):
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")
    return format_pipeline_fn(pipeline)


def update_pipeline(
    pipeline_id: str,
    body: PipelineUpdate,
    db: Session,
    *,
    is_n8n_pipeline_fn: Callable[[Pipeline], bool],
    column_definitions_hash_fn: Callable[[Any], str],
    pipeline_execution_hash_fn: Callable[..., str],
    invalidate_publish_attestation_fn: Callable[[Pipeline], None],
    format_pipeline_fn: Callable[[Pipeline], dict],
):
    """Apply the mutable draft/display contract in one database transaction."""
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")

    update_data = body.model_dump(exclude_unset=True)
    n8n_allowed = {"name", "description", "domain", "column_definitions"}
    if is_n8n_pipeline_fn(pipeline):
        blocked = sorted(set(update_data) - n8n_allowed)
        if blocked:
            raise HTTPException(
                400,
                f"该流水线由数据管家托管（n8n 引擎），字段 {', '.join(blocked)} "
                "请在数据管家对话中修改。",
            )

        if "column_definitions" in update_data:
            from app.data_channel.steward import service as steward_service

            record = steward_service.record_for_pipeline(db, pipeline)
            attestation = (
                steward_service.validation_attestation(record)
                if record is not None
                else None
            )
            definitions_changed = (
                attestation
                and attestation.get("column_definitions_hash")
                != column_definitions_hash_fn(
                    update_data.get("column_definitions")
                )
            )
            if definitions_changed:
                steward_service.invalidate_validation_attestation(record)

    if (pipeline.status or "") == "published" and update_data:
        blocked = sorted(set(update_data) - {"name", "description"})
        if blocked:
            raise HTTPException(
                409,
                "流水线已发布，名称与描述仍可修改，但编排、字段契约及数据源配置已封版。"
                f"不可修改字段：{', '.join(blocked)}。",
            )

    # Python 脚本流水线的脚本只经脚本保存端点写入（该端点会重新执行并校验
    # 输出格式）；通用 update 不接受 definition 变更，绕开格式门禁。
    if (
        (pipeline.definition or {}).get("engine") == "python"
        and "definition" in update_data
    ):
        raise HTTPException(
            400,
            "该流水线是 Python 脚本流水线：脚本请在脚本编辑页修改并保存"
            "（保存会重新执行并校验输出格式）。",
        )

    if "column_definitions" in update_data:
        from app.data_channel.datasets.lake_gate import (
            normalize_definitions,
            validate_contract_structure,
        )

        structure_errors = validate_contract_structure(
            update_data.get("column_definitions")
        )
        if structure_errors:
            raise HTTPException(
                400,
                f"字段契约结构非法：{'；'.join(structure_errors)}。"
                "请回到「设置主键组」修正。",
            )
        update_data["column_definitions"] = normalize_definitions(
            update_data.get("column_definitions")
        )

    if (
        not is_n8n_pipeline_fn(pipeline)
        and pipeline.validation_attestation
    ):
        prospective_execution_hash = pipeline_execution_hash_fn(
            definition=update_data.get("definition", pipeline.definition),
            source_dataset_id=update_data.get(
                "source_dataset_id", pipeline.source_dataset_id
            ),
            route=update_data.get("route", pipeline.route),
            spec=update_data.get("spec", pipeline.spec),
        )
        definitions_hash = column_definitions_hash_fn(
            update_data.get(
                "column_definitions", pipeline.column_definitions
            )
        )
        if (
            prospective_execution_hash
            != pipeline.validation_attestation.get("execution_hash")
            or definitions_hash
            != pipeline.validation_attestation.get(
                "column_definitions_hash"
            )
        ):
            invalidate_publish_attestation_fn(pipeline)

    if "name" in update_data:
        new_name = (update_data.get("name") or "").strip()
        if not new_name:
            raise HTTPException(400, "流水线名称不能为空")
        duplicate = db.query(Pipeline).filter(
            Pipeline.name == new_name,
            Pipeline.domain == (update_data.get("domain") or pipeline.domain),
            Pipeline.id != pipeline.id,
        ).first()
        if duplicate:
            raise HTTPException(400, "已存在同名 Pipeline，请更换名称。")
        update_data["name"] = new_name

    for key, value in update_data.items():
        setattr(pipeline, key, value)
    pipeline.updated_at = datetime.now(timezone.utc)

    if (
        is_n8n_pipeline_fn(pipeline)
        and {"name", "description"} & set(update_data)
    ):
        steward_id = (
            ((pipeline.definition or {}).get("n8n") or {}).get("steward_id")
        )
        record = (
            db.query(N8nPipeline).filter(N8nPipeline.id == steward_id).first()
            if steward_id
            else None
        )
        if record:
            if "name" in update_data:
                record.name = pipeline.name
            if "description" in update_data:
                record.description = pipeline.description or ""

    db.commit()
    db.refresh(pipeline)
    return format_pipeline_fn(pipeline)


def delete_pipeline(
    pipeline_id: str,
    db: Session,
    *,
    is_n8n_pipeline_fn: Callable[[Pipeline], bool],
    pipeline_task_refs_fn: Callable[[Session, str], list],
    reject_sync_chain_refs_fn: Callable[..., None],
):
    """Archive governed n8n pipelines or unreferenced local-engine pipelines."""
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")

    if is_n8n_pipeline_fn(pipeline):
        from app.data_channel.steward import service as steward_service

        record = steward_service.record_for_pipeline(db, pipeline)
        if record is None:
            raise HTTPException(
                409,
                "n8n 流水线缺少数据管家治理记录；"
                "为避免破坏版本与运行审计链，归档已中止。",
            )
        try:
            client = steward_service.get_n8n_client(db)
            steward_service.archive(db, record, client)
        except steward_service.StewardError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"status": "archived", "id": pipeline_id}

    # 非 n8n 引擎（python 及已下线的 canvas 存量）统一「归档」语义：保留
    # 发布版本与运行记录的审计链，只是没有外部资源需要停用；被任务池/
    # 同步链引用时拒绝。
    references = pipeline_task_refs_fn(db, pipeline_id)
    if references:
        names = "、".join(task.name for task in references[:3])
        suffix = "…" if len(references) > 3 else ""
        raise HTTPException(
            400,
            f"流水线已被 {len(references)} 个调度任务引用"
            f"（{names}{suffix}），请先在数据任务池删除或改绑这些任务。",
        )
    reject_sync_chain_refs_fn(db, pipeline_id, action="归档")
    pipeline.status = "archived"
    pipeline.enabled = False
    pipeline.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "archived", "id": pipeline_id}


def _next_clone_name(
    db: Session,
    base_name: str,
    domain: str | None,
    *,
    n8n_name_taken: Callable[[str], bool] | None = None,
) -> str:
    """生成「原名_复制」序列中首个可用名称（重名自动 _复制2/_复制3 递增）。

    查重口径与 create/update 一致（Pipeline name+domain，不过滤归档行）；
    n8n 治理记录另有全局非归档唯一约束，由调用方传入额外查重。
    """
    for index in range(1, 100):
        suffix = "_复制" if index == 1 else f"_复制{index}"
        candidate = f"{(base_name or '')[:200 - len(suffix)]}{suffix}"
        clash = db.query(Pipeline).filter(
            Pipeline.name == candidate,
            Pipeline.domain == domain,
        ).first()
        if clash is None and not (n8n_name_taken and n8n_name_taken(candidate)):
            return candidate
    raise HTTPException(400, "可用克隆名称已耗尽，请先整理历史副本。")


def clone_pipeline(
    pipeline_id: str,
    db: Session,
    current_user,
    *,
    is_n8n_pipeline_fn: Callable[[Pipeline], bool],
    format_pipeline_fn: Callable[[Pipeline], dict],
):
    """克隆流水线结构为未发布、未启用的草稿副本（名称追加「_复制」）。

    python 引擎的载体是 definition 中的脚本，直接深拷贝行内定义；n8n 引擎
    的载体是远端 workflow，经数据管家在 n8n 复制（webhook 路径重新生成）
    并新建治理记录与影子行。发布期产物（validation_attestation /
    target_curated_ids / 运行记录）不复制，克隆体走自己的校验发布流程。
    """
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")

    if is_n8n_pipeline_fn(pipeline):
        from app.data_channel.steward import service as steward_service

        record = steward_service.record_for_pipeline(db, pipeline)
        if record is None:
            raise HTTPException(
                409,
                "n8n 流水线缺少数据管家治理记录，无法克隆；"
                "请先在数据管家中修复该流水线。",
            )

        def _n8n_name_taken(candidate: str) -> bool:
            return (
                db.query(N8nPipeline)
                .filter(
                    N8nPipeline.name == candidate,
                    N8nPipeline.status != STATUS_ARCHIVED,
                )
                .first()
                is not None
            )

        new_name = _next_clone_name(
            db, pipeline.name, pipeline.domain,
            n8n_name_taken=_n8n_name_taken,
        )
        try:
            client = steward_service.get_n8n_client(db)
            clone_record = steward_service.clone_managed_workflow(
                db, record, client,
                new_name=new_name,
                user_id=getattr(current_user, "id", None),
            )
        except steward_service.StewardError as exc:
            raise HTTPException(400, str(exc)) from exc
        clone = db.query(Pipeline).filter(
            Pipeline.id == clone_record.pipeline_id,
        ).first()
        # 影子行默认归属「智能编排」域；克隆口径是以原流水线为准复制所属域、
        # 描述与字段契约，让副本开箱即为可校验发布的草稿。
        clone.domain = pipeline.domain
        clone.description = pipeline.description or ""
        clone.column_definitions = copy.deepcopy(pipeline.column_definitions)
        clone.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(clone)
        return format_pipeline_fn(clone)

    if (pipeline.definition or {}).get("engine") != "python":
        raise HTTPException(
            400,
            "仅支持克隆 n8n 流水线或 Python 脚本流水线；该流水线引擎已下线。",
        )

    clone = Pipeline(
        name=_next_clone_name(db, pipeline.name, pipeline.domain),
        domain=pipeline.domain,
        description=pipeline.description or "",
        source_dataset_id=pipeline.source_dataset_id,
        route=pipeline.route,
        spec=copy.deepcopy(pipeline.spec or {}),
        definition=copy.deepcopy(pipeline.definition),
        column_definitions=copy.deepcopy(pipeline.column_definitions),
        status="draft",
        branch=pipeline.branch or "main",
        version=1,
        created_by=getattr(current_user, "id", None),
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return format_pipeline_fn(clone)


def reject_unpublish(pipeline_id: str, db: Session):
    """Keep the immutable release boundary explicit for legacy clients."""
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")
    if (pipeline.status or "") != "published":
        raise HTTPException(409, "流水线尚未发布，不存在撤回操作。")
    raise HTTPException(
        409,
        "已发布流水线是不可变版本，不支持撤回或重新编辑。"
        "如需变更，请新建流水线；旧版本可先停用，确认替代版本稳定后归档。",
    )


def list_versions(pipeline_id: str, db: Session):
    versions = db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_id == pipeline_id
    ).order_by(PipelineVersion.version.desc()).all()
    return [
        {
            "id": version.id,
            "version": version.version,
            "status": version.status,
            "created_at": (
                version.created_at.isoformat()
                if version.created_at
                else None
            ),
        }
        for version in versions
    ]
