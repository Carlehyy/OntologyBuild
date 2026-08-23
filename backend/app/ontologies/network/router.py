"""本体网络 API — /api/v2/ontology-network/*

  GET  /overview                        全部本体清单 + 规模统计
  GET  /graph                           跨本体全局图（L1/L2 + 同名桥接）
  GET  /{ontology_id}/instances/{id}    实例详情（右侧面板字段列表）
  POST /{ontology_id}/paths             单本体最短路径（复用 agent_runtime 服务）
  POST /{ontology_id}/impact            单本体只读影响推演（dry-run）

全部端点只读。路由层不做业务判断，应用逻辑在 service.py；权限由挂载点的
``menu_guard("ontology_model.network")`` 统一守卫，与页面菜单键一致。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.ontologies.agent_runtime import schemas as S
from app.ontologies.agent_runtime.boundary import ToolError
from app.ontologies.network import service


router = APIRouter()


def _ok(data):
    return {"data": data}


@router.get("/overview")
def get_overview(
    fresh: bool = Query(default=False, description="跳过实例计数缓存，强制直查"),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """全部本体的发布口径与规模统计，供页面数据范围选择器使用。"""
    return _ok(service.list_overview(db, fresh=fresh))


@router.get("/graph")
def get_network_graph(
    ontology_ids: str = Query(..., max_length=2000, description="逗号分隔的本体 id"),
    level: int = Query(default=2, ge=1, le=2),
    query: str | None = Query(default=None, max_length=200),
    limit_per_type: int = Query(default=service.DEFAULT_LIMIT_PER_TYPE, ge=1, le=20),
    bridge_same_name: bool = Query(default=True),
    fresh: bool = Query(default=False, description="跳过实例计数缓存，强制直查"),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """跨本体全局图：各本体子图叠加 + 可选同名类型虚线桥接。"""
    try:
        data = service.build_network_graph(
            db,
            ontology_ids=ontology_ids.split(","),
            level=level,
            query=query,
            limit_per_type=limit_per_type,
            bridge_same_name=bridge_same_name,
            fresh=fresh,
        )
    except ToolError as error:
        raise HTTPException(422, str(error)) from error
    return _ok(data)


@router.get("/{ontology_id}/instances/{instance_id}")
def get_network_instance(
    ontology_id: str,
    instance_id: str,
    release_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    try:
        data = service.network_instance_detail(
            db, ontology_id, instance_id, release_id=release_id)
    except ToolError as error:
        raise HTTPException(404, str(error)) from error
    return _ok(data)


@router.post("/{ontology_id}/paths")
def query_network_paths(
    ontology_id: str,
    body: S.GraphPathRequest,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    try:
        data = service.network_find_paths(db, ontology_id, body)
    except ToolError as error:
        raise HTTPException(422, str(error)) from error
    return _ok(data)


@router.post("/{ontology_id}/impact")
def query_network_impact(
    ontology_id: str,
    body: S.GraphImpactRequest,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    try:
        data = service.network_analyze_impact(db, ontology_id, body)
    except ToolError as error:
        raise HTTPException(422, str(error)) from error
    return _ok(data)
