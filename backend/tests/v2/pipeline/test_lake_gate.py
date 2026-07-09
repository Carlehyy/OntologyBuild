"""资产湖准入闸门（lake_gate）单元测试

覆盖平台规范格式的强制点：行式结构、主键三校验、声明仲裁、列漂移、
upsert 存量校验、契约固化。这些是"流水线输出严格把关"的核心承诺。
"""
import pytest

from app.data_channel.datasets.lake_gate import (
    LakeGateError,
    apply_column_contract,
    detect_drift,
    gate_rows,
    infer_columns_typed,
    normalize_rows_for_lake,
    persist_contract,
    resolve_pk,
    split_pk,
    validate_contract_structure,
    validate_pk,
    validate_upsert_base,
)


class FakeDataset:
    """gate_rows/persist_contract 只读 name 与 schema_json 两个属性"""
    def __init__(self, name="订单 curated", schema_json=None):
        self.name = name
        self.schema_json = schema_json


ROWS = [
    {"order_id": "A-1", "amount": "10.5", "city": "北京"},
    {"order_id": "A-2", "amount": "3", "city": "上海"},
]


# ── 行格式规范化 ──────────────────────────────────────────────

def test_normalize_rejects_non_dict_row():
    with pytest.raises(LakeGateError, match="不是对象"):
        normalize_rows_for_lake([{"a": 1}, ["not", "a", "row"]], dataset_name="x")


def test_normalize_rejects_bytes_value():
    with pytest.raises(LakeGateError, match="二进制"):
        normalize_rows_for_lake([{"blob": b"\x00\x01"}], dataset_name="x")


def test_normalize_exempts_route_c_content():
    rows = normalize_rows_for_lake([{"content": b"pdf-bytes", "filename": "a.pdf"}])
    assert rows[0]["content"] == b"pdf-bytes"


def test_normalize_stringifies_keys():
    rows = normalize_rows_for_lake([{1: "v"}])
    assert rows == [{"1": "v"}]


# ── 主键三校验 ────────────────────────────────────────────────

def test_validate_pk_passes_on_unique_nonnull():
    validate_pk(ROWS, ["order_id"], dataset_name="订单")


def test_validate_pk_composite_key():
    rows = [{"a": "1", "b": "x"}, {"a": "1", "b": "y"}]
    validate_pk(rows, ["a", "b"])  # 组合唯一即可
    with pytest.raises(LakeGateError, match="主键重复"):
        validate_pk(rows + [{"a": "1", "b": "x"}], ["a", "b"])


def test_validate_pk_missing_column():
    with pytest.raises(LakeGateError, match="不存在主键列"):
        validate_pk(ROWS, ["no_such_col"], dataset_name="订单")


def test_validate_pk_null_value():
    with pytest.raises(LakeGateError, match="为空"):
        validate_pk([{"order_id": "A-1"}, {"order_id": ""}], ["order_id"])


def test_validate_pk_duplicate():
    with pytest.raises(LakeGateError, match="主键重复"):
        validate_pk([{"order_id": "A-1"}, {"order_id": "A-1"}], ["order_id"])


# ── 声明仲裁 ──────────────────────────────────────────────────

def test_resolve_pk_first_declaration_from_task():
    assert resolve_pk("order_id", None) == ("order_id", "task")


def test_resolve_pk_lake_declaration_wins_when_task_empty():
    assert resolve_pk("", "order_id") == ("order_id", "lake")


def test_resolve_pk_consistent_declarations():
    assert resolve_pk("a, b", "a,b") == ("a,b", "lake")


def test_resolve_pk_conflict_hard_fails():
    with pytest.raises(LakeGateError, match="不一致"):
        resolve_pk("order_no", "order_id", dataset_name="订单")


def test_resolve_pk_neither():
    assert resolve_pk(None, None) == ("", "")
    assert split_pk(None) == []


# ── 列漂移 ────────────────────────────────────────────────────

def test_detect_drift_none_without_baseline():
    assert detect_drift(["a"], None) is None
    assert detect_drift(["a", "b"], ["a", "b"]) is None


def test_detect_drift_added_and_missing():
    drift = detect_drift(["a", "c"], ["a", "b"])
    assert drift == {"added": ["c"], "missing": ["b"]}


