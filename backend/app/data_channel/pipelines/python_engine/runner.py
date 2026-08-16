"""engine=python 的运行入口 — pipeline_run_task 经 engine_registry 分发到此。

run 状态机 / 资产湖准入闸门 / 版本化入湖 / 统计记账全部由通用的
run_external_pipeline 承担；这里只保留 python 特有的取数逻辑（collector）：
执行已保存的脚本并归一化 result 行。运行成败只写 run，不动 pipeline.status。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.data_channel.pipelines.python_engine.client import (
    PythonEngineError,
    execute_script,
)

logger = logging.getLogger(__name__)


def run_python_pipeline(db: Session, pl, run, write_opts: dict | None = None) -> None:
    from app.data_channel.pipelines.external_runner import run_external_pipeline

    # 任务池声明增量游标时，以 OB_RUN_PARAMS 变量注入脚本上下文
    # （cursor_since 为空串 = 全量；脚本自行按键过滤源端只取新数据）
    run_params = (getattr(run, "stats", None) or {}).get("run_params")

    def collector(db_: Session, pl_) -> tuple[list[dict], dict]:
        script = (((pl_.definition or {}).get("python") or {}).get("script") or "")
        if not script.strip():
            raise PythonEngineError(
                "该 Python 脚本流水线尚未保存脚本，无法运行。")
        execution = execute_script(
            script,
            timeout=settings.python_script_timeout_seconds,
            params={
                "cursor_column": str((run_params or {}).get("cursor_column") or ""),
                "cursor_since": str((run_params or {}).get("cursor_since") or ""),
                "full_refresh": bool((run_params or {}).get("full_refresh")),
            },
        )
        if execution.error:
            raise PythonEngineError(execution.error)
        return execution.rows, {
            "kernel_id": execution.kernel_id,
            "duration_ms": execution.duration_ms,
            "stdout_tail": execution.stdout[-500:],
        }

    contract_cols = (
        ((pl.definition or {}).get("python") or {}).get("output_columns")
        or None
    )
    run_external_pipeline(
        db, pl, run, write_opts,
        engine_name="python",
        collector=collector,
        contract_columns=contract_cols,
    )
