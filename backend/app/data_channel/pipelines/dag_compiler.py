"""Pipeline DAG compiler and production structural guardrails.

The canvas runtime currently executes one linear branch per connector.  A graph
outside that capability must be rejected explicitly; silently flattening it
would make the diagram and the executed data flow disagree.
"""
from __future__ import annotations

from dataclasses import dataclass


NODE_PHASE_ORDER = {
    "connector": 1,
    "storage": 2,
    "transform": 3,
    "output": 4,
}

_ALLOWED_EDGES = {
    "connector": {"storage"},
    "storage": {"transform", "output"},
    "transform": {"transform", "output"},
    "output": set(),
}


@dataclass
class DAGCompileError(ValueError):
    """A definition cannot be represented by the current runtime."""

    errors: list[str]

    def __str__(self) -> str:
        return "；".join(self.errors)


def _empty_plan() -> dict:
    return {
        "phases": [],
        "linear": True,
        "execution_order": [],
        "paths": {},
    }


def compile_definition(definition: dict | None) -> dict:
    """Compile and validate the canvas DSL.

    Supported production shape is one linear path per connector:
    ``connector -> storage -> [transform ...] -> output``.  Multiple connector
    paths are allowed, but branches and merges are rejected until the runtime
    has explicit fan-out/fan-in semantics.
    """
    if not definition:
        return _empty_plan()

    nodes = definition.get("nodes") or []
    edges = definition.get("edges") or []
    if not nodes:
        return _empty_plan()

    errors: list[str] = []
    node_map: dict[str, dict] = {}
    duplicate_ids: set[str] = set()
    for index, node in enumerate(nodes):
        node_id = str(node.get("id") or "").strip()
        node_type = str(node.get("type") or "").strip()
        if not node_id:
            errors.append(f"第 {index + 1} 个节点缺少 id")
            continue
        if node_id in node_map:
            duplicate_ids.add(node_id)
        node_map[node_id] = node
        if node_type not in NODE_PHASE_ORDER:
            errors.append(f"节点 {node_id} 使用了不支持的类型 {node_type or '<empty>'}")
    if duplicate_ids:
        errors.append(f"节点 id 重复: {sorted(duplicate_ids)}")

    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_map}
    reverse: dict[str, list[str]] = {node_id: [] for node_id in node_map}
    seen_edges: set[tuple[str, str]] = set()
    for index, edge in enumerate(edges):
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        if source not in node_map or target not in node_map:
            errors.append(f"第 {index + 1} 条边引用不存在的节点: {source} -> {target}")
            continue
        if source == target:
            errors.append(f"节点 {source} 不能连接到自身")
            continue
        edge_key = (source, target)
        if edge_key in seen_edges:
            errors.append(f"重复边: {source} -> {target}")
            continue
        seen_edges.add(edge_key)
        source_type = node_map[source].get("type")
        target_type = node_map[target].get("type")
        if target_type not in _ALLOWED_EDGES.get(source_type, set()):
            errors.append(f"不支持的节点连接: {source}({source_type}) -> {target}({target_type})")
        adjacency[source].append(target)
        reverse[target].append(source)

    connectors = [node_id for node_id, node in node_map.items() if node.get("type") == "connector"]
    outputs = [node_id for node_id, node in node_map.items() if node.get("type") == "output"]
    if not connectors:
        errors.append("画布 Pipeline 至少需要一个 Connector 节点")
    if not outputs:
        errors.append("画布 Pipeline 至少需要一个 Output 节点")

    for node_id, node in node_map.items():
        node_type = node.get("type")
        indegree = len(reverse[node_id])
        outdegree = len(adjacency[node_id])
        if node_type == "connector":
            if indegree:
                errors.append(f"Connector {node_id} 不能有入边")
            if outdegree != 1:
                errors.append(f"Connector {node_id} 必须且只能连接一个下游")
        elif node_type == "output":
            if indegree != 1:
                errors.append(f"Output {node_id} 必须且只能有一个上游")
            if outdegree:
                errors.append(f"Output {node_id} 不能有下游")
        else:
            if indegree != 1 or outdegree != 1:
                errors.append(
                    f"节点 {node_id} 当前必须是线性节点（一个上游、一个下游）；"
                    "分支/合流尚无可靠运行语义"
                )

    # Kahn sort: cycles are a hard error, never appended and executed anyway.
    indegrees = {node_id: len(reverse[node_id]) for node_id in node_map}
    queue = [node_id for node_id in node_map if indegrees[node_id] == 0]
    execution_order: list[str] = []
    while queue:
        node_id = queue.pop(0)
        execution_order.append(node_id)
        for target in adjacency[node_id]:
            indegrees[target] -= 1
            if indegrees[target] == 0:
                queue.append(target)
    if len(execution_order) != len(node_map):
        cyclic = [node_id for node_id in node_map if node_id not in execution_order]
        errors.append(f"Pipeline 存在环路: {cyclic}")

    paths: dict[str, list[str]] = {}
    visited: set[str] = set()
    for connector_id in connectors:
        path: list[str] = []
        current = connector_id
        local_seen: set[str] = set()
        while current and current not in local_seen:
            local_seen.add(current)
            path.append(current)
            next_nodes = adjacency.get(current) or []
            current = next_nodes[0] if len(next_nodes) == 1 else ""
        if path and node_map[path[-1]].get("type") != "output":
            errors.append(f"Connector {connector_id} 的路径没有终止于 Output")
        paths[connector_id] = path
        visited.update(path)

    disconnected = [node_id for node_id in node_map if node_id not in visited]
    if disconnected:
        errors.append(f"存在未接入完整数据路径的节点: {disconnected}")

    if errors:
        # Preserve first occurrence order while avoiding repetitive messages.
        raise DAGCompileError(list(dict.fromkeys(errors)))

    phases: list[dict] = []
    for phase_no in sorted(set(NODE_PHASE_ORDER.values())):
        ids = [node_id for node_id in execution_order
               if NODE_PHASE_ORDER.get(node_map[node_id].get("type")) == phase_no]
        if ids:
            phases.append({"name": node_map[ids[0]].get("type"), "node_ids": ids})

    return {
        "phases": phases,
        "linear": len(connectors) == 1,
        "execution_order": execution_order,
        "paths": paths,
    }
