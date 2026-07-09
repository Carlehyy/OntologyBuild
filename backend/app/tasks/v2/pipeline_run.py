"""Pipeline 执行 Celery 任务 — 支持 DAG 编译 + 节点状态追踪"""
from __future__ import annotations
from datetime import datetime, timezone
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def get_celery_app():
    try:
        from app.tasks.extraction import celery_app
        return celery_app
    except Exception:
        return None


celery_app = get_celery_app()


def _init_node_status(definition: dict | None) -> dict[str, str]:
    """从 definition 中提取所有节点 ID，初始化为 'idle'"""
    if not definition:
        return {}
    nodes = definition.get("nodes", [])
    return {n["id"]: "idle" for n in nodes}


def _compute_quality_score(rows: list[dict], route: str, meta: dict) -> float:
    if not rows:
        return 0.0
    if route == "C":
        meta_fields = {"markdown_text", "filename", "source_file", "source_dataset_id",
                       "extraction_strategy", "extraction_method", "structured_extraction_ok",
                       "structured_extraction_error"}
        meaningful_fields = [k for k in rows[0].keys() if k not in meta_fields]
        total_fields = len(meaningful_fields) or 1
        filled = sum(1 for row in rows for k in meaningful_fields if row.get(k))
        completeness = filled / (len(rows) * total_fields) if total_fields > 0 else 0
        rule_bonus = min(0.2, int(rows[0].get("rule_count", 0)) * 0.02)
        return min(1.0, completeness + rule_bonus)
    rows_before = meta.get("rows_before", len(rows)) or len(rows)
    rows_after = meta.get("rows_after", len(rows)) or len(rows)
    retention = rows_after / rows_before if rows_before > 0 else 1.0
    total_cells = sum(len(r) for r in rows) or 1
    filled_cells = sum(1 for r in rows for v in r.values() if v is not None and str(v).strip() != "")
    fill_rate = filled_cells / total_cells
    return round(retention * 0.4 + fill_rate * 0.6, 3)


def _route_for_kind(kind: str | None, default_route: str | None = None) -> str:
    if default_route in ("A", "B", "C"):
        return default_route
    if kind == "semi":
        return "B"
    if kind == "unstructured":
        return "C"
    return "A"


def _transform_nodes(definition: dict | None) -> list[dict]:
    if not definition:
        return []
    return [n for n in definition.get("nodes") or [] if n.get("type") == "transform"]


def _route_from_transform_config(config: dict | None) -> str | None:
    path = (config or {}).get("path")
    if path == "structured":
        return "A"
    if path == "semi_structured":
        return "B"
    if path == "unstructured":
        return "C"
    if path == "wide_table":
        return "A"
    return None


