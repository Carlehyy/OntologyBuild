"""任务池查询刻画测试：锁定 _with_pipeline_info / selectable_pipelines /
_last_impact_map 的精确输出。

这些测试先于查询批量化重构写入并通过，重构后必须逐字段保持绿色——
输出不变是「不改变业务流程」的客观证据。
"""
from datetime import datetime

from app.data_channel.datasets.service import (
    rows_to_parquet_bytes,
    version_has_content,
)
from app.data_channel.pipeline_tasks.models import PipelineTask
from app.data_channel.pipeline_tasks.query_service import (
    _curated_columns,
    _with_pipeline_info,
    selectable_pipelines,
)
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.pipeline import Pipeline, PipelineRun


def _pipeline(pid, *, name, status, enabled, columns=None, targets=None,
              version=3, updated_at=None, domain="供应链"):
    return Pipeline(
        id=pid,
        name=name,
        spec={},
        status=status,
        enabled=enabled,
        column_definitions=columns,
        target_curated_ids=targets or [],
        version=version,
        domain=domain,
        updated_at=updated_at or datetime(2026, 1, 1, 8, 0, 0),
    )


CONTRACT = [
    {
        "field_key": "order_id",
        "field_name": "订单编号",
        "field_type": "string",
        "is_primary_key": True,
        "nullable": False,
    },
    {
        "field_key": "amount",
        "field_name": "金额",
        "field_type": "float",
        "is_primary_key": False,
        "nullable": True,
    },
]


def _seed_selectable(db):
    db.add_all([
        _pipeline(
            "pipe-a", name="订单流水线", status="published", enabled=True,
            columns=CONTRACT,
            targets=["ds-full", "ds-empty", "ds-missing", "ds-fallback"],
            updated_at=datetime(2026, 1, 2, 9, 30, 0),
        ),
        # 无契约且无产出：不进候选
        _pipeline("pipe-b", name="空流水线", status="published", enabled=True),
        _pipeline("pipe-c", name="草稿流水线", status="draft", enabled=True,
                  columns=CONTRACT),
        _pipeline("pipe-d", name="停用流水线", status="published",
                  enabled=False, columns=CONTRACT),
        # 有契约、未产出：可选，curated 为空
        _pipeline("pipe-e", name="待跑流水线", status="published", enabled=True,
                  columns=CONTRACT[:1], version=1,
                  updated_at=datetime(2026, 1, 1, 6, 0, 0)),
    ])
    db.add_all([
        Dataset(id="ds-full", name="订单 curated", kind="curated", schema_json={
            "primary_key": "order_id",
            "columns_typed": [
                {"name": "order_id", "type": "string"},
                {"name": "amount", "type": "float"},
            ],
        }),
        Dataset(id="ds-empty", name="空壳 curated", kind="curated",
                schema_json={"columns": ["a", "b"]}),
        # 无 schema_json 列信息的老数据集：走 preview 回退推断
        Dataset(id="ds-fallback", name="老 curated", kind="curated",
                schema_json=None),
    ])
    fallback_bytes = rows_to_parquet_bytes([
        {"订单号": "A-1", "金额": "10"},
        {"订单号": "A-2", "金额": "20"},
    ])
    db.add_all([
        DatasetVersion(id="v-full-1", dataset_id="ds-full", version_no=1,
                       rowcount=5, data_size=10),
        DatasetVersion(id="v-full-2", dataset_id="ds-full", version_no=2,
                       rowcount=7, data_size=10),
        DatasetVersion(id="v-empty-1", dataset_id="ds-empty", version_no=1,
                       rowcount=0, data_size=None, storage_uri=None),
        DatasetVersion(id="v-fallback-1", dataset_id="ds-fallback",
                       version_no=1, rowcount=2,
                       data_blob=fallback_bytes,
                       data_size=len(fallback_bytes)),
    ])
    db.commit()


