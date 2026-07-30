"""Pipeline execution and serialization helpers independent of HTTP routing."""
from __future__ import annotations

import socket
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.data_channel.pipelines.contracts import EnabledBody, PreviewStepBody
from app.models.v2.pipeline import Pipeline, PipelineRun


DRY_RUN_BUCKET = "raw-datasets"


def dry_run_uri(pipeline_id: str, dry_run_id: str) -> str:
    """Rebuild a staged-output URI without trusting a client-provided path."""
    uuid.UUID(dry_run_id)
    return f"s3://{DRY_RUN_BUCKET}/dry-runs/{pipeline_id}/{dry_run_id}.json"


def ensure_broker_reachable(timeout: float = 2.0) -> None:
    """Fail quickly when the Celery broker cannot accept a connection."""
    parsed = urlparse(settings.redis_url)
    sock = socket.create_connection(
        (parsed.hostname or "localhost", parsed.port or 6379),
        timeout=timeout,
    )
    sock.close()


def format_pipeline(pipeline: Pipeline) -> dict:
    return {
        "id": pipeline.id,
        "name": pipeline.name,
        "domain": pipeline.domain or "通用",
        "description": pipeline.description or "",
        "source_dataset_id": pipeline.source_dataset_id,
        "route": pipeline.route,
        "spec": pipeline.spec or {},
        "definition": pipeline.definition,
        "status": pipeline.status or "draft",
        "engine": ((pipeline.definition or {}).get("engine") or "canvas"),
        "enabled": True if pipeline.enabled is None else bool(pipeline.enabled),
        "column_definitions": pipeline.column_definitions,
        "branch": pipeline.branch or "main",
        "version": pipeline.version or 1,
        "target_curated_ids": pipeline.target_curated_ids or [],
        "created_at": (
            pipeline.created_at.isoformat() if pipeline.created_at else None
        ),
        "updated_at": (
            pipeline.updated_at.isoformat() if pipeline.updated_at else None
        ),
    }


