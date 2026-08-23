"""本体网络的只读查询服务。

职责边界：

* ``list_overview``：全部本体的清单与规模统计（类型 / 链接 / 实例数、发布状态）；
* ``build_network_graph``：逐本体复用 agent_runtime 的 ``build_workspace_graph``
  构建 L1/L2 图，合并为跨本体全局图；同名对象类型之间生成展示层桥接边；
* 路径 / 影响分析沿用单本体语义，直接委托 agent_runtime 的既有服务函数，
  仅补齐"未显式给 release 时默认取当前发布版"的口径。
"""
from __future__ import annotations

import unicodedata
from typing import Any, Iterable, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.ontology import OntologyProject
from app.ontologies.agent_runtime.boundary import AgentScope, ToolError, build_scope
from app.ontologies.agent_runtime.graph_service import (
    analyze_change_impact,
    build_workspace_graph,
    find_paths,
    get_instance_detail,
)
from app.ontologies.agent_runtime import schemas as S


# 全局画布预算。单本体内部还有 build_workspace_graph 自己的 800/2000 保护，
# 这里限制的是"一次跨本体请求"的总量，防止多选时响应无界膨胀。
NETWORK_MAX_ONTOLOGIES = 12
NETWORK_MAX_NODES = 800
NETWORK_MAX_EDGES = 2000
DEFAULT_LIMIT_PER_TYPE = 10
# 单请求语句超时：上亿实例下未命中缓存的计数/搜索可能长跑，
# 超时统一转为可读错误而非挂死数据库连接。
NETWORK_STATEMENT_TIMEOUT_MS = 8000
_TIMEOUT_MESSAGE = "数据量过大，请缩小范围或稍后重试"

_BRIDGE_LABEL = "同名类型"


def _arm_statement_timeout(db: Session) -> None:
    """为当前事务设置语句超时；上下文不具备事务时静默跳过。"""
    try:
        db.execute(text(f"SET LOCAL statement_timeout = '{NETWORK_STATEMENT_TIMEOUT_MS}ms'"))
    except Exception:  # noqa: BLE001 - 超时护栏失败不阻塞业务
        pass


def _raise_timeout(error: OperationalError) -> None:
    """PG 语句超时（SQLSTATE 57014）映射为可读的请求错误。"""
    if getattr(error.orig, "pgcode", None) == "57014":
        raise NetworkRequestError(_TIMEOUT_MESSAGE) from error
    raise error


def _normalize_key(raw: Optional[str]) -> str:
    """名称归一化：NFKC + casefold + 去空白，供同名启发式比较。"""
    if not raw:
        return ""
    return unicodedata.normalize("NFKC", str(raw)).strip().casefold()


def _resolve_scope(
    db: Session, project: OntologyProject, *, release_id: Optional[str] = None
) -> tuple[AgentScope, Optional[str], bool]:
    """解析一个本体的读取口径。

    返回 ``(scope, effective_release_id, published)``。已发布本体固定读
    当前发布版的快照 + 对应实例；未发布本体回退工作区实时数据（release_id
    为 None），由调用方在响应中显式标注"未发布"，避免用户误以为是发布态。
    """
    effective_release = release_id
    if effective_release is None:
        effective_release = project.current_release_id or None
    _, _, scope = build_scope(db, project.id, release_id=effective_release)
    return scope, effective_release, effective_release is not None


def _section_from_scope(
    project: OntologyProject, scope: AgentScope, release_id: Optional[str],
    *, fresh: bool = False,
) -> dict:
    counts = scope.instance_counts(fresh=fresh)
    version = None
    if release_id and scope.release is not None:
        version = getattr(scope.release, "version_number", None)
    return {
        "id": project.id,
        "name": project.name,
        "domain": project.domain,
        "published": release_id is not None,
        "releaseId": release_id,
        "version": version,
        "typeCount": len(scope.object_types),
        "linkTypeCount": len(scope.link_types),
        "instanceCount": int(sum(counts.values())),
    }


def _ontology_section(
    db: Session, project: OntologyProject, *, fresh: bool = False,
) -> dict:
    """单个本体的概览统计（overview 与 graph 共用口径）。"""
    scope, release_id, _published = _resolve_scope(db, project)
    return _section_from_scope(project, scope, release_id, fresh=fresh)


def list_overview(db: Session, *, fresh: bool = False) -> list[dict]:
    _arm_statement_timeout(db)
    try:
        return _list_overview(db, fresh=fresh)
    except OperationalError as error:
        _raise_timeout(error)
        raise


