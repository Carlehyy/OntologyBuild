"""
世界模型 API — 挂 /api/v2/world-model（menu key: ontologies.world_model）

  项目管理：
  GET    /projects                      列表（keyword / engine_type / 分页）
  POST   /projects                      新建（脚本初始化为平台契约模板）
  GET    /projects/{id}                 详情（含脚本）
  PATCH  /projects/{id}                 编辑基本信息
  DELETE /projects/{id}                 删除（版本级联删除）

  开发调试（复用数据通道的内核网关执行通道）：
  POST   /projects/{id}/execute         调试执行（不落库）
  POST   /projects/{id}/save            保存（重跑复核 + 冻结版本）
  GET    /projects/{id}/versions        脚本版本列表
  GET    /projects/{id}/versions/{vid}  版本详情（含脚本，供恢复）

  调用记录（只读；写入方属二期「发布为推演服务」）：
  GET    /calls                         列表（keyword / result / 时间范围 / 分页）
  GET    /calls/overview                概览统计
  GET    /calls/{id}                    详情（含请求/响应快照）
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.world_model import schemas, service

router = APIRouter()


def _ok(data):
    return {"data": data}


def _project_out(project, *, version_count: int = 0) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description or "",
        "engine_type": project.engine_type,
        "status": project.status,
        "version_count": version_count,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def _project_detail_out(project) -> dict:
    return {**_project_out(project), "script": project.script}


def _call_record_out(row, *, with_payloads: bool = False) -> dict:
    data = {
        "id": row.id,
        "project_id": row.project_id,
        "service_name": row.service_name,
        "caller": row.caller,
        "ok": row.ok,
        "duration_ms": row.duration_ms,
        "error": row.error,
        "created_at": row.created_at,
    }
    if with_payloads:
        data["request_payload"] = row.request_payload
        data["response_payload"] = row.response_payload
    return data


# ── 项目管理 ──


@router.get("/projects")
def list_projects(
    keyword: str = Query(default=""),
    engine_type: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = service.list_projects(
        db, keyword=keyword, engine_type=engine_type, page=page, size=size)
    return _ok(result.model_dump())


@router.post("/projects", status_code=201)
def create_project(
    body: schemas.ProjectCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = service.create_project(db, body, current_user)
    return _ok(_project_detail_out(project))


@router.get("/projects/{project_id}")
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _ok(_project_detail_out(service.get_project(db, project_id)))


@router.patch("/projects/{project_id}")
def update_project(
    project_id: str,
    body: schemas.ProjectUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = service.update_project(db, project_id, body)
    return _ok(_project_out(project))


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    service.delete_project(db, project_id)
    return _ok({"status": "deleted", "id": project_id})


# ── 开发调试 ──


@router.post("/projects/{project_id}/execute")
def execute_script(
    project_id: str,
    body: schemas.ScriptExecuteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = service.execute_project_script(db, project_id, body)
    return _ok(result.model_dump())


@router.post("/projects/{project_id}/save")
def save_script(
    project_id: str,
    body: schemas.ScriptSaveRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = service.save_project_script(db, project_id, body, current_user)
    return _ok(result.model_dump())


@router.get("/projects/{project_id}/versions")
def list_versions(
    project_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    items = service.list_script_versions(db, project_id)
    return _ok([item.model_dump() for item in items])


@router.get("/projects/{project_id}/versions/{version_id}")
def get_version(
    project_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _ok(service.get_script_version(db, project_id, version_id).model_dump())


# ── 调用记录（只读） ──


@router.get("/calls")
def list_calls(
    keyword: str = Query(default=""),
    result: str = Query(default="all"),
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result_page = service.list_call_records(
        db, keyword=keyword, result=result, start=start, end=end,
        page=page, size=size)
    return _ok(result_page.model_dump())


@router.get("/calls/overview")
def calls_overview(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _ok(service.call_records_overview(db).model_dump())


@router.get("/calls/{record_id}")
def get_call(
    record_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = service.get_call_record(db, record_id)
    return _ok(_call_record_out(row, with_payloads=True))