def _merge_dict(base: dict, overlay: dict) -> dict:
    result = dict(base or {})
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _spec_from_transform_config(config: dict | None) -> dict:
    config = config or {}
    spec: dict = {}
    steps = config.get("steps") or []

    if config.get("engine"):
        spec["engine"] = config.get("engine")
    if config.get("path"):
        spec["path"] = config.get("path")

    for step in steps:
        op = step.get("op") if isinstance(step, dict) else None
        params = step.get("params") if isinstance(step, dict) else {}
        params = params or {}

        if op in ("parse_json", "flatten_json", "explode_array"):
            spec["format"] = "json"
            flatten = dict(spec.get("json_flatten") or {})
            if op == "explode_array":
                flatten["array_explode"] = True
            flatten.update(params)
            spec["json_flatten"] = flatten
        elif op == "parse_xml":
            spec["format"] = "xml"
        elif op in ("detect_wide_table", "suggest_split", "apply_split"):
            wide = dict(spec.get("wide_table_split") or {})
            wide["enabled"] = True
            if op in ("detect_wide_table", "suggest_split"):
                wide["suggest_only"] = True
            if op == "apply_split":
                wide["suggest_only"] = False
            wide.update(params)
            spec["wide_table_split"] = wide
        elif op in ("drop_duplicates", "drop_nulls", "fill_nulls", "normalize_dates"):
            cleansing = dict(spec.get("cleansing") or {})
            if op == "drop_duplicates":
                cleansing["deduplicate"] = True
            elif op == "drop_nulls":
                cleansing["null_strategy"] = "drop"
            elif op == "fill_nulls":
                cleansing["null_strategy"] = params.get("strategy", "fill_empty")
            elif op == "normalize_dates":
                cleansing["normalize_dates"] = True
            cleansing.update({k: v for k, v in params.items() if k != "strategy"})
            spec["cleansing"] = cleansing
        elif op == "document_to_markdown":
            doc = dict(spec.get("document_to_md") or {})
            doc["strategy"] = params.get("strategy", doc.get("strategy", "markitdown"))
            doc.update(params)
            spec["document_to_md"] = doc
        elif op == "ocr_extract":
            doc = dict(spec.get("document_to_md") or {})
            doc["strategy"] = "ocr"
            doc.update(params)
            spec["document_to_md"] = doc
        elif op == "vlm_extract":
            doc = dict(spec.get("document_to_md") or {})
            doc["strategy"] = "vlm"
            doc.update(params)
            spec["document_to_md"] = doc
        elif op == "llm_structurize":
            extract = dict(spec.get("md_to_structured") or {})
            extract["auto_extract"] = True
            extract.update(params)
            spec["md_to_structured"] = extract
        elif op == "llm_enrich":
            # 方案三：规则提取后 LLM 语义增强，不影响结构确定性
            extract = dict(spec.get("md_to_structured") or {})
            extract["llm_enrich"] = True
            if params.get("fields"):
                extract["enrich_fields"] = params["fields"]
            spec["md_to_structured"] = extract

    if config.get("path") == "wide_table":
        wide = dict(spec.get("wide_table_split") or {})
        wide.setdefault("enabled", True)
        wide.setdefault("suggest_only", False)
        spec["wide_table_split"] = wide
    return spec


def _pipeline_runtime_config(pl) -> tuple[str | None, dict]:
    transforms = _transform_nodes(pl.definition)
    route = None
    spec = dict(pl.spec or {})
    for node in transforms:
        cfg = node.get("config") or {}
        route = route or _route_from_transform_config(cfg)
        spec = _merge_dict(spec, _spec_from_transform_config(cfg))
    return route, spec


def _source_runtime_route(source: dict, transform_route: str | None, default_route: str | None) -> str:
    return transform_route or source.get("route") or _route_for_kind(source.get("kind"), default_route)


def _find_dataset_for_file(db, filename: str):
    from app.models.v2.dataset import Dataset, DatasetVersion

    stem = Path(filename).stem
    candidates = db.query(Dataset).filter(
        Dataset.name == stem
    ).order_by(Dataset.created_at.desc()).limit(20).all()
    for candidate in candidates:
        ver = db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == candidate.id
        ).order_by(DatasetVersion.version_no.desc()).first()
        if ver and ((ver.rowcount or 0) > 0 or ver.storage_uri):
            return candidate
    return candidates[0] if candidates else None


