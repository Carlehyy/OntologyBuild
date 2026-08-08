"""Python 脚本流水线的执行与保存 — HTTP 层业务逻辑。

「执行」= 内核试跑 + 平台行格式（list[dict]）复核，不写库；
「保存」= 双重保障的服务端一侧：重新执行并过格式门禁，通过才把脚本写入
``definition.python`` 并清空既有发布校验凭证（脚本变更必须重新预览+校验
才能发布，execution_hash 覆盖 definition 天然保证这一点）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.data_channel.pipelines.python_engine.client import (
    PythonEngineError,
    ScriptExecution,
    execute_script,
)
from app.models.v2.pipeline import Pipeline

# 执行结果回传的样本行数（完整数据以 dry-run 暂存通道为准）
_SAMPLE_ROWS = 50


def execute_pipeline_script(pipeline_id: str, body, db: Session) -> dict:
    """执行脚本并返回结果与格式校验结论（脚本级失败以 ok=false 承载）。"""
    pipeline = _load_python_pipeline(db, pipeline_id)
    script = (body.script or "")
    if not script.strip():
        raise HTTPException(400, "脚本内容为空，无法执行。")
    execution = _run(script)
    return _execution_payload(pipeline, execution)


def save_pipeline_script(
    pipeline_id: str,
    body,
    db: Session,
    *,
    format_pipeline_fn,
) -> dict:
    """保存脚本：服务端重跑复验，执行成功且输出格式合法才落库。"""
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

    definition = dict(pipeline.definition or {})
    definition["python"] = {
        "script": script,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "output_columns": _columns_of(execution.rows),
    }
    pipeline.definition = definition
    # 脚本变更使既有发布校验凭证失效：发布前必须重新执行预览并校验字段定义
    pipeline.validation_attestation = None
    pipeline.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(pipeline)
    return {
        "pipeline": format_pipeline_fn(pipeline),
        "execution": _execution_payload(pipeline, execution),
    }


def _load_python_pipeline(db: Session, pipeline_id: str) -> Pipeline:
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")
    if (pipeline.definition or {}).get("engine") != "python":
        raise HTTPException(400, "该流水线不是 Python 脚本流水线。")
    return pipeline


def _run(script: str) -> ScriptExecution:
    """基础设施类失败（未配置/不可达/超时）映射为 502；脚本异常留在结果里。"""
    try:
        return execute_script(
            script,
            timeout=settings.python_script_timeout_seconds,
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