# ── upsert 存量校验 ───────────────────────────────────────────

def test_validate_upsert_base_blocks_legacy_rows_without_pk():
    old = [{"name": "旧行，没有主键列"}]
    with pytest.raises(LakeGateError, match="overwrite"):
        validate_upsert_base(old, ["order_id"], dataset_name="订单")


def test_validate_upsert_base_ok():
    validate_upsert_base(ROWS, ["order_id"])
    validate_upsert_base([], ["order_id"])  # 空湖直接放行


# ── gate_rows 编排 ────────────────────────────────────────────

def test_gate_rows_first_declaration_and_validation():
    ds = FakeDataset(schema_json=None)
    gate = gate_rows(ds, ROWS, {"mode": "upsert", "primary_key": "order_id"})
    assert gate["pk"] == "order_id"
    assert gate["pk_source"] == "task"
    assert gate["drift"] is None


def test_gate_rows_enforces_lake_declared_pk_on_manual_run():
    """手动画布运行（write_opts=None）同样受湖中契约约束"""
    ds = FakeDataset(schema_json={"primary_key": "order_id"})
    bad = [{"order_id": "A-1"}, {"order_id": "A-1"}]
    with pytest.raises(LakeGateError, match="主键重复"):
        gate_rows(ds, bad, None)


def test_gate_rows_conflict_between_task_and_lake():
    """增量/合并入库：任务主键与湖中声明冲突仍硬失败（不允许静默改写）"""
    ds = FakeDataset(schema_json={"primary_key": "order_id"})
    with pytest.raises(LakeGateError, match="不一致"):
        gate_rows(ds, ROWS, {"primary_key": "order_no", "mode": "upsert"})


def test_gate_rows_overwrite_redeclares_lake_pk():
    """全量覆盖是变更主键声明的唯一受控通道：新声明生效并给出重写警告"""
    ds = FakeDataset(schema_json={"primary_key": "order_id"})
    gate = gate_rows(ds, ROWS, {"primary_key": "city", "mode": "overwrite"})
    assert (gate["pk"], gate["pk_source"]) == ("city", "task")
    assert any("重写湖中主键声明" in w for w in gate["warnings"])


def test_resolve_pk_redeclare_only_on_overwrite():
    # allow_redeclare：新声明（契约 > 任务）覆盖湖中旧声明；与湖一致时仍归 lake
    assert resolve_pk("", "old_pk", "new_pk", allow_redeclare=True) == ("new_pk", "pipeline")
    assert resolve_pk("new_pk", "old_pk", None, allow_redeclare=True) == ("new_pk", "task")
    assert resolve_pk("", "old_pk", "old_pk", allow_redeclare=True) == ("old_pk", "lake")
    # 契约 vs 任务的冲突任何模式都硬失败（两个「当前意图」互相矛盾）
    with pytest.raises(LakeGateError):
        resolve_pk("a", "old_pk", "b", allow_redeclare=True)


def test_gate_rows_drift_warning_uses_last_output_baseline():
    ds = FakeDataset(schema_json={
        # 湖中列是历史并集（append 模式），基线应取上次输出列
        "columns": ["order_id", "amount", "city", "legacy_col"],
        "last_output_columns": ["order_id", "amount", "city"],
    })
    gate = gate_rows(ds, ROWS, None)
    assert gate["drift"] is None  # 与上次输出一致 → 不误报

    shrunk = [{"order_id": "A-1"}]
    gate2 = gate_rows(ds, shrunk, None)
    assert gate2["drift"] and "amount" in gate2["drift"]["missing"]
    assert gate2["warnings"]


def test_gate_rows_engine_contract_drift():
    ds = FakeDataset(schema_json=None)
    gate = gate_rows(ds, ROWS, None, engine_contract_cols=["order_id", "amount", "city", "region"])
    assert gate["drift"] and gate["drift"]["missing"] == ["region"]
    assert any("审批契约" in w for w in gate["warnings"])


# ── 契约固化 ──────────────────────────────────────────────────

