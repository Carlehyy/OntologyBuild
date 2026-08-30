"""
世界模型 API — 挂 /api/v2/world-model（menu key: world_model）

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

  推演服务（发布即上线；一个项目可发布多个服务，每个绑定一个本体）：
  GET    /projects/{id}/service         代表性服务信息（兼容入口，未发布为 null）
  GET    /projects/{id}/services        项目全部已发布服务（多本体发布列表）
  POST   /projects/{id}/publish         发布/覆盖更新（同一本体覆盖，跨本体新增）
  POST   /projects/{id}/service/status  上线 / 下线（批量作用于项目全部服务）
  POST   /services/{id}/invoke          调用（写调用记录）

  推演服务注册表（跨项目，推演服务页数据源）：
  GET    /services                      列表（keyword / status / 分页，含调用统计）
  GET    /services/overview             概览统计（服务状态计数 + 全局调用统计）
  GET    /services/{id}                 详情（注册表条目口径）
  POST   /services/{id}/status          上线 / 下线（服务侧入口）

  官方脚本模板：
  GET    /templates/time-series         时序推演示例（ARIMA/SARIMA），开发页一键插入

  调用记录（只读；由 invoke 写入）：
  GET    /calls                         列表（keyword / result / service_id / 时间范围 / 分页）
  GET    /calls/overview                概览统计
  GET    /calls/daily                   近 N 天按日分桶序列（趋势图）
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


def _project_out(project, *, version_count: int = 0, service_status: str | None = None) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description or "",
        "engine_type": project.engine_type,
        "status": project.status,
        "version_count": version_count,
        "service_status": service_status,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def _project_detail_out(project, *, version_count: int = 0, service_status: str | None = None) -> dict:
    return {
        **_project_out(
            project, version_count=version_count, service_status=service_status),
        "script": project.script,
    }


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


# ── 官方脚本模板 ──


@router.get("/templates/time-series")
def get_time_series_template(
    current_user=Depends(get_current_user),
):
    return _ok(service.get_time_series_template().model_dump())


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
    project = service.get_project(db, project_id)
    svc = service.get_project_service(db, project_id)
    return _ok(_project_detail_out(
        project,
        version_count=service.count_versions(db, project_id),
        service_status=svc.status if svc else None,
    ))


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


# ── 推演服务（发布 / 状态 / 调用） ──


@router.get("/projects/{project_id}/service")
def get_project_service(
    project_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    svc = service.get_project_service(db, project_id)
    return _ok(service.service_out(db, svc).model_dump() if svc else None)


@router.get("/projects/{project_id}/services")
def list_project_services(
    project_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """项目的全部已发布服务（多本体发布后一个项目可有 N 个，每个绑定一个本体）。"""
    rows = service.list_project_services(db, project_id)
    return _ok([service.service_out(db, s).model_dump() for s in rows])


@router.post("/projects/{project_id}/publish", status_code=201)
def publish_project_service(
    project_id: str,
    body: schemas.ServicePublishRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    svc = service.publish_service(db, project_id, body, current_user)
    return _ok(service.service_out(db, svc).model_dump())


@router.post("/projects/{project_id}/service/status")
def set_project_service_status(
    project_id: str,
    body: schemas.ServiceStatusRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    svc = service.set_service_status(db, project_id, body.status)
    return _ok(service.service_out(db, svc).model_dump())


@router.post("/services/{service_id}/invoke")
def invoke_world_model_service(
    service_id: str,
    body: schemas.InvokeRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = service.invoke_service(db, service_id, body, current_user)
    return _ok(result.model_dump())


# ── 推演服务注册表（跨项目） ──


@router.get("/services")
def list_services(
    keyword: str = Query(default=""),
    status: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result_page = service.list_services(
        db, keyword=keyword, status=status, page=page, size=size)
    return _ok(result_page.model_dump())


# 声明顺序敏感：/services/overview 必须先于 /services/{service_id} 注册
@router.get("/services/overview")
def services_overview(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _ok(service.services_overview(db).model_dump())


@router.get("/services/{service_id}")
def get_service(
    service_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    svc = service.get_service_by_id(db, service_id)
    return _ok(service.service_summary_out(db, svc).model_dump())


@router.post("/services/{service_id}/status")
def set_service_status(
    service_id: str,
    body: schemas.ServiceStatusRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    svc = service.set_service_status_by_id(db, service_id, body.status)
    return _ok(service.service_summary_out(db, svc).model_dump())


# ── 调用记录（只读） ──


@router.get("/calls")
def list_calls(
    keyword: str = Query(default=""),
    result: str = Query(default="all"),
    service_id: str = Query(default=""),
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result_page = service.list_call_records(
        db, keyword=keyword, result=result, service_id=service_id,
        start=start, end=end, page=page, size=size)
    return _ok(result_page.model_dump())


@router.get("/calls/overview")
def calls_overview(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _ok(service.call_records_overview(db).model_dump())


# 声明顺序敏感：/calls/daily 必须先于 /calls/{record_id} 注册
@router.get("/calls/daily")
def calls_daily(
    days: int = Query(default=14, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _ok([item.model_dump() for item in service.call_records_daily(db, days=days)])


@router.get("/calls/{record_id}")
def get_call(
    record_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = service.get_call_record(db, record_id)
    return _ok(_call_record_out(row, with_payloads=True))