def test_selectable_pipelines_output_is_pinned(db):
    _seed_selectable(db)

    result = selectable_pipelines(
        db,
        curated_columns_fn=_curated_columns,
        version_has_content_fn=version_has_content,
    )

    assert result["total"] == 2
    # 按 updated_at 倒序：pipe-a 在前
    assert [item["id"] for item in result["items"]] == ["pipe-a", "pipe-e"]

    first = result["items"][0]
    assert first["name"] == "订单流水线"
    assert first["version"] == 3
    assert first["domain"] == "供应链"
    assert first["status"] == "published"
    assert first["updated_at"] == "2026-01-02T09:30:00"
    assert first["contract"] == {
        "primary_key": "order_id",
        "columns": [
            {"name": "order_id", "type": "string", "field_name": "订单编号",
             "is_primary_key": True, "nullable": False},
            {"name": "amount", "type": "float", "field_name": "金额",
             "is_primary_key": False, "nullable": True},
        ],
    }
    # ds-empty（无内容）与 ds-missing（不存在）被跳过，顺序保持 target 声明序
    assert first["curated_datasets"] == [
        {
            "id": "ds-full",
            "name": "订单 curated",
            "rowcount": 7,          # 最新版本 v2 的行数
            "version_no": 2,
            "primary_key": "order_id",
            "columns": [
                {"name": "order_id", "type": "string"},
                {"name": "amount", "type": "float"},
            ],
        },
        {
            "id": "ds-fallback",
            "name": "老 curated",
            "rowcount": 2,
            "version_no": 1,
            "primary_key": "",
            "columns": [
                {"name": "订单号", "type": "string"},
                {"name": "金额", "type": "string"},
            ],
        },
    ]
    assert first["total_rows"] == 9

    second = result["items"][1]
    assert second["curated_datasets"] == []
    assert second["total_rows"] == 0
    assert second["contract"]["primary_key"] == "order_id"
    assert len(second["contract"]["columns"]) == 1


def _task(tid, pipeline_id, **overrides):
    data = {
        "id": tid,
        "name": f"任务{tid}",
        "pipeline_id": pipeline_id,
        "write_mode": "upsert",
        "schedule_type": "MANUAL",
        "status": "idle",
        "enabled": True,
    }
    data.update(overrides)
    return PipelineTask(**data)


def test_with_pipeline_info_output_is_pinned(db):
    _seed_selectable(db)
    db.add_all([
        _task("task-1", "pipe-a"),
        _task("task-2", "ghost-pipeline"),
    ])
    db.add_all([
        PipelineRun(
            id="run-old", pipeline_id="pipe-a", task_id="task-1",
            status="success", created_at=datetime(2026, 1, 2, 8, 0, 0),
            stats={"lake_impact": {"added_count": 1}},
        ),
        PipelineRun(
            id="run-new", pipeline_id="pipe-a", task_id="task-1",
            status="success", created_at=datetime(2026, 1, 2, 9, 0, 0),
            stats={"lake_impact": {"added_count": 5, "updated_count": 2}},
        ),
    ])
    db.commit()

    items = _with_pipeline_info(db, [
        db.query(PipelineTask).get("task-1"),
        db.query(PipelineTask).get("task-2"),
    ])

    first, second = items
    assert first["pipeline_name"] == "订单流水线"
    assert first["pipeline_status"] == "published"
    assert first["pipeline_enabled"] is True
    assert first["pipeline_version"] == 3
    assert first["next_run_at"] is None        # MANUAL 无下次调度时间
    # 最近一次运行的湖影响（按 created_at 最新）
    assert first["last_impact"] == {"added_count": 5, "updated_count": 2}

    # 关联流水线已删除的兜底展示
    assert second["pipeline_name"] == "(已删除)"
    assert second["pipeline_status"] == "deleted"
    assert second["pipeline_enabled"] is False
    assert second["pipeline_version"] is None
    assert second["last_impact"] is None