def _collect_sources(db, pl) -> list[dict]:
    from app.models.v2.dataset import Dataset

    sources: list[dict] = []
    definition = pl.definition or {}
    for node in definition.get("nodes") or []:
        if node.get("type") != "connector":
            continue
        for file_info in (node.get("config") or {}).get("files", []) or []:
            filename = file_info.get("name") or file_info.get("filename") or ""
            dataset_id = file_info.get("dataset_id")
            ds = db.query(Dataset).filter(Dataset.id == dataset_id).first() if dataset_id else None
            if not ds and filename:
                ds = _find_dataset_for_file(db, filename)
            if ds:
                sources.append({
                    "dataset_id": ds.id,
                    "filename": filename or ds.name,
                    "route": _route_for_kind(ds.kind, None),
                    "kind": ds.kind,
                })

    if not sources and pl.source_dataset_id:
        ds = db.query(Dataset).filter(Dataset.id == pl.source_dataset_id).first()
        if ds:
            sources.append({
                "dataset_id": ds.id,
                "filename": ds.name,
                "route": _route_for_kind(ds.kind, pl.route),
                "kind": ds.kind,
            })

    # Preserve order while removing duplicate datasets.
    seen: set[str] = set()
    unique_sources = []
    for source in sources:
        if source["dataset_id"] in seen:
            continue
        seen.add(source["dataset_id"])
        unique_sources.append(source)
    return unique_sources


def _load_source_rows(db, svc, source: dict, limit: int = 10000) -> list[dict]:
    from app.models.v2.dataset import DatasetVersion

    if source["route"] == "C":
        ver = db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == source["dataset_id"]
        ).order_by(DatasetVersion.version_no.desc()).first()
        if not ver or not ver.storage_uri:
            return []
        raw = svc._storage.get_object(ver.storage_uri)
        return [{
            "filename": source["filename"],
            "content": raw,
            "storage_uri": ver.storage_uri,
            "source_dataset_id": source["dataset_id"],
        }]
    # None=最新版本：增量同步产生 v2+ 后，管道必须加工最新数据而非首个旧版本
    return svc.preview(source["dataset_id"], None, limit=limit)


def _execute_route(route: str, ctx, data: list[dict]) -> tuple[list[dict], object]:
    from app.services.v2.pipeline.engine import execute_route_a, execute_route_b, execute_route_c

    if route == "B":
        return execute_route_b(ctx, data)
    if route == "C":
        return execute_route_c(ctx, data)
    return execute_route_a(ctx, data)


def _execute_source(db, svc, pl, source: dict, transform_route: str | None, runtime_spec: dict):
    """单个源的完整执行：定路由 → 读最新版本行 → 跑 A/B/C steps。

    真实运行（pipeline_run_task）与试运行（collect_pipeline_output）共用，
    保证 dry-run 预览所见 = 入湖所得。返回 (data, ctx)。
    """
    from app.services.v2.pipeline.base import PipelineContext

    source["route"] = _source_runtime_route(source, transform_route, pl.route)
    data = _load_source_rows(db, svc, source)

    ctx = PipelineContext(
        dataset_id=source["dataset_id"],
        version_no=1,
        route=source["route"],
        spec=runtime_spec,
    )
    if source["route"] == "C":
        ctx.spec = dict(ctx.spec or {})
        # 默认使用规则提取保证可重复性。只有当 pipeline spec 中已明确写入
        # md_to_structured.auto_extract=true 时才调用 LLM（非确定性）。
        existing_md_spec = ctx.spec.get("md_to_structured") or {}
        if existing_md_spec.get("auto_extract"):
            # Pipeline spec 显式请求 LLM 提取：检查模型是否可用
            from app.services.model_config_selector import select_llm_model_config
            try:
                _has_llm = bool(select_llm_model_config(
                    purpose_tags=("结构化提取", "结构化抽取"), allow_vlm=False))
            except Exception:
                _has_llm = False
            if not _has_llm:
                existing_md_spec = {k: v for k, v in existing_md_spec.items() if k != "auto_extract"}
                existing_md_spec["rule_based"] = True
            ctx.spec["md_to_structured"] = existing_md_spec
        else:
            # 默认规则提取（确定性）
            ctx.spec["md_to_structured"] = {"rule_based": True, **existing_md_spec}
    ctx.rows_in = len(data)
    data, ctx = _execute_route(source["route"], ctx, data)
    ctx.rows_out = len(data)
    return data, ctx


