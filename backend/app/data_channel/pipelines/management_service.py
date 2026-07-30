"""Pipeline catalog, authoring, archival, and version-query workflows.

The HTTP router owns authentication/dependency wiring.  This module owns the
transactional management use cases so CRUD and lifecycle rules do not drift
back into route handlers.  Patch-sensitive helpers are injected explicitly by
the router to preserve existing extension and test contracts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.data_channel.pipelines.contracts import PipelineCreate, PipelineUpdate
from app.data_channel.steward.models import N8nPipeline
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineVersion


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

    inferred_route = body.route
    if not inferred_route and body.definition:
        nodes = body.definition.get("nodes", [])
        types = {node.get("type") for node in nodes if node.get("type")}
        if "transform" in types:
            # The current route contract deliberately keeps the default path.
            pass
        inferred_route = inferred_route or "A"
    pipeline = Pipeline(
        name=body.name,
        domain=body.domain or "通用",
        description=body.description or "",
        source_dataset_id=body.source_dataset_id,
        route=inferred_route or "A",
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
    if engine in {"n8n", "canvas"}:
        engine_value = Pipeline.definition["engine"].as_string()
        if engine == "n8n":
            query = query.filter(engine_value == "n8n")
        else:
            query = query.filter(or_(
                Pipeline.definition.is_(None),
                engine_value.is_(None),
                engine_value != "n8n",
            ))
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

    results = []
    for pipeline in pipeline_rows:
        item = format_pipeline_fn(pipeline)
        item["task_count"] = int(task_counts.get(pipeline.id, 0))
        if is_n8n_pipeline_fn(pipeline):
            n8n_definition = (pipeline.definition or {}).get("n8n") or {}
            steward_id = n8n_definition.get("steward_id")
            if steward_id:
                record = db.query(N8nPipeline).filter(
                    N8nPipeline.id == steward_id
                ).first()
                if record:
                    item["definition"] = dict(item.get("definition") or {})
                    item["definition"]["n8n"] = {
                        **n8n_definition,
                        "n8n_workflow_id": record.n8n_workflow_id,
                    }
        last_run = db.query(PipelineRun).filter(
            PipelineRun.pipeline_id == pipeline.id
        ).order_by(PipelineRun.created_at.desc()).first()
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
        }
    return results


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
    invalidate_canvas_attestation_fn: Callable[[Pipeline], None],
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
            invalidate_canvas_attestation_fn(pipeline)

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
    """Archive governed n8n pipelines or delete unreferenced canvas drafts."""
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

    references = pipeline_task_refs_fn(db, pipeline_id)
    if references:
        names = "、".join(task.name for task in references[:3])
        suffix = "…" if len(references) > 3 else ""
        raise HTTPException(
            400,
            f"流水线已被 {len(references)} 个调度任务引用"
            f"（{names}{suffix}），请先在数据任务池删除或改绑这些任务。",
        )
    reject_sync_chain_refs_fn(db, pipeline_id, action="删除")
    db.query(PipelineRun).filter(
        PipelineRun.pipeline_id == pipeline_id
    ).delete()
    db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_id == pipeline_id
    ).delete()
    db.delete(pipeline)
    db.commit()
    return {"status": "deleted", "id": pipeline_id}


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
