import pytest

from app.data_channel.pipelines.dag_compiler import DAGCompileError, compile_definition
from app.tasks.v2.pipeline_run import _pipeline_runtime_config


def _node(node_id: str, node_type: str, config: dict | None = None) -> dict:
    return {"id": node_id, "type": node_type, "config": config or {}}


def _linear_definition(prefix: str = "a", transform_config: dict | None = None) -> dict:
    return {
        "nodes": [
            _node(f"{prefix}_c", "connector"),
            _node(f"{prefix}_s", "storage"),
            _node(f"{prefix}_t", "transform", transform_config),
            _node(f"{prefix}_o", "output"),
        ],
        "edges": [
            {"source": f"{prefix}_c", "target": f"{prefix}_s"},
            {"source": f"{prefix}_s", "target": f"{prefix}_t"},
            {"source": f"{prefix}_t", "target": f"{prefix}_o"},
        ],
    }


def test_compile_returns_explicit_connector_path():
    plan = compile_definition(_linear_definition())

    assert plan["execution_order"] == ["a_c", "a_s", "a_t", "a_o"]
    assert plan["paths"] == {"a_c": ["a_c", "a_s", "a_t", "a_o"]}


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d["nodes"].append(_node("a_t", "transform")), "节点 id 重复"),
        (lambda d: d["nodes"].append(_node("orphan", "transform")), "未接入完整数据路径"),
        (lambda d: d["edges"].append({"source": "a_t", "target": "a_s"}), "环路"),
        (lambda d: d["edges"].append({"source": "a_s", "target": "a_o"}), "分支/合流"),
    ],
)
def test_compile_rejects_graphs_runtime_cannot_execute(mutate, expected):
    definition = _linear_definition()
    mutate(definition)

    with pytest.raises(DAGCompileError) as exc:
        compile_definition(definition)

    assert expected in str(exc.value)


def test_runtime_config_only_uses_transforms_on_selected_path():
    left = _linear_definition("left", {
        "path": "structured",
        "steps": [{"op": "drop_duplicates", "params": {"keys": ["id"]}}],
    })
    right = _linear_definition("right", {
        "path": "unstructured",
        "steps": [{"op": "llm_structurize", "params": {"fields": ["name"]}}],
    })
    definition = {
        "nodes": left["nodes"] + right["nodes"],
        "edges": left["edges"] + right["edges"],
    }

    class Pipeline:
        spec = {}

    pipeline = Pipeline()
    pipeline.definition = definition
    plan = compile_definition(definition)

    route, spec = _pipeline_runtime_config(pipeline, plan["paths"]["left_c"])

    assert route == "A"
    assert spec["cleansing"]["deduplicate"] is True
    assert "md_to_structured" not in spec