def _strip_content(rows: list[dict]) -> list[dict]:
    """去掉 route C 的原始文件 bytes 列——它从不入 CSV，也无法进 JSON 暂存。"""
    return [{k: v for k, v in r.items() if k != "content"} for r in rows]


def collect_pipeline_output(db, pl) -> list[dict]:
    """试运行取数：执行采集与加工但【不写资产湖】。

    供列表页「执行」弹窗的 dry-run 预览使用；宽表拆分在此展开成多个输出，
    与 _save_curated_outputs 的落库粒度一一对应，commit 时按同样粒度回放。
    返回 [{source, table_name, rows, rows_in, rows_out, route, meta, multi_source}]。
    """
    from app.services.v2.dataset_service import DatasetService

    svc = DatasetService(db)
    sources = _collect_sources(db, pl)
    if not sources:
        raise ValueError("Pipeline 未绑定源数据集，请先在画布中配置连接器节点")
    transform_route, runtime_spec = _pipeline_runtime_config(pl)

    outputs: list[dict] = []
    multi_source = len(sources) > 1
    for source in sources:
        data, ctx = _execute_source(db, svc, pl, source, transform_route, runtime_spec)
        base_meta = {k: v for k, v in ctx.meta.items()
                     if k in ("rows_before", "rows_after")}  # 质量分只用这两个键
        split_tables = ctx.meta.get("split_tables")
        if isinstance(split_tables, dict) and split_tables:
            for table_name, rows in split_tables.items():
                rows = _strip_content(rows or [])
                outputs.append({
                    "source": dict(source), "table_name": str(table_name),
                    "rows": rows, "rows_in": ctx.rows_in, "rows_out": len(rows),
                    "route": source["route"], "meta": base_meta, "multi_source": True,
                })
        else:
            outputs.append({
                "source": dict(source), "table_name": None,
                "rows": _strip_content(data), "rows_in": ctx.rows_in, "rows_out": ctx.rows_out,
                "route": source["route"], "meta": base_meta, "multi_source": multi_source,
            })
    return outputs


def _curated_name(pl, source: dict, multi_source: bool, table_name: str | None = None) -> str:
    """产物 curated 数据集的命名规则——入湖与 dry-run 预检必须用同一套派生。"""
    stem = Path(source["filename"]).stem
    name_parts = [pl.name]
    if multi_source:
        name_parts.append(stem)
    if table_name:
        name_parts.append(table_name)
    name_parts.append("curated")
    return " ".join(name_parts)


def resolve_curated_target(db, pl, source: dict, multi_source: bool,
                           table_name: str | None = None):
    """定位本产物应写入的既有 curated 数据集：**按 id 绑定优先，按名字兜底**。

    产物名由 pl.name 派生，若只按名字 get-or-create，流水线改名后下一次运行
    会另起一个空白资产——旧资产（主键契约/版本史/映射绑定）静默停更。因此
    先在 target_curated_ids 里按入湖时固化的 schema 键位（pipeline_id /
    transform_output_table / source_dataset_id）反查；多候选歧义时回退名字，
    宁可分叉也不错绑别的产物。返回 (既有数据集|None, 派生名)。
    """
    from app.models.v2.dataset import Dataset as _DS

    ds_name = _curated_name(pl, source, multi_source, table_name)
    target_ids = [c for c in (pl.target_curated_ids or []) if c]
    if not target_ids:
        return None, ds_name
    candidates = db.query(_DS).filter(
        _DS.kind == "curated", _DS.id.in_(target_ids)).all()
    # 别的流水线的产物（历史数据 target_curated_ids 被误写时）不认
    candidates = [c for c in candidates
                  if (dict(c.schema_json or {}).get("pipeline_id") or pl.id) == pl.id]

    def _schema(c):
        return dict(c.schema_json or {})

    if table_name:
        matched = [c for c in candidates
                   if _schema(c).get("transform_output_table") == table_name]
    elif multi_source:
        matched = [c for c in candidates
                   if _schema(c).get("source_dataset_id") == source.get("dataset_id")]
    else:
        matched = candidates
    if len(matched) == 1:
        return matched[0], ds_name
    # 0 个（首跑/旧数据无键位）或多个（歧义）→ 名字兜底
    by_name = db.query(_DS).filter(
        _DS.kind == "curated", _DS.name == ds_name).first()
    return by_name, ds_name