def test_persist_contract_first_declaration():
    ds = FakeDataset(schema_json=None)
    schema = persist_contract(ds, pk="order_id", pk_source="task",
                              lake_rows=ROWS, output_rows=ROWS)
    assert schema["primary_key"] == "order_id"
    assert schema["contract"]["pk_source"] == "task"
    assert schema["columns"] == ["order_id", "amount", "city"]
    assert schema["last_output_columns"] == ["order_id", "amount", "city"]
    assert {c["name"]: c["type"] for c in schema["columns_typed"]}["amount"] in ("float", "integer")


def test_persist_contract_never_rewrites_existing_pk():
    ds = FakeDataset(schema_json={"primary_key": "order_id", "contract": {"pk_source": "task"}})
    schema = persist_contract(ds, pk="order_id", pk_source="lake",
                              lake_rows=ROWS, output_rows=ROWS)
    assert schema["primary_key"] == "order_id"
    assert schema["contract"]["pk_source"] == "task"  # 首次声明信息保留


def test_persist_contract_redeclares_on_overwrite_with_audit_trail():
    """全量覆盖重建：新主键重写声明，旧声明留在 contract.previous_pk 供审计"""
    ds = FakeDataset(schema_json={"primary_key": "order_id", "contract": {"pk_source": "task"}})
    schema = persist_contract(ds, pk="city", pk_source="pipeline",
                              lake_rows=ROWS, output_rows=ROWS,
                              allow_redeclare=True)
    assert schema["primary_key"] == "city"
    assert schema["contract"]["pk_source"] == "pipeline"
    assert schema["contract"]["previous_pk"] == "order_id"
    # 未开重声明开关时保持原声明（增量/合并路径的兜底不变量）
    ds2 = FakeDataset(schema_json={"primary_key": "order_id", "contract": {"pk_source": "task"}})
    schema2 = persist_contract(ds2, pk="city", pk_source="pipeline",
                               lake_rows=ROWS, output_rows=ROWS)
    assert schema2["primary_key"] == "order_id"


def test_validate_contract_structure_rejects_bad_and_dup_field_keys():
    errors = validate_contract_structure([
        {"field_key": "订单号"},                       # 非法命名
        {"field_key": "user_name"},
        {"source_key": "uname", "field_key": "user_name"},  # 重复入湖列名
    ])
    assert any("不合法" in e for e in errors)
    assert any("重复" in e for e in errors)
    assert validate_contract_structure([{"field_key": "ok_col"}]) == []


def test_apply_column_contract_dup_field_key_hard_fails():
    """重复 field_key 的改名映射会让两列互相覆盖=丢数据，运行期硬失败兜底"""
    defs = [
        {"source_key": "order_id", "field_key": "oid"},
        {"source_key": "city", "field_key": "oid"},
    ]
    with pytest.raises(LakeGateError, match="结构非法"):
        apply_column_contract(ROWS, defs, dataset_name="订单 curated")


def test_apply_column_contract_rename_collision_with_existing_column_fails():
    """改名目标与实际输出里的既有列同名（如漂移新列）→ 硬失败而非静默覆盖"""
    defs = [{"source_key": "order_id", "field_key": "city"}]
    with pytest.raises(LakeGateError, match="列名冲突"):
        apply_column_contract(ROWS, defs, dataset_name="订单 curated")


def test_infer_columns_typed_orders_and_skips_content():
    typed = infer_columns_typed([{"b": "1", "content": b"x"}, {"a": "y", "b": "2"}])
    assert [c["name"] for c in typed] == ["b", "a"]


# ── 流水线字段契约（column_definitions）───────────────────────────

from app.data_channel.datasets.lake_gate import (  # noqa: E402
    apply_column_contract,
    contract_pk,
    normalize_definitions,
)

DEFS = [
    {"source_key": "username", "field_key": "user_name", "field_name": "用户名称",
     "field_type": "string", "is_primary_key": True, "nullable": False},
    {"source_key": "age", "field_key": "age", "field_name": "年龄",
     "field_type": "integer", "is_primary_key": False, "nullable": True},
]


