"""Python 脚本流水线的执行与保存 — HTTP 层业务逻辑。

「执行」= 内核试跑 + 平台行格式（list[dict]）复核，不写库；
「保存」= 双重保障的服务端一侧：重新执行并过格式门禁，通过才把脚本写入
``definition.python`` 并清空既有发布校验凭证（脚本变更必须重新预览+校验
才能发布，execution_hash 覆盖 definition 天然保证这一点）。
"""
from __future__ import annotations

from datetime import datetime, timezone
import threading

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.data_channel.pipelines.python_engine.client import (
    PythonEngineError,
    ScriptExecution,
    execute_script,
)
from app.models.v2.pipeline import Pipeline, PipelineScriptVersion

# 执行结果回传的样本行数（完整数据以 dry-run 暂存通道为准）
_SAMPLE_ROWS = 50
# 每条流水线保留的脚本历史版数上限（超出后最旧的版本被修剪）
_SCRIPT_VERSION_KEEP = 20

# 进行中的手动执行：key = f"{pipeline_id}:{user_id}"，值为取消事件。
# 「取消」端点置位后，执行循环在下一个轮询周期内终止并销毁内核。
_IN_FLIGHT: dict[str, threading.Event] = {}
_IN_FLIGHT_LOCK = threading.Lock()


def _in_flight_key(pipeline_id: str, current_user) -> str:
    return f"{pipeline_id}:{getattr(current_user, 'id', None) or 'anonymous'}"


def execute_pipeline_script(pipeline_id: str, body, db: Session, current_user=None) -> dict:
    """执行脚本并返回结果与格式校验结论（脚本级失败以 ok=false 承载）。"""
    pipeline = _load_python_pipeline(db, pipeline_id)
    script = (body.script or "")
    if not script.strip():
        raise HTTPException(400, "脚本内容为空，无法执行。")
    key = _in_flight_key(pipeline_id, current_user)
    cancel_event = threading.Event()
    with _IN_FLIGHT_LOCK:
        if key in _IN_FLIGHT:
            raise HTTPException(
                409,
                "该流水线有正在执行的脚本，请等待完成或先取消上一次执行。",
            )
        _IN_FLIGHT[key] = cancel_event
    try:
        execution = _run(script, cancel_event)
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.pop(key, None)
    return _execution_payload(pipeline, execution)


def cancel_pipeline_script(pipeline_id: str, db: Session, current_user=None) -> dict:
    """取消当前用户在该流水线上进行中的脚本执行（内核侧终止）。"""
    _load_python_pipeline(db, pipeline_id)
    key = _in_flight_key(pipeline_id, current_user)
    with _IN_FLIGHT_LOCK:
        event = _IN_FLIGHT.get(key)
    if event is None:
        return {"cancelled": False}
    event.set()
    return {"cancelled": True}


def save_pipeline_script(
    pipeline_id: str,
    body,
    db: Session,
    *,
    format_pipeline_fn,
    current_user=None,
) -> dict:
    """保存脚本：服务端重跑复验，执行成功且输出格式合法才落库。

    落库同时把该版脚本冻结进保存历史（v2_pipeline_script_versions），
    供脚本编辑页查看/恢复；历史超出保留上限时修剪最旧版本。
    """
    pipeline = _load_python_pipeline(db, pipeline_id)
    if (pipeline.status or "") == "published":
        raise HTTPException(
            409,
            "流水线已发布，脚本已封版不可修改。如需变更，请新建流水线。",
        )
    script = (body.script or "")
    if not script.strip():
        raise HTTPException(400, "脚本内容为空，无法保存。")

    execution = _run(script)
    if execution.error:
        raise HTTPException(
            400,
            f"保存前校验执行失败，脚本未保存：{execution.error}",
        )
    format_error = _format_error(pipeline, execution.rows)
    if format_error:
        raise HTTPException(
            400,
            f"保存前格式校验未通过，脚本未保存：{format_error}",
        )

    output_columns = _columns_of(execution.rows)
    definition = dict(pipeline.definition or {})
    definition["python"] = {
        "script": script,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "output_columns": output_columns,
    }
    pipeline.definition = definition
    # 脚本变更使既有发布校验凭证失效：发布前必须重新执行预览并校验字段定义
    pipeline.validation_attestation = None
    pipeline.updated_at = datetime.now(timezone.utc)

    next_version = (
        db.query(func.max(PipelineScriptVersion.version_no))
        .filter(PipelineScriptVersion.pipeline_id == pipeline.id)
        .scalar()
        or 0
    ) + 1
    db.add(PipelineScriptVersion(
        pipeline_id=pipeline.id,
        version_no=next_version,
        script=script,
        output_columns=output_columns,
        row_count=len(execution.rows),
        duration_ms=execution.duration_ms,
        created_by=getattr(current_user, "id", None),
    ))
    stale_ids = (
        db.query(PipelineScriptVersion.id)
        .filter(PipelineScriptVersion.pipeline_id == pipeline.id)
        .order_by(PipelineScriptVersion.version_no.desc())
        .offset(_SCRIPT_VERSION_KEEP)
        .all()
    )
    if stale_ids:
        db.query(PipelineScriptVersion).filter(
            PipelineScriptVersion.id.in_([row[0] for row in stale_ids])
        ).delete(synchronize_session=False)

    db.commit()
    db.refresh(pipeline)
    return {
        "pipeline": format_pipeline_fn(pipeline),
        "execution": _execution_payload(pipeline, execution),
    }


