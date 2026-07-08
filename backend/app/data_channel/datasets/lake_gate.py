"""
资产湖准入闸门 (Lake Gate) — 所有采集引擎入湖前的唯一必经校验点。

平台规范的行数据格式（湖内契约）：
  - 行式结构：list[dict]，每行一个扁平 dict，键为列名（对齐 MySQL 一行一列的语义）
  - 值域：标量（str/int/float/bool/None）；dict/list 入湖时会被压成 JSON 字符串；
    bytes 一律拒绝（CSV 序列化会把它变成 "<N bytes>" 占位符，等于静默丢数据）
  - 契约元数据挂在 curated Dataset.schema_json 上：
      primary_key    逗号分隔主键列。一经声明即为该资产的权威身份规则，
                     upsert 合并、映射实例身份、行级审核都以它为准
      columns        列名列表（既有字段，前端/映射在用，保持不变）
      columns_typed  [{name, type}]，带推断类型的列清单
      contract       {pk_source, declared_at} 主键声明的来源与时间

职责边界：
  - 主键违规（列缺失/空值/重复、任务声明与湖中声明冲突）→ LakeGateError 硬失败。
    宁可让运行失败，也不允许错误身份的数据入湖——下游本体实例 ID 由主键值
    派生（uuid5），错误主键 = 一整批实例身份作废。
  - 列集合漂移 → 默认警告并随 run stats 记录，不阻断采集。源端加列是常态，
    减列/换列则需要人看见（漂移信息会出现在任务运行统计里）。

主键为什么不放在任务（PipelineTask）上而要固化到湖里：
  1. 一条流水线可被多个任务调度（pipeline_id 非唯一），主键只活在任务上
     会出现同一份资产被两套身份规则交替写入；
  2. 映射灌本体读不到任务配置，只能读数据集元数据；
  3. 一条流水线可产出多个 curated 数据集（宽表拆分），主键的正确粒度是
     「每个数据集一个」。
契约的录入口在流水线（column_definitions，发布即封版）：单产物流水线入湖时
经 apply_column_contract 改名/校验，主键按 lake > pipeline > task 仲裁后固化；
任务的 primary_key 仅作兼容录入（无契约的旧流水线），首次入湖同样固化到湖。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone


class LakeGateError(ValueError):
    """入湖校验失败。message 必须可操作：说清哪个数据集、哪列、怎么修。"""


def split_pk(pk: str | None) -> list[str]:
    """'a, b' → ['a', 'b']。空/None → []。"""
    return [c.strip() for c in str(pk or "").split(",") if c.strip()]


# ── 流水线字段契约（Pipeline.column_definitions）──────────────────
# 契约由流水线编辑向导定义、发布后封版：
#   [{source_key, field_key, field_name, field_type, is_primary_key, nullable}]
#   source_key = 流水线原始输出列名；field_key = 入湖列名（湖内权威列名）。
# 仅适用于单产物流水线；多产物（宽表拆分/多源）契约粒度错位，调用方跳过。

CONTRACT_FIELD_TYPES = ("string", "integer", "float", "boolean", "timestamp", "json")

# 旧数据/前端旧词表的类型别名，统一映射到平台词表（与 infer_columns_typed 一致）
_LEGACY_TYPE_ALIASES = {
    "int": "integer", "bool": "boolean", "datetime": "timestamp",
    "str": "string", "text": "string", "number": "float", "double": "float",
    "date": "timestamp",
}

FIELD_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_field_type(t) -> str:
    t = str(t or "string").strip().lower()
    t = _LEGACY_TYPE_ALIASES.get(t, t)
    return t if t in CONTRACT_FIELD_TYPES else "string"


def normalize_definitions(defs: list | None) -> list[dict]:
    """规整 column_definitions：补 source_key（旧数据只有 field_key）、
    映射旧类型词、丢弃空项。所有消费方一律经此函数读契约。"""
    out: list[dict] = []
    for d in defs or []:
        if not isinstance(d, dict):
            continue
        field_key = str(d.get("field_key") or "").strip()
        source_key = str(d.get("source_key") or "").strip() or field_key
        if not source_key:
            continue
        out.append({
            "source_key": source_key,
            "field_key": field_key or source_key,
            "field_name": str(d.get("field_name") or "").strip() or (field_key or source_key),
            "field_type": normalize_field_type(d.get("field_type")),
            "is_primary_key": bool(d.get("is_primary_key")),
            "nullable": bool(d.get("nullable", True)),
        })
    return out


def contract_pk(defs: list | None) -> str:
    """流水线契约声明的主键（入湖列名 field_key，逗号拼接）。无契约/无主键 → ""。"""
    return ",".join(d["field_key"] for d in normalize_definitions(defs) if d["is_primary_key"])


def _value_type(v) -> str | None:
    """单值的平台类型（与 infer_columns_typed 同一词表）。空值 → None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "float"
    if isinstance(v, (dict, list)):
        return "json"
    s = str(v).strip()
    if not s:
        return None
    from app.services.v2.pipeline.steps.schema_inference import SchemaInferenceStep
    try:
        t = SchemaInferenceStep._infer_type(s)
    except Exception:  # noqa: BLE001 — 推断失败按 string 处理，不阻断
        return "string"
    return None if t == "null" else (t if t in CONTRACT_FIELD_TYPES else "string")