def enqueue_pipeline_run(
    pipeline_id: str,
    db: Session,
    *,
    require_production_executable_fn: Callable[[Pipeline], None],
    broker_check_fn: Callable[[], None],
) -> dict:
    """Create a run and enqueue the unchanged Celery task."""
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")
    require_production_executable_fn(pipeline)

    run = PipelineRun(
        pipeline_id=pipeline_id,
        status="pending",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        broker_check_fn()
        from app.tasks.v2.pipeline_run import pipeline_run_task

        pipeline_run_task.delay(pipeline_id, run.id)
    except Exception as exc:
        # Celery/Redis 不可用时立即标记失败，避免 run 永远停在 pending。
        # 运行失败只写 run，不动 pipeline.status（生命周期与运行态分离）
        run.status = "failed"
        run.error_log = f"任务派发失败 (Celery/Redis 不可用?): {exc}"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return {
            "run_id": run.id,
            "status": "failed",
            "error": run.error_log,
        }

    return {"run_id": run.id, "status": "pending"}


def list_pipeline_runs(pipeline_id: str, db: Session) -> list[dict]:
    runs = db.query(PipelineRun).filter(
        PipelineRun.pipeline_id == pipeline_id
    ).order_by(PipelineRun.created_at.desc()).all()
    return [
        {
            "id": run.id,
            "status": run.status,
            "stats": run.stats,
            "error_log": run.error_log or "",
            "started_at": (
                run.started_at.isoformat()
                if run.started_at
                else None
            ),
            "finished_at": (
                run.finished_at.isoformat()
                if run.finished_at
                else None
            ),
        }
        for run in runs
    ]


def get_pipeline_run(run_id: str, db: Session) -> dict:
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Run not found")
    return {
        "id": run.id,
        "status": run.status,
        "stats": run.stats,
        "error_log": run.error_log,
        "started_at": (
            run.started_at.isoformat()
            if run.started_at
            else None
        ),
        "finished_at": (
            run.finished_at.isoformat()
            if run.finished_at
            else None
        ),
    }


def run_pipeline_synchronously(
    pipeline_id: str,
    db: Session,
    *,
    require_production_executable_fn: Callable[[Pipeline], None],
) -> dict:
    """Run through the unchanged task entry without Celery dispatch."""
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")
    require_production_executable_fn(pipeline)

    run = PipelineRun(
        pipeline_id=pipeline_id,
        status="pending",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        from app.tasks.v2.pipeline_run import pipeline_run_task

        pipeline_run_task(pipeline_id, run.id)
        db.refresh(run)
        return {
            "run_id": run.id,
            "status": run.status,
            "stats": run.stats,
            "error": run.error_log,
        }
    except Exception as exc:
        run.status = "failed"
        run.error_log = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return {
            "run_id": run.id,
            "status": "failed",
            "error": str(exc),
        }


def set_pipeline_enabled(
    pipeline_id: str,
    body: EnabledBody,
    db: Session,
    *,
    task_refs_fn: Callable[[Session, str], list[Any]],
    is_n8n_pipeline_fn: Callable[[Pipeline], bool],
    format_pipeline_fn: Callable[[Pipeline], dict],
) -> dict:
    """Apply the published-pipeline enabled state and compensate n8n."""
    pipeline = db.query(Pipeline).filter(
        Pipeline.id == pipeline_id
    ).first()
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")
    if body.enabled and (pipeline.status or "") != "published":
        raise HTTPException(
            400,
            "只有已发布的流水线才能启用。请先在编辑向导中完成发布。",
        )

    current_enabled = (
        True
        if pipeline.enabled is None
        else bool(pipeline.enabled)
    )
    if bool(body.enabled) == current_enabled:
        return format_pipeline_fn(pipeline)

    refs = task_refs_fn(db, pipeline_id)
    if refs:
        names = "、".join(task.name for task in refs[:3])
        suffix = "…" if len(refs) > 3 else ""
        raise HTTPException(
            409,
            f"流水线「{pipeline.name}」已被 {len(refs)} 个数据任务关联"
            f"（{names}{suffix}），为避免影响任务调度，不允许更改启用状态。"
            "请先在数据任务池删除或改绑这些任务，解除关联后再操作。",
        )

    n8n_transition = None
    if is_n8n_pipeline_fn(pipeline):
        from app.data_channel.steward import service as steward_service

        record = steward_service.record_for_pipeline(db, pipeline)
        if record is None:
            raise HTTPException(
                409,
                "n8n 流水线缺少数据管家治理记录，无法安全启停。",
            )
        try:
            n8n_client = steward_service.get_n8n_client(db)
            previous_remote_active = (
                steward_service.set_published_enabled(
                    pipeline,
                    record,
                    n8n_client,
                    enabled=bool(body.enabled),
                )
            )
        except steward_service.StewardError as exc:
            raise HTTPException(400, str(exc))
        n8n_transition = (
            record,
            n8n_client,
            previous_remote_active,
        )

    pipeline.enabled = bool(body.enabled)
    pipeline.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        if n8n_transition is not None:
            record, n8n_client, previous_remote_active = n8n_transition
            try:
                steward_service.restore_remote_active(
                    pipeline,
                    record,
                    n8n_client,
                    enabled=previous_remote_active,
                    context="启停事务补偿",
                )
            except steward_service.StewardError as compensation_exc:
                raise HTTPException(
                    500,
                    f"平台启停事务失败（{exc}），且恢复 n8n 原状态失败"
                    f"（{compensation_exc}）。请立即人工核对。",
                ) from exc
        raise HTTPException(
            500,
            f"平台启停事务失败，n8n 原状态已恢复：{exc}",
        ) from exc
    db.refresh(pipeline)
    return format_pipeline_fn(pipeline)


def dry_run_pipeline(
    pipeline_id: str,
    db: Session,
    max_rows: int,
    *,
    is_n8n_pipeline_fn: Callable[[Pipeline], bool],
    dry_run_bucket: str,
) -> dict:
    """Execute and stage a preview without writing to the asset lake."""
    import json as json_module
    import uuid as uuid_module
    from datetime import datetime as datetime_type
    from datetime import timezone as timezone_type
    from types import SimpleNamespace

    pipeline = db.query(Pipeline).filter(
        Pipeline.id == pipeline_id
    ).first()
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")

    engine_meta: dict = {}
    try:
        if is_n8n_pipeline_fn(pipeline):
            from app.data_channel.steward.runner import (
                collect_n8n_rows,
                collect_test_rows,
                persist_test_result,
            )
            from app.data_channel.steward.service import record_for_pipeline
            from app.tasks.v2.pipeline_run import _strip_content

            record = record_for_pipeline(db, pipeline)
            if (
                record is not None
                and (pipeline.status or "") != "published"
            ):
                # 未发布的 workflow 未激活、生产 webhook 未注册——走数据管家的
                # 试跑通道（临时激活→触发→恢复），否则向导第 2 步必失败，
                # 未发布 n8n 永远设不了字段契约（先设契约、后发布的流程死锁）
                # 预览与发布凭证解耦：远端 n8n 版本若不返回 activeVersionId，
                # 已成功产生的输出仍应展示；缺失的发布证据只在第 3 步校验时阻断发布。
                rows, engine_meta = collect_test_rows(
                    db,
                    record,
                    require_publish_evidence=False,
                )
                if engine_meta.get("error"):
                    raise RuntimeError(
                        f"n8n 执行失败：{engine_meta['error']}"
                    )
                persist_test_result(
                    db,
                    record,
                    rows,
                    engine_meta,
                )
            else:
                # 已发布（或治理记录缺失时让 collect_n8n_rows 给出准确报错）
                rows, engine_meta = collect_n8n_rows(db, pipeline)
            outputs = [{
                "source": {
                    "dataset_id": None,
                    "filename": pipeline.name,
                    "route": "A",
                    "kind": "n8n",
                },
                "table_name": None,
                "rows": _strip_content(rows),
                "rows_in": len(rows),
                "rows_out": len(rows),
                "route": "A",
                "meta": {},
                "multi_source": False,
            }]
        else:
            from app.tasks.v2.pipeline_run import collect_pipeline_output

            outputs = collect_pipeline_output(db, pipeline)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"试运行失败：{exc}")

    from app.data_channel.datasets.lake_gate import LakeGateError, gate_rows
    from app.tasks.v2.pipeline_run import resolve_curated_target

    contract_definitions = (
        pipeline.column_definitions
        if len(outputs) == 1
        else None
    )
    preview = []
    for output in outputs:
        curated, derived_name = resolve_curated_target(
            db,
            pipeline,
            output["source"],
            output["multi_source"],
            output["table_name"],
        )
        dataset_name = (
            curated.name
            if curated is not None
            else derived_name
        )
        gate_error = None
        gate_info: dict = {
            "pk": "",
            "pk_source": "",
            "warnings": [],
            "drift": None,
        }
        preview_rows = output["rows"]
        try:
            gate = gate_rows(
                curated
                or SimpleNamespace(
                    name=dataset_name,
                    schema_json=None,
                ),
                output["rows"],
                None,
                column_definitions=contract_definitions,
            )
            preview_rows = gate["rows"]
            gate_info = {
                "pk": gate["pk"],
                "pk_source": gate["pk_source"],
                "warnings": gate["warnings"],
                "drift": gate["drift"],
            }
        except LakeGateError as exc:
            gate_error = str(exc)
        if (
            contract_definitions is None
            and (pipeline.column_definitions or [])
        ):
            gate_info["warnings"] = [
                *gate_info["warnings"],
                "多产物流水线暂不应用流水线级字段契约（契约粒度=单产物）",
            ]

        columns: list[str] = []
        for row in preview_rows[:50]:
            for key in row.keys():
                if key not in columns:
                    columns.append(key)
        preview.append({
            "dataset_name": dataset_name,
            "dataset_exists": curated is not None,
            "rows_out": output["rows_out"],
            "columns": columns,
            "sample": preview_rows[:max_rows],
            "gate_error": gate_error,
            **gate_info,
        })

    max_stage_rows = 100_000
    budget = max_stage_rows
    staged_outputs: list[dict] = []
    truncated = False
    for index, output in enumerate(outputs):
        output_rows = output.get("rows") or []
        keep = (
            output_rows
            if len(output_rows) <= budget
            else output_rows[:budget]
        )
        if len(keep) < len(output_rows):
            truncated = True
            if index < len(preview):
                preview[index]["warnings"] = [
                    *preview[index].get("warnings", []),
                    f"输出 {len(output_rows):,} 行超出暂存上限 "
                    f"{max_stage_rows:,} 行：「展开查看全部」与第 3 步"
                    f"全量校验仅覆盖前 {len(keep):,} 行",
                ]
        budget -= len(keep)
        staged_outputs.append({**output, "rows": keep})

    dry_run_id = str(uuid_module.uuid4())
    from app.data_channel.steward.service import canonical_json_hash

    output_checksum = canonical_json_hash(staged_outputs)
    payload = {
        "pipeline_id": pipeline.id,
        "dry_run_id": dry_run_id,
        "created_at": datetime_type.now(
            timezone_type.utc
        ).isoformat(),
        "engine_meta": engine_meta,
        "truncated": truncated,
        "outputs": staged_outputs,
        "output_checksum": output_checksum,
    }
    from app.services.storage_service import get_storage_service

    storage = get_storage_service()
    try:
        for uri in storage.list_prefix(
            dry_run_bucket,
            f"dry-runs/{pipeline.id}/",
        ):
            storage.delete_object(uri)
    except Exception:  # noqa: BLE001
        pass
    storage.put_bytes(
        dry_run_bucket,
        f"dry-runs/{pipeline.id}/{dry_run_id}.json",
        json_module.dumps(
            payload,
            ensure_ascii=False,
            default=str,
        ).encode("utf-8"),
        content_type="application/json",
    )

    total_out = sum(output["rows_out"] for output in outputs)
    return {
        "dry_run_id": dry_run_id,
        "engine": (
            (pipeline.definition or {}).get("engine")
            or "canvas"
        ),
        "rows_in": sum(output["rows_in"] for output in outputs),
        "rows_out": total_out,
        "outputs": preview,
    }


def dry_run_rows(
    pipeline_id: str,
    dry_run_id: str,
    output_index: int,
    page: int,
    page_size: int,
    db: Session,
    *,
    dry_run_uri_fn: Callable[[str, str], str],
) -> dict:
    """Read one page from the staged full dry-run output."""
    import json as json_module

    from app.data_channel.datasets.lake_gate import (
        LakeGateError,
        apply_column_contract,
    )

    pipeline = db.query(Pipeline).filter(
        Pipeline.id == pipeline_id
    ).first()
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")
    try:
        from app.services.storage_service import get_storage_service

        raw = get_storage_service().get_object(
            dry_run_uri_fn(pipeline_id, dry_run_id)
        )
        payload = json_module.loads(raw.decode("utf-8"))
    except ValueError:
        raise HTTPException(400, "非法的 dry_run_id")
    except Exception:
        raise HTTPException(404, "试运行结果不存在或已过期，请重新执行")
    if payload.get("pipeline_id") != pipeline_id:
        raise HTTPException(400, "试运行结果与流水线不匹配")

    outputs = payload.get("outputs") or []
    if output_index >= len(outputs):
        raise HTTPException(404, "产物序号超出范围")
    rows = [
        row
        for row in (outputs[output_index].get("rows") or [])
        if isinstance(row, dict)
    ]
    if len(outputs) == 1 and (pipeline.column_definitions or []):
        try:
            rows, _warnings = apply_column_contract(
                rows,
                pipeline.column_definitions,
            )
        except LakeGateError:
            pass

    columns: list[str] = []
    seen: set[str] = set()
    for row in rows[:200]:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                columns.append(str(key))
    start = (page - 1) * page_size
    return {
        "total": len(rows),
        "page": page,
        "page_size": page_size,
        "columns": columns,
        "rows": rows[start:start + page_size],
    }


def reject_dry_run_commit() -> None:
    """Keep the deprecated endpoint as an explicit no-write fence."""
    raise HTTPException(
        409,
        "试执行结果不能直接写入资产湖。请在数据任务池创建并执行任务，由任务统一完成入湖。",
    )


def preview_pipeline_step(body: PreviewStepBody) -> dict:
    """Preview one legacy transform step."""
    try:
        from app.services.v2.pipeline.base import PipelineContext
        from app.services.v2.pipeline.steps.cleansing import CleansingStep
        from app.services.v2.pipeline.steps.schema_inference import (
            SchemaInferenceStep,
        )

        context = PipelineContext(
            dataset_id="",
            version_no=1,
            route="A",
            spec={},
        )
        data = body.sample_data or [{"col": "sample"}]

        if body.op in (
            "drop_duplicates",
            "fill_nulls",
            "normalize_dates",
        ):
            step = CleansingStep()
            data = step.run(context, data)
        elif body.op == "schema_inference":
            step = SchemaInferenceStep()
            data = step.run(context, data)

        return {
            "op": body.op,
            "rows_in": len(body.sample_data),
            "rows_out": len(data),
            "preview": data[:20],
        }
    except Exception as exc:
        return {
            "op": body.op,
            "error": str(exc),
            "rows_in": 0,
            "rows_out": 0,
            "preview": [],
        }