def test_normalize_definitions_backfills_source_key_and_maps_legacy_types():
    defs = normalize_definitions([
        {"field_key": "order_id", "field_type": "int", "is_primary_key": True},
        {"field_key": "created", "field_type": "datetime"},
        {"field_key": "ok", "field_type": "bool", "nullable": False},
        {"field_key": ""},  # 空项丢弃
    ])
    assert [d["source_key"] for d in defs] == ["order_id", "created", "ok"]
    assert [d["field_type"] for d in defs] == ["integer", "timestamp", "boolean"]
    assert defs[0]["field_name"] == "order_id"  # 缺省显示名回退列名
    assert defs[2]["nullable"] is False


def test_contract_pk_uses_field_keys():
    assert contract_pk(DEFS) == "user_name"
    assert contract_pk(None) == ""
    assert contract_pk([{"field_key": "a"}, {"field_key": "b", "is_primary_key": True}]) == "b"


def test_apply_column_contract_renames_source_to_field_key():
    rows = [{"username": "张三", "age": 20, "extra": "保留"}]
    out, warnings = apply_column_contract(rows, DEFS)
    assert out == [{"user_name": "张三", "age": 20, "extra": "保留"}]
    assert warnings == []


def test_apply_column_contract_nullable_violation_hard_fails():
    rows = [{"username": "张三"}, {"username": ""}]
    with pytest.raises(LakeGateError) as e:
        apply_column_contract(rows, DEFS, dataset_name="用户 curated")
    assert "不允许为空" in str(e.value)


def test_apply_column_contract_type_mismatch_warns_not_blocks():
    rows = [{"username": "张三", "age": "不是数字"}]
    out, warnings = apply_column_contract(rows, DEFS)
    assert out[0]["user_name"] == "张三"
    assert any("age" in w and "integer" in w for w in warnings)


def test_apply_column_contract_float_accepts_integer_values():
    defs = [{"source_key": "price", "field_key": "price", "field_name": "价格",
             "field_type": "float", "is_primary_key": False, "nullable": True}]
    _, warnings = apply_column_contract([{"price": 3}], defs)
    assert warnings == []


def test_resolve_pk_pipeline_contract_beats_task():
    pk, source = resolve_pk("", None, "user_name")
    assert (pk, source) == ("user_name", "pipeline")
    pk, source = resolve_pk("user_name", None, "user_name")
    assert (pk, source) == ("user_name", "pipeline")


def test_resolve_pk_lake_beats_pipeline():
    pk, source = resolve_pk("", "order_id", "order_id")
    assert (pk, source) == ("order_id", "lake")


def test_resolve_pk_conflicts_hard_fail():
    with pytest.raises(LakeGateError):
        resolve_pk("a", None, "b")  # 任务 vs 契约
    with pytest.raises(LakeGateError):
        resolve_pk("", "a", "b")  # 湖 vs 契约
    with pytest.raises(LakeGateError):
        resolve_pk("b", "a", None)  # 任务 vs 湖（原有语义保留）


def test_gate_rows_applies_contract_rename_and_pk():
    ds = FakeDataset(name="用户 curated", schema_json=None)
    rows = [{"username": "张三", "age": 1}, {"username": "李四", "age": 2}]
    g = gate_rows(ds, rows, None, column_definitions=DEFS)
    assert [r["user_name"] for r in g["rows"]] == ["张三", "李四"]
    assert g["pk"] == "user_name"
    assert g["pk_source"] == "pipeline"


def test_gate_rows_contract_pk_duplicate_fails():
    ds = FakeDataset(name="用户 curated", schema_json=None)
    rows = [{"username": "张三"}, {"username": "张三"}]
    with pytest.raises(LakeGateError):
        gate_rows(ds, rows, None, column_definitions=DEFS)


def test_persist_contract_records_field_names_and_definitions():
    ds = FakeDataset(schema_json=None)
    renamed = [{"user_name": "张三", "age": 20}]
    schema = persist_contract(ds, pk="user_name", pk_source="pipeline",
                              lake_rows=renamed, output_rows=renamed,
                              column_definitions=DEFS)
    assert schema["field_names"] == {"user_name": "用户名称", "age": "年龄"}
    assert schema["primary_key"] == "user_name"
    assert schema["contract"]["pk_source"] == "pipeline"
    assert [d["field_key"] for d in schema["contract_definitions"]] == ["user_name", "age"]