_TYPE_CHECK_SAMPLE = 200  # 类型检查采样行数——只产警告，无需全量扫描


def apply_column_contract(rows: list[dict], definitions: list | None, *,
                          dataset_name: str = "") -> tuple[list[dict], list[str]]:
    """按流水线字段契约处理行数据：source_key→field_key 改名 + 约束校验。

    - 改名：契约声明的原始列统一重命名为入湖列名（湖内只认 field_key）
    - 非空：nullable=False 的列出现空值 → LakeGateError 硬失败（契约即承诺）
    - 类型：与契约类型不符 → 警告不阻断（值类型推断本身是模糊的）
    - 契约外的输出列原样保留：源端加列是常态，交给漂移检测提醒
    返回 (处理后的行, 警告列表)。
    """
    defs = normalize_definitions(definitions)
    if not defs or not rows:
        return rows, []
    rename = {d["source_key"]: d["field_key"] for d in defs
              if d["source_key"] != d["field_key"]}
    if rename:
        rows = [{rename.get(str(k), str(k)): v for k, v in row.items()} for row in rows]

    for d in defs:
        if d["nullable"]:
            continue
        col = d["field_key"]
        for i, row in enumerate(rows):
            v = row.get(col)
            if v is None or (isinstance(v, str) and not v.strip()):
                raise LakeGateError(
                    f"数据集「{dataset_name}」第 {i + 1} 行的列「{col}」为空，但流水线契约声明该列不允许为空。"
                    f"请在流水线中过滤/补全该列，或回到流水线（草稿态）放宽该列的空值约束。")

    warnings: list[str] = []
    for d in defs:
        expected = d["field_type"]
        if expected in ("string", "json"):
            continue
        col = d["field_key"]
        observed: set[str] = set()
        for row in rows[:_TYPE_CHECK_SAMPLE]:
            actual = _value_type(row.get(col))
            if actual is None or actual == expected:
                continue
            if expected == "float" and actual == "integer":
                continue
            observed.add(actual)
        if observed:
            warnings.append(
                f"列「{col}」契约类型为 {expected}，本次数据出现 {'/'.join(sorted(observed))} 值"
                f"（采样前 {_TYPE_CHECK_SAMPLE} 行）")
    return rows, warnings


def normalize_rows_for_lake(rows: list[dict], *, dataset_name: str = "") -> list[dict]:
    """行格式规范化：键转 str、拒绝 bytes、嵌套结构原样保留（CSV 序列化时压 JSON）。

    route C 的 "content" 列携带原始文件 bytes 且从不写入 CSV，予以豁免。
    """
    out: list[dict] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise LakeGateError(
                f"数据集「{dataset_name}」第 {i + 1} 行不是对象（实际为 {type(row).__name__}）。"
                f"流水线输出必须是行式数据：每行一个 {{列名: 值}} 对象。")
        clean: dict = {}
        for k, v in row.items():
            key = str(k)
            if isinstance(v, (bytes, bytearray)) and key != "content":
                raise LakeGateError(
                    f"数据集「{dataset_name}」第 {i + 1} 行的列「{key}」是二进制值（{len(v)} bytes），"
                    f"无法作为行数据入湖。二进制内容请走非结构化路径（route C）或先在流水线中转码。")
            clean[key] = v
        out.append(clean)
    return out