def _list_overview(db: Session, *, fresh: bool) -> list[dict]:
    projects = (
        db.query(OntologyProject).order_by(OntologyProject.created_at, OntologyProject.id).all()
    )
    items: list[dict] = []
    for project in projects:
        try:
            items.append(_ontology_section(db, project, fresh=fresh))
        except ToolError as error:
            # 单个本体口径异常（如发布指针损坏）不拖垮整个清单。
            items.append({
                "id": project.id,
                "name": project.name,
                "domain": project.domain,
                "published": False,
                "releaseId": None,
                "version": None,
                "typeCount": 0,
                "linkTypeCount": 0,
                "instanceCount": 0,
                "error": str(error),
            })
    return items


class NetworkRequestError(ToolError):
    """请求参数层面的错误（区别于单个本体的数据错误）。"""


def _tagged(node: dict, section: dict) -> dict:
    tagged = dict(node)
    tagged["ontologyId"] = section["id"]
    tagged["ontologyName"] = section["name"]
    return tagged


def _tagged_edge(edge: dict, section: dict) -> dict:
    tagged = dict(edge)
    tagged["ontologyId"] = section["id"]
    tagged["ontologyName"] = section["name"]
    return tagged


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: str) -> str:
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def compute_bridge_groups(type_nodes: list[dict]) -> list[dict]:
    """把不同本体间同名的对象类型聚成桥接组（纯展示层启发式）。

    匹配规则：technical name 或 display_name 归一化后相同即视为同名；
    同一本体内部的两个类型永远不会互连。节点键带 ontologyId 前缀，即使
    两个本体存在同 id 的类型（测试夹具常见）也不会被误并成一个节点。
    返回的组内成员按 (ontologyId, label) 稳定排序，保证响应可复现。
    """
    def uid(node: dict) -> str:
        return f"{node['ontologyId']}::{node['id']}"

    uf = _UnionFind()
    for node in type_nodes:
        uf.add(uid(node))
    by_name: dict[str, list[dict]] = {}
    by_display: dict[str, list[dict]] = {}
    for node in type_nodes:
        technical = _normalize_key(node.get("technicalName"))
        display = _normalize_key(node.get("label"))
        for key_bucket, key in ((by_name, technical), (by_display, display)):
            if not key:
                continue
            for peer in key_bucket.get(key, []):
                uf.union(uid(node), uid(peer))
            key_bucket.setdefault(key, []).append(node)

    members: dict[str, list[dict]] = {}
    for node in type_nodes:
        members.setdefault(uf.find(uid(node)), []).append(node)

    groups: list[dict] = []
    for group_nodes in members.values():
        ontology_ids = {node["ontologyId"] for node in group_nodes}
        if len(ontology_ids) < 2:
            continue
        ordered = sorted(group_nodes, key=lambda n: (n["ontologyId"], n["label"]))
        groups.append({
            "key": f"bridge-group-{uuid4().hex[:8]}",
            "label": ordered[0]["label"],
            "members": [
                {
                    "nodeId": node["id"],
                    "entityId": node["entityId"],
                    "ontologyId": node["ontologyId"],
                    "ontologyName": node["ontologyName"],
                    "label": node["label"],
                }
                for node in ordered
            ],
        })
    groups.sort(key=lambda g: g["label"])
    return groups


def bridge_edges(groups: Iterable[dict]) -> list[dict]:
    """同名桥接组 → 组内两两虚线边（纯展示层，不落库）。"""
    edges: list[dict] = []
    for group in groups:
        members = group["members"]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                left, right = members[i], members[j]
                pair = sorted([left["nodeId"], right["nodeId"]])
                edges.append({
                    "id": f"bridge:{pair[0]}::{pair[1]}",
                    "kind": "bridge",
                    "source": left["nodeId"],
                    "target": right["nodeId"],
                    "label": _BRIDGE_LABEL,
                    "bridgeGroup": group["key"],
                    "crossOntology": True,
                })
    return edges


def _truncate_merged(
    nodes: list[dict], edges: list[dict]
) -> tuple[list[dict], list[dict], dict]:
    truncated = False
    kept_nodes = nodes[:NETWORK_MAX_NODES]
    if len(nodes) > NETWORK_MAX_NODES:
        truncated = True
    visible_ids = {node["id"] for node in kept_nodes}
    kept_edges: list[dict] = []
    dropped_edges = 0
    for edge in edges:
        if edge["source"] not in visible_ids or edge["target"] not in visible_ids:
            dropped_edges += 1
            truncated = True
            continue
        if len(kept_edges) >= NETWORK_MAX_EDGES:
            dropped_edges += 1
            truncated = True
            continue
        kept_edges.append(edge)
    meta = {
        "nodeBudget": NETWORK_MAX_NODES,
        "edgeBudget": NETWORK_MAX_EDGES,
        "truncated": truncated,
        "droppedEdges": dropped_edges,
        "nodeCount": len(kept_nodes),
        "edgeCount": len(kept_edges),
    }
    return kept_nodes, kept_edges, meta


