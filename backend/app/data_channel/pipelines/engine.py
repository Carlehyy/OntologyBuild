"""Pipeline 执行引擎"""
from __future__ import annotations
from app.services.v2.pipeline.base import PipelineContext
from app.services.v2.pipeline.steps.schema_inference import SchemaInferenceStep
from app.services.v2.pipeline.steps.cleansing import CleansingStep
from app.services.v2.pipeline.steps.sync_postprocess import DeduplicateLatestStep, SoftDeleteStep


def _apply_sync_postprocess(ctx: PipelineContext, data: list[dict]) -> list[dict]:
    """在常规清洗后按需追加：去重取最新 + 软删除标记。
    通过 spec.deduplicate_latest / spec.soft_delete 显式启用。"""
    if ctx.spec.get("deduplicate_latest"):
        data = DeduplicateLatestStep().run(ctx, data)
    if ctx.spec.get("soft_delete"):
        data = SoftDeleteStep().run(ctx, data)
    return data


def execute_route_a(ctx: PipelineContext, data: list[dict]) -> tuple[list[dict], PipelineContext]:
    """Route A: 结构化数据处理 (schema 推断 + 清洗 + 同步后处理)"""
    steps = [SchemaInferenceStep(), CleansingStep()]
    for step in steps:
        data = step.run(ctx, data)
    data = _apply_sync_postprocess(ctx, data)
    wide_spec = ctx.spec.get("wide_table_split", {})
    if wide_spec.get("enabled") or wide_spec.get("split_config"):
        from app.services.v2.pipeline.steps.wide_table_split import WideTableSplitStep

        data = WideTableSplitStep().run(ctx, data)
    ctx.rows_out = len(data)
    return data, ctx


def execute_route_b(ctx: PipelineContext, data: list[dict]) -> tuple[list[dict], PipelineContext]:
    """Route B: 半结构化数据处理 (JSON flatten 或 XML 解析 + 清洗 + 同步后处理)"""
    from app.services.v2.pipeline.steps.json_flatten import JsonFlattenStep
    from app.services.v2.pipeline.steps.xml_parse import XmlParseStep

    data_format = ctx.spec.get("format", "json")  # json | xml

    cleansing = dict(ctx.spec.get("cleansing") or {})
    cleansing.setdefault("filter_jagged", False)
    ctx.spec["cleansing"] = cleansing

    if data_format == "xml":
        steps = [XmlParseStep(), CleansingStep()]
    else:
        steps = [JsonFlattenStep(), CleansingStep()]

    for step in steps:
        data = step.run(ctx, data)
    data = _apply_sync_postprocess(ctx, data)
    ctx.rows_out = len(data)
    return data, ctx


def execute_route_c(ctx: PipelineContext, data: list[dict]) -> tuple[list[dict], PipelineContext]:
    """Route C: 非结构化数据 (文档 → Markdown → LLM 结构化提取)"""
    from app.services.v2.pipeline.steps.document_to_md import DocumentToMarkdownStep
    from app.services.v2.pipeline.steps.md_to_structured import MarkdownToStructuredStep

    steps = [DocumentToMarkdownStep(), MarkdownToStructuredStep()]
    for step in steps:
        data = step.run(ctx, data)
    ctx.rows_out = len(data)
    return data, ctx


def execute_pipeline(pipeline_id: str, triggered_by: str = "") -> dict:
    """被 SyncEngine 链式触发。

    统一走 pipeline_run_task（与手动/Celery 运行完全同一条路径）：
    旧实现只计算 result_rows 却不落任何存储（无 curated、无版本），
    "同步→管道→治理入湖"链路在此断裂——现在同步触发的运行同样产出
    curated 数据集版本，供下游审核与映射灌入本体。
    """
    from datetime import datetime, timezone
    from app.database import SessionLocal
    from app.models.v2.pipeline import Pipeline, PipelineRun

    db = SessionLocal()
    try:
        pipe = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pipe:
            return {"status": "error", "error": f"Pipeline {pipeline_id} 不存在"}
        if pipe.enabled is False:  # 停用的流水线不参与链式触发（NULL 老数据视为启用）
            return {"status": "error", "error": f"Pipeline「{pipe.name}」已停用，跳过链式触发"}
        if not pipe.source_dataset_id and not pipe.definition:
            return {"status": "error", "error": "Pipeline 未绑定源数据集"}

        run = PipelineRun(
            pipeline_id=pipeline_id,
            status="pending",
            started_at=datetime.now(timezone.utc),
            stats={"triggered_by": triggered_by} if triggered_by else {},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    from app.tasks.v2.pipeline_run import pipeline_run_task
    # 同步执行（本函数本身运行在 sync 任务的后台上下文中）
    fn = getattr(pipeline_run_task, "run", pipeline_run_task)  # Celery task 或裸函数均可
    fn(pipeline_id, run_id)

    db = SessionLocal()
    try:
        done = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        stats = (done.stats or {}) if done else {}
        return {
            "status": "ok" if (done and done.status == "success") else "error",
            "pipeline_id": pipeline_id, "run_id": run_id,
            "rows_in": stats.get("rows_in", 0), "rows_out": stats.get("rows_out", 0),
            "curated_dataset_ids": stats.get("curated_dataset_ids", []),
            "error": done.error_log if done else None,
        }
    finally:
        db.close()