def validate_pk(rows: list[dict], pk_cols: list[str], *,
                dataset_name: str = "", scope: str = "本次输出") -> None:
    """主键三校验：列存在 / 值非空 / 组合唯一。任一违规抛 LakeGateError。"""
    if not pk_cols or not rows:
        return
    all_cols: set[str] = set()
    for row in rows:
        all_cols.update(row.keys())
    missing = [c for c in pk_cols if c not in all_cols]
    if missing:
        raise LakeGateError(
            f"数据集「{dataset_name}」{scope}中不存在主键列 {missing}（现有列：{sorted(all_cols)[:20]}）。"
            f"请核对任务的主键配置，或调整流水线让输出携带该列。")

    seen: dict[tuple, int] = {}
    for i, row in enumerate(rows):
        values = []
        for c in pk_cols:
            v = row.get(c)
            if v is None or str(v).strip() == "":
                raise LakeGateError(
                    f"数据集「{dataset_name}」{scope}第 {i + 1} 行的主键列「{c}」为空。"
                    f"主键值必须非空，否则该行无法获得稳定身份。请在流水线中过滤或补全该列。")
            values.append(str(v).strip())
        key = tuple(values)
        if key in seen:
            raise LakeGateError(
                f"数据集「{dataset_name}」{scope}第 {seen[key] + 1} 行与第 {i + 1} 行主键重复"
                f"（{dict(zip(pk_cols, key))}）。同一主键值代表同一业务对象，{scope}内不允许重复；"
                f"若源端确有重复，请在流水线中先去重（保留最新）。")
        seen[key] = i


def resolve_pk(task_pk: str | None, declared_pk: str | None,
               pipeline_pk: str | None = None, *,
               dataset_name: str = "") -> tuple[str, str]:
    """仲裁主键声明：湖中已固化 > 流水线契约 > 任务配置。

    返回 (生效主键, 来源)。来源 ∈ "lake" | "pipeline" | "task" | ""。
    任意两方同时声明且不一致 → 硬失败：改主键 = 全部实例身份重算，必须显式
    操作（以 overwrite 重建资产清除湖中声明，或修改契约/任务配置对齐），
    不允许被静默改写。
    """
    task_cols = split_pk(task_pk)
    lake_cols = split_pk(declared_pk)
    pipe_cols = split_pk(pipeline_pk)
    if lake_cols and pipe_cols and lake_cols != pipe_cols:
        raise LakeGateError(
            f"数据集「{dataset_name}」湖中已声明主键 {lake_cols}，与流水线契约的 {pipe_cols} 不一致。"
            f"主键是该资产的身份契约；若确需变更，请先以 overwrite 方式重建该资产"
            f"（重建会清除旧声明），或修改流水线契约与湖中声明保持一致。")
    if lake_cols and task_cols and lake_cols != task_cols:
        raise LakeGateError(
            f"数据集「{dataset_name}」已声明主键 {lake_cols}，与任务配置的 {task_cols} 不一致。"
            f"主键是该资产的身份契约，不能由任务静默改写；若确需变更，请先以 overwrite 方式"
            f"重建该资产（重建会清除旧声明），或修改任务主键与湖中声明保持一致。")
    if pipe_cols and task_cols and pipe_cols != task_cols:
        raise LakeGateError(
            f"数据集「{dataset_name}」的流水线契约已声明主键 {pipe_cols}，与任务配置的 {task_cols} 不一致。"
            f"主键已上收至流水线契约管理，请清空任务的主键配置或与契约保持一致。")
    if lake_cols:
        return ",".join(lake_cols), "lake"
    if pipe_cols:
        return ",".join(pipe_cols), "pipeline"
    if task_cols:
        return ",".join(task_cols), "task"
    return "", ""


def detect_drift(new_cols: list[str], expected: list[str] | None) -> dict | None:
    """列集合漂移：对比本次输出列与既有契约列。无漂移 → None。"""
    if not expected:
        return None
    new_set, old_set = set(new_cols), set(expected)
    added = [c for c in new_cols if c not in old_set]
    missing = [c for c in expected if c not in new_set]
    if not added and not missing:
        return None
    return {"added": added, "missing": missing}


def infer_columns_typed(rows: list[dict], sample_size: int = 50) -> list[dict]:
    """带类型的列清单：列序保持首现顺序，类型按前 N 行非空值推断。"""
    from app.services.v2.pipeline.steps.schema_inference import SchemaInferenceStep

    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row.keys():
            if k != "content" and k not in seen:
                columns.append(k)
                seen.add(k)

    typed: list[dict] = []
    for col in columns:
        col_type = "string"
        for row in rows[:sample_size]:
            v = row.get(col)
            if v is None or str(v).strip() == "":
                continue
            if isinstance(v, bool):
                col_type = "boolean"
            elif isinstance(v, int):
                col_type = "integer"
            elif isinstance(v, float):
                col_type = "float"
            elif isinstance(v, (dict, list)):
                col_type = "json"
            else:
                try:
                    col_type = SchemaInferenceStep._infer_type(str(v).strip())
                except Exception:  # noqa: BLE001 — 类型推断永不阻断入湖
                    col_type = "string"
            break
        typed.append({"name": col, "type": col_type})
    return typed


