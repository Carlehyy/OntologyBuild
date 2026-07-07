"""资产湖准入闸门（lake_gate）单元测试

覆盖平台规范格式的强制点：行式结构、主键三校验、声明仲裁、列漂移、
upsert 存量校验、契约固化。这些是"流水线输出严格把关"的核心承诺。
"""
import pytest

from app.data_channel.datasets.lake_gate import (
    LakeGateError,
    detect_drift,
    gate_rows,
    infer_columns_typed,
    normalize_rows_for_lake,
    persist_contract,
    resolve_pk,
    split_pk,
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
    ds = FakeDataset(schema_json={"primary_key": "order_id"})
    with pytest.raises(LakeGateError, match="不一致"):
        gate_rows(ds, ROWS, {"primary_key": "order_no"})


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


def test_infer_columns_typed_orders_and_skips_content():
    typed = infer_columns_typed([{"b": "1", "content": b"x"}, {"a": "y", "b": "2"}])
    assert [c["name"] for c in typed] == ["b", "a"]