def _save_curated_dataset(db, svc, pl, source: dict, data: list[dict], ctx, multi_source: bool, table_name: str | None = None, write_opts: dict | None = None, contract_columns: list[str] | None = None) -> dict:
    """入湖是「读湖中全量→内存合并→写新版本」的读改写，全程持数据集写锁。

    任务调度与手动运行并发落同一 curated 数据集时后到者等待；不锁则双方
    各自基于旧版本合并，先提交的增量被后提交者静默覆盖。锁键优先用已绑定
    数据集的 id（改名后名字会变、id 不变）；首建场景退回名字锁，
    get-or-create 也在锁内，同名数据集的创建竞争一并串行化。
    """
    from app.data_channel.datasets.lock import dataset_write_lock

    bound_ds, ds_name = resolve_curated_target(db, pl, source, multi_source, table_name)
    lock_key = f"curated::{bound_ds.id}" if bound_ds is not None else f"curated::{ds_name}"
    with dataset_write_lock(lock_key, bind=db.get_bind()):
        return _save_curated_dataset_in_lock(
            db, svc, pl, source, data, ctx, multi_source, table_name,
            write_opts, contract_columns, ds_name,
            bound_ds_id=(bound_ds.id if bound_ds is not None else None))


def _save_curated_dataset_in_lock(db, svc, pl, source: dict, data: list[dict], ctx, multi_source: bool, table_name: str | None, write_opts: dict | None, contract_columns: list[str] | None, ds_name: str, bound_ds_id: str | None = None) -> dict:
    # 复用既有 curated 数据集追加版本：同一管道反复运行不再无限增殖新数据集，
    # 下游 mapping 绑定的 curated id 保持稳定、能持续收到新版本。
    # 绑定关系按 id（resolve_curated_target），流水线改名不影响归属
    from app.models.v2.dataset import Dataset as _DS
    curated_ds = db.query(_DS).filter(_DS.id == bound_ds_id).first() if bound_ds_id else None
    if curated_ds is None:
        curated_ds = db.query(_DS).filter(
            _DS.kind == "curated", _DS.name == ds_name).first()
    if curated_ds is None:
        curated_ds = svc.create_dataset(name=ds_name, kind="curated")

    # ── 资产湖准入闸门：行格式规范化 + 主键契约（声明仲裁/三校验）+ 列漂移检测。
    # 主键违规抛 LakeGateError → 运行失败，错误身份的数据不入湖。
    from app.data_channel.datasets.lake_gate import (
        gate_rows, persist_contract, split_pk, validate_upsert_base, infer_columns_typed)
    # 流水线字段契约（改名/非空/主键）仅适用于单产物运行：多源/宽表拆分的
    # 契约粒度是「每个数据集一个」，流水线级契约对不上，跳过并在警告里说明
    contract_applicable = table_name is None and not multi_source
    column_defs = pl.column_definitions if contract_applicable else None
    gate = gate_rows(curated_ds, data, write_opts,
                     engine_contract_cols=contract_columns,
                     column_definitions=column_defs)
    if not contract_applicable and (pl.column_definitions or []):
        gate["warnings"].append(
            "该产物来自多源/多表拆分，流水线级字段契约未应用（契约仅适用于单产物流水线）")
    data = gate["rows"]
    effective_pk = gate["pk"]

    # 入库方式：任务触发时按 write_mode 与资产湖已有数据合并；
    # 手动画布运行不带 write_opts，保持原行为（本次输出即新版本 = 全量覆盖）
    merge_meta: dict = {}
    lake_rows = data
    lake_impact: dict | None = None
    if write_opts:
        if not data and write_opts.get("skip_empty", True):
            # 空输出保护：本次流水线输出 0 行，跳过入库，避免误清空资产
            return {
                "source_dataset_id": source["dataset_id"],
                "source_file": source["filename"],
                "route": source["route"],
                "table_name": table_name,
                "curated_dataset_id": curated_ds.id,
                "dataset_version_id": None,
                "rows_in": ctx.rows_in,
                "rows_out": 0,
                "lake_rows": None,
                "skipped": "empty_output",
                "meta": ctx.meta,
            }
        from app.data_channel.pipeline_tasks.merge import (
            load_latest_rows, merge_rows, compute_lake_impact)
        # 入库前的湖中全量——既作非 overwrite 的合并基，也作审计差异基线
        prev_rows = load_latest_rows(db, curated_ds.id)
        old_rows = [] if write_opts.get("mode") in (None, "overwrite") else prev_rows
        if write_opts.get("mode") == "upsert":
            # 湖中存量行缺主键列时按键去重会把它们折叠成一行——合并前硬校验
            validate_upsert_base(old_rows, split_pk(effective_pk),
                                 dataset_name=curated_ds.name)
        # 合并统一用仲裁后的生效主键（湖中已声明的契约优先于任务本次填写）
        lake_rows, merge_meta = merge_rows(
            old_rows, data, {**write_opts, "primary_key": effective_pk})
        # 审计：本次入库对资产湖的行级影响（入库前后 diff：新增/更新/删除）
        lake_impact = compute_lake_impact(prev_rows, lake_rows, split_pk(effective_pk))

    from app.data_channel.datasets.service import rows_to_parquet_bytes
    ver = svc.create_version(curated_ds.id, rows_to_parquet_bytes(lake_rows), rowcount=len(lake_rows))

    if data or lake_rows:
        try:
            # 赋新 dict, 原地修改 JSON 列不会被 SQLAlchemy 跟踪
            # 契约字段（primary_key/columns/columns_typed/last_output_columns）由闸门统一维护
            schema = persist_contract(
                curated_ds, pk=effective_pk,
                pk_source=gate["pk_source"],
                lake_rows=lake_rows, output_rows=data,
                column_definitions=column_defs,
                # 全量覆盖重建 = 变更主键声明的唯一受控通道（与 gate_rows 同一口径）
                allow_redeclare=(write_opts or {}).get("mode") in (None, "", "overwrite"))
            sample = data or lake_rows
            schema["quality_score"] = _compute_quality_score(sample, source["route"], ctx.meta)
            schema["route"] = source["route"]
            schema["source_dataset_id"] = source["dataset_id"]
            schema["pipeline_id"] = pl.id
            if merge_meta:
                schema["write_mode"] = merge_meta.get("mode")
            if table_name:
                schema["transform_output_table"] = table_name
            curated_ds.schema_json = schema
            db.commit()
        except Exception:
            logger.warning("curated schema_json 更新失败（不影响数据版本）", exc_info=True)

    # 审计：本次流水线输出样本（入库前的产物），供执行记录追溯「流水线的输出是什么」
    from app.data_channel.pipeline_tasks.merge import _slim_row
    output_columns = [c["name"] for c in infer_columns_typed(data)] if data else []
    output_sample = [_slim_row(r) for r in data[:50]]

    return {
        "source_dataset_id": source["dataset_id"],
        "source_file": source["filename"],
        "route": source["route"],
        "table_name": table_name,
        "curated_dataset_id": curated_ds.id,
        "curated_dataset_name": curated_ds.name,
        "dataset_version_id": ver.id,
        "version_no": ver.version_no,
        "rows_in": ctx.rows_in,
        "rows_out": len(data),
        "lake_rows": len(lake_rows),
        "output_columns": output_columns,
        "output_sample": output_sample,
        "lake_impact": lake_impact,
        "merge": merge_meta or None,
        "primary_key": effective_pk or None,
        "pk_source": gate["pk_source"] or None,
        "schema_drift": gate["drift"],
        "gate_warnings": gate["warnings"] or None,
        "meta": ctx.meta,
    }