def gate_rows(curated_ds, rows: list[dict], write_opts: dict | None,
              engine_contract_cols: list[str] | None = None,
              column_definitions: list | None = None) -> dict:
    """入湖前置校验（合并前）。返回 gate 结果，供调用方合并/落盘/记账。

    column_definitions：流水线字段契约（仅单产物流水线传入）——先改名/校验
    约束，其主键声明参与仲裁（lake > pipeline > task）。
    返回 {rows, pk, pk_source, drift, warnings}。主键/非空违规抛 LakeGateError。
    """
    name = getattr(curated_ds, "name", "") or ""
    schema = dict(getattr(curated_ds, "schema_json", None) or {})
    rows = normalize_rows_for_lake(rows, dataset_name=name)
    rows, contract_warnings = apply_column_contract(
        rows, column_definitions, dataset_name=name)

    pk, pk_source = resolve_pk((write_opts or {}).get("primary_key"),
                               schema.get("primary_key"),
                               contract_pk(column_definitions), dataset_name=name)
    if pk and rows:
        validate_pk(rows, split_pk(pk), dataset_name=name, scope="本次输出")

    warnings: list[str] = list(contract_warnings)
    new_cols = [c["name"] for c in infer_columns_typed(rows)] if rows else []
    # 漂移基线用「上次运行的输出列」：append 等合并模式下湖中列是历史并集，
    # 拿它当基线会让正常的追加运行每次误报"缺失列"
    baseline = schema.get("last_output_columns") or schema.get("columns")
    drift = detect_drift(new_cols, baseline) if rows else None
    if drift:
        warnings.append(f"列集合相对湖中上一版发生漂移：新增 {drift['added'] or '无'}，"
                        f"缺失 {drift['missing'] or '无'}")
    if rows and engine_contract_cols:
        contract_drift = detect_drift(new_cols, engine_contract_cols)
        if contract_drift:
            warnings.append(f"列集合相对引擎审批契约漂移：新增 {contract_drift['added'] or '无'}，"
                            f"缺失 {contract_drift['missing'] or '无'}")
            drift = drift or contract_drift

    return {"rows": rows, "pk": pk, "pk_source": pk_source,
            "drift": drift, "warnings": warnings}


def validate_upsert_base(old_rows: list[dict], pk_cols: list[str], *,
                         dataset_name: str = "") -> None:
    """upsert 合并前校验湖中存量行也满足主键契约。

    存量行缺主键列时，_dedup_by_key 会把它们全部折叠成一行（键全为空串）——
    这是静默清空资产的隐患，必须硬失败并给出迁移路径。
    """
    if not old_rows or not pk_cols:
        return
    try:
        validate_pk(old_rows, pk_cols, dataset_name=dataset_name, scope="湖中现有数据")
    except LakeGateError as e:
        raise LakeGateError(
            f"{e} —— 湖中存量数据不满足主键契约，无法安全执行主键合并（upsert）。"
            f"请先用 overwrite 方式重建该资产，让全量数据经过主键校验后再切回 upsert。") from None


def persist_contract(curated_ds, *, pk: str, pk_source: str,
                     lake_rows: list[dict], output_rows: list[dict] | None = None,
                     column_definitions: list | None = None) -> dict:
    """把契约固化/更新到 schema_json（返回新 schema dict，调用方负责赋值提交）。

    - primary_key：首次声明时写入（来源 task/pipeline）；已有声明保持不动
    - columns / columns_typed：每次成功入湖都刷新为湖中本版实际列（合并后）
    - last_output_columns：本次流水线输出的列（合并前），下次漂移检测的基线
    - field_names / contract_definitions：流水线契约的显示名与完整定义快照，
      供资产湖展示与审计（每次入湖随契约刷新）
    """
    schema = dict(getattr(curated_ds, "schema_json", None) or {})
    typed = infer_columns_typed(lake_rows)
    schema["columns"] = [c["name"] for c in typed]
    schema["columns_typed"] = typed
    if output_rows:
        schema["last_output_columns"] = [
            c["name"] for c in infer_columns_typed(output_rows)]
    defs = normalize_definitions(column_definitions)
    if defs:
        schema["field_names"] = {d["field_key"]: d["field_name"] for d in defs}
        schema["contract_definitions"] = defs
    if pk and not schema.get("primary_key"):
        schema["primary_key"] = pk
        schema["contract"] = {
            "pk_source": pk_source or "task",
            "declared_at": datetime.now(timezone.utc).isoformat(),
        }
    return schema