def list_script_versions(pipeline_id: str, db: Session) -> dict:
    """脚本的保存历史（最近在前）。"""
    pipeline = _load_python_pipeline(db, pipeline_id)
    rows = (
        db.query(PipelineScriptVersion)
        .filter(PipelineScriptVersion.pipeline_id == pipeline.id)
        .order_by(PipelineScriptVersion.version_no.desc())
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "version_no": row.version_no,
                "script": row.script,
                "output_columns": row.output_columns or [],
                "row_count": row.row_count or 0,
                "duration_ms": row.duration_ms or 0,
                "created_at": (
                    row.created_at.isoformat() if row.created_at else None
                ),
            }
            for row in rows
        ]
    }


def _load_python_pipeline(db: Session, pipeline_id: str) -> Pipeline:
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")
    if (pipeline.definition or {}).get("engine") != "python":
        raise HTTPException(400, "该流水线不是 Python 脚本流水线。")
    return pipeline


def _run(script: str, cancel_event=None) -> ScriptExecution:
    """基础设施类失败（未配置/不可达/超时/取消）映射为 502；脚本异常留在结果里。"""
    try:
        return execute_script(
            script,
            timeout=settings.python_script_timeout_seconds,
            cancel_event=cancel_event,
        )
    except PythonEngineError as exc:
        raise HTTPException(502, str(exc)) from exc


def _execution_payload(pipeline: Pipeline, execution: ScriptExecution) -> dict:
    failed = execution.error is not None
    format_error = None if failed else _format_error(pipeline, execution.rows)
    return {
        "ok": not failed,
        "format_valid": not failed and format_error is None,
        "format_error": format_error,
        "row_count": 0 if failed else len(execution.rows),
        "columns": [] if failed else _columns_of(execution.rows),
        "sample": [] if failed else execution.rows[:_SAMPLE_ROWS],
        "stdout": execution.stdout,
        "error": execution.error,
        "traceback": execution.traceback,
        "duration_ms": execution.duration_ms,
        # 平台执行时限，供页面展示「已执行 Xs / 上限 Ys」
        "timeout_seconds": settings.python_script_timeout_seconds,
    }


def _format_error(pipeline: Pipeline, rows: list[dict]) -> str | None:
    """复用资产湖准入闸门的行格式硬校验，保证保存认可的格式=入湖接受的格式。"""
    from app.data_channel.datasets.lake_gate import (
        LakeGateError,
        normalize_rows_for_lake,
    )

    try:
        normalize_rows_for_lake(rows, dataset_name=pipeline.name)
    except LakeGateError as exc:
        return str(exc)
    return None


def _columns_of(rows: list[dict]) -> list[str]:
    columns: list[str] = []
    for row in rows[:_SAMPLE_ROWS]:
        for key in row.keys():
            if key not in columns:
                columns.append(key)
    return columns
