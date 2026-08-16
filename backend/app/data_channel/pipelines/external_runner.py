"""
外部采集引擎通用运行器 — n8n / python 等注册表引擎流水线共用执行骨架。

引擎差异被压缩到一个 collector 回调（「行数据从哪来」）；其余全部通用：
run 状态机、PipelineContext 记账、资产湖准入闸门 + 版本化入湖
（_save_curated_outputs）、run.stats 统计。运行成败只写 run，不动
pipeline.status——发布是显式动作（publish 端点），失败不夺走发布态。

新引擎接入示例（另见 engine_registry 模块 docstring）：

    def run_acme_pipeline(db, pl, run, write_opts=None):
        def collector(db, pl):
            rows = acme_client.fetch(...)          # 引擎自己的取数逻辑
            return rows, {"execution_id": "..."}  # (list[dict], 执行元信息)
        run_external_pipeline(db, pl, run, write_opts,
                              engine_name="acme", collector=collector)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)


def run_external_pipeline(db, pl, run, write_opts: dict | None = None, *,
                          engine_name: str,
                          collector: Callable,
                          contract_columns: list[str] | None = None,
                          output_finalizer: Callable | None = None) -> None:
    """执行一次外部引擎流水线，负责把 run 写到终态。

    collector(db, pipeline) -> (rows, exec_meta)，失败时抛异常。
    rows 可以是全量 list[dict]，也可以是批次迭代器 Iterable[list[dict]]
    （流式：行数由入湖段精确记账进 ctx.rows_in/rows_out）。
    contract_columns：引擎侧审批固化的期望列（供准入闸门做漂移检测）。
    """
    from app.services.v2.dataset_service import DatasetService
    from app.services.v2.pipeline.base import PipelineContext
    from app.tasks.v2.pipeline_run import _save_curated_outputs

    try:
        run.status = "running"
        run.started_at = run.started_at or datetime.now(timezone.utc)
        db.commit()

        rows, exec_meta = collector(db, pl)

        ctx = PipelineContext(dataset_id="", version_no=1, route="A", spec={})
        ctx.rows_in = len(rows) if isinstance(rows, list) else 0
        ctx.rows_out = ctx.rows_in
        ctx.meta[f"{engine_name}_execution"] = exec_meta
        source = {"dataset_id": None, "filename": pl.name, "route": "A", "kind": engine_name}
        svc = DatasetService(db)
        outputs = _save_curated_outputs(db, svc, pl, source, rows, ctx,
                                        multi_source=False, write_opts=write_opts,
                                        contract_columns=contract_columns,
                                        run_params=(run.stats or {}).get("run_params"))

        curated_ids = [o["curated_dataset_id"] for o in outputs]
        dataset_version_id = next(
            (o.get("dataset_version_id") for o in outputs if o.get("dataset_version_id")), None)
        gate_warnings = [w for o in outputs for w in (o.get("gate_warnings") or [])]
        # 增量游标：当次产出的水位词法最大值（空产出为 None，不推进水位）
        watermark_after = next(
            (o.get("watermark_after") for o in outputs
             if o.get("watermark_after")), None)
        stats = {
            **(run.stats or {}),
            "engine": engine_name,
            f"{engine_name}_execution": exec_meta,
            "rows_in": ctx.rows_in,
            "rows_out": ctx.rows_out,
            **({"watermark_after": watermark_after} if watermark_after else {}),
            "lake_rows": sum(o.get("lake_rows") or 0 for o in outputs),
            "write_mode": (write_opts or {}).get("mode"),
            "gate_warnings": gate_warnings or None,
            "skipped_outputs": [o for o in outputs if o.get("skipped")] or None,
            "curated_dataset_id": curated_ids[0] if curated_ids else None,
            "curated_dataset_ids": curated_ids,
            "meta": {"outputs": outputs},
        }
        # Engines with side assets (for example n8n FileRefs) finalize their
        # metadata in the same final DB transaction as the run success state.
        # Dataset versions are already immutable/published by the lake writer;
        # a finalizer failure therefore marks this run failed and leaves side
        # assets eligible for compensation instead of exposing broken refs.
        if output_finalizer is not None:
            output_finalizer(db, pl, run, outputs, exec_meta, dataset_version_id)
        pl.target_curated_ids = curated_ids
        run.status = "success"
        run.finished_at = datetime.now(timezone.utc)
        run.dataset_version_id = dataset_version_id
        run.stats = stats
    except Exception as e:  # noqa: BLE001 — 引擎失败必须落到 run 终态而非炸掉调度进程
        logger.error("%s 流水线运行失败: %s", engine_name, e, exc_info=True)
        run.status = "failed"
        run.error_log = str(e)
        run.finished_at = datetime.now(timezone.utc)
    finally:
        db.commit()