def build_network_graph(
    db: Session,
    *,
    ontology_ids: list[str],
    level: int = 2,
    query: Optional[str] = None,
    limit_per_type: int = DEFAULT_LIMIT_PER_TYPE,
    bridge_same_name: bool = True,
    fresh: bool = False,
) -> dict:
    """构建跨本体全局图：逐本体投影后合并 + 同名类型桥接。"""
    _arm_statement_timeout(db)
    try:
        return _build_network_graph(
            db,
            ontology_ids=ontology_ids,
            level=level,
            query=query,
            limit_per_type=limit_per_type,
            bridge_same_name=bridge_same_name,
            fresh=fresh,
        )
    except OperationalError as error:
        _raise_timeout(error)
        raise


def _build_network_graph(
    db: Session,
    *,
    ontology_ids: list[str],
    level: int = 2,
    query: Optional[str] = None,
    limit_per_type: int = DEFAULT_LIMIT_PER_TYPE,
    bridge_same_name: bool = True,
    fresh: bool = False,
) -> dict:
    unique_ids = list(dict.fromkeys(oid.strip() for oid in ontology_ids if oid.strip()))
    if not unique_ids:
        raise NetworkRequestError("请至少选择一个本体")
    if len(unique_ids) > NETWORK_MAX_ONTOLOGIES:
        raise NetworkRequestError(f"一次最多同时查看 {NETWORK_MAX_ONTOLOGIES} 个本体")
    depth = 1 if level <= 1 else 2
    per_type = max(1, min(int(limit_per_type), 20))
    keyword = (query or "").strip() or None

    nodes: list[dict] = []
    edges: list[dict] = []
    sections: list[dict] = []
    errors: list[dict] = []

    for ontology_id in unique_ids:
        project = (
            db.query(OntologyProject)
            .filter(OntologyProject.id == ontology_id)
            .first()
        )
        if not project:
            errors.append({"ontologyId": ontology_id, "message": "本体不存在"})
            continue
        try:
            scope, _release_id, _published = _resolve_scope(db, project)
            section = _section_from_scope(project, scope, _release_id, fresh=fresh)
            graph = build_workspace_graph(
                scope,
                depth=depth,
                query=keyword,
                limit_per_type=per_type,
            )
        except ToolError as error:
            errors.append({"ontologyId": ontology_id, "message": str(error)})
            continue
        sections.append(section)
        nodes.extend(_tagged(node, section) for node in graph["nodes"])
        edges.extend(_tagged_edge(edge, section) for edge in graph["edges"])

    type_nodes = [node for node in nodes if node.get("kind") == "object_type"]
    groups = compute_bridge_groups(type_nodes) if bridge_same_name else []
    all_edges = edges + bridge_edges(groups)

    kept_nodes, kept_edges, meta = _truncate_merged(nodes, all_edges)
    total_instances = sum(section["instanceCount"] for section in sections)
    return {
        "level": depth,
        "query": keyword,
        "limitPerType": per_type,
        "ontologies": sections,
        "errors": errors,
        "nodes": kept_nodes,
        "edges": kept_edges,
        "bridges": {"enabled": bool(bridge_same_name), "groups": groups},
        "meta": {
            **meta,
            "selectedOntologies": len(sections),
            "totalInstances": total_instances,
        },
    }


# ------------------------------------------------ 路径 / 影响 / 实例详情


def network_instance_detail(
    db: Session, ontology_id: str, instance_id: str, *, release_id: Optional[str] = None
) -> dict:
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise ToolError("本体不存在")
    scope, _release_id, _published = _resolve_scope(db, project, release_id=release_id)
    return get_instance_detail(scope, instance_id)


def network_find_paths(
    db: Session, ontology_id: str, body: S.GraphPathRequest
) -> dict:
    _arm_statement_timeout(db)
    try:
        return _network_find_paths(db, ontology_id, body)
    except OperationalError as error:
        _raise_timeout(error)
        raise


def _network_find_paths(
    db: Session, ontology_id: str, body: S.GraphPathRequest
) -> dict:
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise ToolError("本体不存在")
    scope, _release_id, _published = _resolve_scope(db, project, release_id=body.release_id)
    return find_paths(
        scope,
        body.source_instance_id,
        body.target_instance_id,
        direction=body.direction,
        max_depth=body.max_depth,
        max_paths=body.max_paths,
    )


def network_analyze_impact(
    db: Session, ontology_id: str, body: S.GraphImpactRequest
) -> dict:
    _arm_statement_timeout(db)
    try:
        return _network_analyze_impact(db, ontology_id, body)
    except OperationalError as error:
        _raise_timeout(error)
        raise


def _network_analyze_impact(
    db: Session, ontology_id: str, body: S.GraphImpactRequest
) -> dict:
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise ToolError("本体不存在")
    scope, _release_id, _published = _resolve_scope(db, project, release_id=body.release_id)
    return analyze_change_impact(
        scope,
        body.instance_id,
        body.property,
        body.proposed_value,
        direction=body.direction,
        max_depth=body.max_depth,
    )
