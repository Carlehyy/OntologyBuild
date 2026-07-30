"""Pure A/B/C pipeline route execution.

This module deliberately knows nothing about Celery tasks or pipeline-run
persistence.  Keeping transformation execution below orchestration prevents
the worker task from depending back on its own trigger path.
"""
from __future__ import annotations

from app.services.v2.pipeline.base import PipelineContext
from app.services.v2.pipeline.steps.cleansing import CleansingStep
from app.services.v2.pipeline.steps.schema_inference import SchemaInferenceStep
from app.services.v2.pipeline.steps.sync_postprocess import (
    DeduplicateLatestStep,
    SoftDeleteStep,
)


def _apply_sync_postprocess(
    ctx: PipelineContext,
    data: list[dict],
) -> list[dict]:
    """Apply the explicitly enabled synchronization post-processing steps."""
    if ctx.spec.get("deduplicate_latest"):
        data = DeduplicateLatestStep().run(ctx, data)
    if ctx.spec.get("soft_delete"):
        data = SoftDeleteStep().run(ctx, data)
    return data


def execute_route_a(
    ctx: PipelineContext,
    data: list[dict],
) -> tuple[list[dict], PipelineContext]:
    """Route A: structured data (schema inference, cleansing, sync steps)."""
    steps = [SchemaInferenceStep(), CleansingStep()]
    for step in steps:
        data = step.run(ctx, data)
    data = _apply_sync_postprocess(ctx, data)
    wide_spec = ctx.spec.get("wide_table_split", {})
    if wide_spec.get("enabled") or wide_spec.get("split_config"):
        from app.services.v2.pipeline.steps.wide_table_split import (
            WideTableSplitStep,
        )

        data = WideTableSplitStep().run(ctx, data)
    ctx.rows_out = len(data)
    return data, ctx


def execute_route_b(
    ctx: PipelineContext,
    data: list[dict],
) -> tuple[list[dict], PipelineContext]:
    """Route B: semi-structured JSON/XML data followed by cleansing."""
    from app.services.v2.pipeline.steps.json_flatten import JsonFlattenStep
    from app.services.v2.pipeline.steps.xml_parse import XmlParseStep

    data_format = ctx.spec.get("format", "json")
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


def execute_route_c(
    ctx: PipelineContext,
    data: list[dict],
) -> tuple[list[dict], PipelineContext]:
    """Route C: document-to-Markdown and structured extraction."""
    from app.services.v2.pipeline.steps.document_to_md import (
        DocumentToMarkdownStep,
    )
    from app.services.v2.pipeline.steps.md_to_structured import (
        MarkdownToStructuredStep,
    )

    steps = [DocumentToMarkdownStep(), MarkdownToStructuredStep()]
    for step in steps:
        data = step.run(ctx, data)
    ctx.rows_out = len(data)
    return data, ctx