def _save_curated_outputs(db, svc, pl, source: dict, data: list[dict], ctx, multi_source: bool, write_opts: dict | None = None, contract_columns: list[str] | None = None) -> list[dict]:
    split_tables = ctx.meta.get("split_tables")
    if isinstance(split_tables, dict) and split_tables:
        outputs = []
        for table_name, rows in split_tables.items():
            outputs.append(_save_curated_dataset(
                db, svc, pl, source, rows or [], ctx, multi_source=True, table_name=str(table_name), write_opts=write_opts, contract_columns=contract_columns
            ))
        return outputs
    return [_save_curated_dataset(db, svc, pl, source, data, ctx, multi_source, write_opts=write_opts, contract_columns=contract_columns)]


def pipeline_run_task(pipeline_id: str, run_id: str, write_opts: dict | None = None):
    """Pipeline 执行任务 — 支持 DAG 编译 + 节点状态追踪"""
    from app.database import SessionLocal
    from app.models.v2.pipeline import Pipeline, PipelineRun
    from app.services.v2.pipeline.dag_compiler import compile_definition
    from app.services.v2.dataset_service import DatasetService

    db = SessionLocal()
    run = None  # except 块引用；首个查询即抛异常时不能 NameError 掩盖原始错误
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            return
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.commit()

        pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pl:
            run.status = "failed"
            run.error_log = "Pipeline not found"
            db.commit()
            return

        # ── 非画布引擎（n8n / 未来第三方工作流）：注册表分发，共用入湖通道 ──
        from app.data_channel.pipelines.engine_registry import (
            CANVAS_ENGINES, get_engine_runner, known_engines)
        engine = (pl.definition or {}).get("engine")
        if engine not in CANVAS_ENGINES:
            runner = get_engine_runner(engine)
            if runner is None:
                run.status = "failed"
                run.error_log = (f"未知采集引擎「{engine}」，已注册引擎：{known_engines()}。"
                                 f"接入新引擎请在 engine_registry 登记 runner。")
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
                return
            runner(db, pl, run, write_opts)
            return

        # ── DAG 编译 ──────────────────────────────────────────────
        definition = pl.definition
        plan = compile_definition(definition)
        node_status = _init_node_status(definition)

        def set_node_status(nid: str, status: str):
            if nid in node_status:
                node_status[nid] = status
                # 每步更新持久化到 run; 必须赋新 dict, 原地修改 JSON 列不会被 SQLAlchemy 跟踪
                run.stats = {**(run.stats or {}), "node_status": dict(node_status)}
                db.commit()

        svc = DatasetService(db)
        sources = _collect_sources(db, pl)
        if not sources:
            raise ValueError("Pipeline has no source datasets")

        transform_route, runtime_spec = _pipeline_runtime_config(pl)

        if sources and not pl.source_dataset_id:
            pl.source_dataset_id = sources[0]["dataset_id"]
            db.commit()

        outputs = []
        source_stats = []
        multi_source = len(sources) > 1
        for source in sources:
            data, ctx = _execute_source(db, svc, pl, source, transform_route, runtime_spec)

            # 找到对应的 connector 节点
            conn_node_id = None
            for n in (definition or {}).get("nodes", []):
                if n.get("type") == "connector":
                    n_files = (n.get("config") or {}).get("files", []) or []
                    for fi in n_files:
                        if fi.get("dataset_id") == source["dataset_id"]:
                            conn_node_id = n.get("id")
                            break
                if conn_node_id:
                    break

            outputs.extend(_save_curated_outputs(db, svc, pl, source, data, ctx, multi_source, write_opts))

            # 记录逐源统计
            source_stat = {
                "source_name": source.get("filename", source["dataset_id"][:8]),
                "dataset_id": source["dataset_id"],
                "route": source["route"],
                "rows_in": ctx.rows_in,
                "rows_out": ctx.rows_out,
                "connector_node_id": conn_node_id,
            }
            source_stats.append(source_stat)
            if conn_node_id:
                set_node_status(conn_node_id, f"in:{ctx.rows_in} out:{ctx.rows_out}")

        total_in = sum(s["rows_in"] for s in source_stats)
        total_out = sum(s["rows_out"] for s in source_stats)

        # 更新 transform 和 output 节点状态
        for n in (definition or {}).get("nodes", []):
            if n.get("type") == "transform":
                set_node_status(n.get("id"), f"in:{total_in} out:{total_out}")
            elif n.get("type") == "output":
                set_node_status(n.get("id"), f"out:{total_out}")

        for nid in node_status:
            if ":" not in str(node_status.get(nid, "")):
                set_node_status(nid, "success")

        curated_ids = [o["curated_dataset_id"] for o in outputs]
        pl.target_curated_ids = curated_ids
        if len({s["route"] for s in sources}) == 1:
            pl.route = sources[0]["route"]
        else:
            pl.route = pl.route or sources[0]["route"] or "A"

        # 生命周期与运行态分离：status 只承载 draft/published（发布须走 publish
        # 端点），本次运行的成败由 PipelineRun.status 承载，不回写流水线
        db.commit()

        run.status = "success"
        run.finished_at = datetime.now(timezone.utc)
        # run ↔ 产物版本血缘：主产物的 DatasetVersion id
        run.dataset_version_id = next((o.get("dataset_version_id") for o in outputs if o.get("dataset_version_id")), None)
        lake_rows_total = sum(o.get("lake_rows") or 0 for o in outputs)
        gate_warnings = [w for o in outputs for w in (o.get("gate_warnings") or [])]
        # 审计：跨产物聚合的资产湖影响计数（列表页快速展示，明细在 meta.outputs）
        _impacts = [o.get("lake_impact") for o in outputs if o.get("lake_impact")]
        lake_impact_summary = {
            "added": sum(i.get("added_count", 0) for i in _impacts),
            "updated": sum(i.get("updated_count", 0) for i in _impacts),
            "deleted": sum(i.get("deleted_count", 0) for i in _impacts),
        } if _impacts else None
        run.stats = {
            **(run.stats or {}),
            "rows_in": total_in,
            "rows_out": total_out,
            "lake_rows": lake_rows_total,
            "lake_impact": lake_impact_summary,
            "write_mode": (write_opts or {}).get("mode"),
            "gate_warnings": gate_warnings or None,
            "skipped_outputs": [o for o in outputs if o.get("skipped")] or None,
            "node_status": dict(node_status),
            "source_stats": source_stats,
            "meta": {"outputs": outputs},
            "curated_dataset_id": curated_ids[0] if curated_ids else None,
            "curated_dataset_ids": curated_ids,
        }
        db.commit()

    except Exception as e:
        logger.error(f"Pipeline run failed: {e}")
        if run:
            run.status = "failed"
            run.error_log = str(e)
            run.finished_at = datetime.now(timezone.utc)
            stats = dict(run.stats or {})
            stats.setdefault("node_status", {})
            run.stats = stats
            # 运行失败不夺走发布态：failed 属于这次 run，不属于流水线生命周期
            db.commit()
    finally:
        db.close()


def _get_node_type(definition: dict | None, node_id: str) -> str:
    """从 definition 中获取节点类型"""
    if not definition:
        return ""
    for n in definition.get("nodes", []):
        if n.get("id") == node_id:
            return n.get("type", "")
    return ""


if celery_app:
    pipeline_run_task = celery_app.task(pipeline_run_task)
