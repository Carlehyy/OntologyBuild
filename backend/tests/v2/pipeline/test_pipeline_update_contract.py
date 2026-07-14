import pytest
from fastapi import HTTPException

from app.data_channel.pipelines.models import Pipeline
from app.data_channel.pipelines.router import PipelineUpdate, update_pipeline


def test_published_pipeline_allows_name_and_description_updates(db):
    pipeline = Pipeline(
        name="发布前名称",
        description="发布前描述",
        status="published",
        definition={"engine": "canvas", "nodes": [], "edges": []},
        column_definitions=[],
    )
    db.add(pipeline)
    db.commit()

    result = update_pipeline(
        pipeline.id,
        PipelineUpdate(name="更新后的名称", description="更新后的描述"),
        db,
    )

    assert result["name"] == "更新后的名称"
    assert result["description"] == "更新后的描述"
    assert pipeline.status == "published"


def test_published_pipeline_still_rejects_contract_changes(db):
    pipeline = Pipeline(
        name="已发布流水线",
        status="published",
        definition={"engine": "canvas", "nodes": [], "edges": []},
        column_definitions=[],
    )
    db.add(pipeline)
    db.commit()

    with pytest.raises(HTTPException, match="字段契约") as exc:
        update_pipeline(
            pipeline.id,
            PipelineUpdate(column_definitions=[{
                "source_key": "id",
                "field_key": "id",
                "field_name": "ID",
                "field_type": "string",
                "is_primary_key": True,
                "nullable": True,
            }]),
            db,
        )

    assert exc.value.status_code == 409


def test_draft_pipeline_persists_primary_key_as_non_nullable(db):
    pipeline = Pipeline(name="草稿流水线", status="draft")
    db.add(pipeline)
    db.commit()

    result = update_pipeline(
        pipeline.id,
        PipelineUpdate(column_definitions=[{
            "source_key": "tenant_id",
            "field_key": "tenant_id",
            "field_name": "租户 ID",
            "field_type": "string",
            "is_primary_key": True,
            "nullable": True,
        }]),
        db,
    )

    assert result["column_definitions"][0]["nullable"] is False
