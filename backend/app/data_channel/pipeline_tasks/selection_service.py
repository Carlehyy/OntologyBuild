"""新建/编辑任务表单的流水线候选选择与成品列推断。

从 query_service 下沉：候选组装（selectable_pipelines）与列推断回退
（_curated_columns）是任务表单向导的专用读取路径，与任务池列表/统计的
查询职责分开。query_service 保留再导出，既有导入与 patch 点不变。
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.v2.pipeline import Pipeline


QueryDependency = Callable[..., Any]


def _curated_columns(
    db: Session,
    dataset,
    schema: dict,
) -> list[dict]:
    """Return curated dataset columns for contract selection and preview."""
    typed = schema.get("columns_typed")
    if isinstance(typed, list) and typed:
        return [
            {
                "name": column.get("name"),
                "type": column.get("type") or "string",
            }
            for column in typed
            if isinstance(column, dict) and column.get("name")
        ]
    columns = schema.get("columns")
    if isinstance(columns, list) and columns:
        return [
            {"name": column, "type": "string"}
            for column in columns
            if column
        ]
    # 回退：无 schema 的老数据集按存储形态轻量推断列（湖表首页行键 /
    # blob 表头），不物化整份数据
    from app.data_channel.datasets.service import infer_stored_columns

    return [
        {"name": name, "type": "string"}
        for name in infer_stored_columns(db, dataset)
    ]


def selectable_pipelines(
    db: Session,
    *,
    curated_columns_fn: QueryDependency,
    version_has_content_fn: QueryDependency,
) -> dict:
    """Return published and enabled pipelines usable by new tasks."""
    from app.data_channel.curated.approved_version_reader import (
        review_matches_version,
    )
    from app.data_channel.datasets.lake_gate import (
        contract_pk,
        normalize_definitions,
    )
    from app.models.v2.curated import CuratedReview
    from app.models.v2.dataset import Dataset, DatasetVersion

    # 与 _with_pipeline_info 同一口径：只取展示需要的列——Pipeline 行含
    # definition/spec/validation_attestation 大 JSON，全列拉取会随流水线
    # 数量与定义体积劣化。
    pipelines = (
        db.query(
            Pipeline.id, Pipeline.name, Pipeline.version, Pipeline.domain,
            Pipeline.status, Pipeline.updated_at, Pipeline.target_curated_ids,
            Pipeline.column_definitions,
        )
        .filter(
            Pipeline.status == "published",
            Pipeline.enabled.isnot(False),
        )
        .all()
    )

    # 批量预取候选引用的数据集与各自最新版本，替代逐流水线逐产物的 N+1 查询。
    # 最新版本 = 同 dataset_id 下 version_no 最大行（唯一约束保证无并列，
    # 与 order_by(version_no.desc()).first() 语义一致）；DatasetVersion.data_blob
    # 是 deferred 列，批量查询不会物化字节内容。
    all_dataset_ids: list[str] = []
    seen_dataset_ids: set[str] = set()
    for pipeline in pipelines:
        for dataset_id in (pipeline.target_curated_ids or []):
            if dataset_id and dataset_id not in seen_dataset_ids:
                seen_dataset_ids.add(dataset_id)
                all_dataset_ids.append(dataset_id)
    datasets_by_id: dict[str, Any] = {}
    latest_version_by_dataset: dict[str, Any] = {}
    reviews_by_dataset: dict[str, list] = {}
    if all_dataset_ids:
        datasets_by_id = {
            dataset.id: dataset
            for dataset in db.query(Dataset)
            .filter(Dataset.id.in_(all_dataset_ids))
            .all()
        }
        latest_version_sub = (
            db.query(
                DatasetVersion.dataset_id,
                func.max(DatasetVersion.version_no).label("mx"),
            )
            .filter(DatasetVersion.dataset_id.in_(all_dataset_ids))
            .group_by(DatasetVersion.dataset_id)
            .subquery()
        )
        latest_version_by_dataset = {
            ver.dataset_id: ver
            for ver in db.query(DatasetVersion)
            .join(
                latest_version_sub,
                (DatasetVersion.dataset_id == latest_version_sub.c.dataset_id)
                & (DatasetVersion.version_no == latest_version_sub.c.mx),
            )
            .all()
        }
        # 当前版本审核状态批量预取（与 catalog_service 同一匹配口径），
        # 前端据此举证「查看实际数据」是否可用，不再靠 409 试错。
        for review in (
            db.query(CuratedReview)
            .filter(CuratedReview.curated_dataset_id.in_(all_dataset_ids))
            .order_by(CuratedReview.created_at.desc())
            .all()
        ):
            reviews_by_dataset.setdefault(
                review.curated_dataset_id, []
            ).append(review)

    items: list[dict] = []
    for pipeline in pipelines:
        curated: list[dict] = []
        total_rows = 0
        for dataset_id in [
            item
            for item in (pipeline.target_curated_ids or [])
            if item
        ]:
            dataset = datasets_by_id.get(dataset_id)
            if not dataset:
                continue
            latest = latest_version_by_dataset.get(dataset_id)
            has_data = bool(
                latest
                and (
                    version_has_content_fn(latest)
                    or (latest.rowcount or 0) > 0
                )
            )
            if not has_data:
                continue
            schema = dict(dataset.schema_json or {})
            rowcount = latest.rowcount or 0
            review = next(
                (
                    candidate
                    for candidate in reviews_by_dataset.get(dataset_id, [])
                    if review_matches_version(candidate, latest)
                ),
                None,
            )
            curated.append(
                {
                    "id": dataset.id,
                    "name": dataset.name,
                    "rowcount": rowcount,
                    "version_no": latest.version_no,
                    "primary_key": schema.get("primary_key") or "",
                    "review_status": (
                        review.status if review else "pending_review"
                    ),
                    "columns": curated_columns_fn(
                        db,
                        dataset,
                        schema,
                    ),
                }
            )
            total_rows += rowcount

        definitions = normalize_definitions(
            pipeline.column_definitions
        )
        contract = (
            {
                "primary_key": contract_pk(
                    pipeline.column_definitions
                ),
                "columns": [
                    {
                        "name": definition["field_key"],
                        "type": definition["field_type"],
                        "field_name": definition["field_name"],
                        "is_primary_key": (
                            definition["is_primary_key"]
                        ),
                        "nullable": definition["nullable"],
                    }
                    for definition in definitions
                ],
            }
            if definitions
            else None
        )

        # 既无契约也无已产出数据：无从配置入库方式，不进候选
        if not curated and not contract:
            continue
        items.append(
            {
                "id": pipeline.id,
                "name": pipeline.name,
                "version": pipeline.version,
                "domain": pipeline.domain,
                "status": pipeline.status,
                "total_rows": total_rows,
                "contract": contract,
                "curated_datasets": curated,
                "updated_at": (
                    pipeline.updated_at.isoformat()
                    if pipeline.updated_at
                    else None
                ),
            }
        )
    items.sort(
        key=lambda item: item["updated_at"] or "",
        reverse=True,
    )
    return {"items": items, "total": len(items)}
