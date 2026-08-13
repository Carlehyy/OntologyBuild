"""
世界模型 — HTTP 层业务逻辑。

「执行」= 内核试跑（用户脚本 + 注入的 simulate 调用收尾），不落库；
「保存」= 双重保障的服务端一侧：重新执行复核，通过才把脚本写入项目
并冻结一个历史版本（与 Python 脚本流水线同一纪律：脚本变更必须重新
验证才能保存）。
"""
from __future__ import annotations

import json
import logging

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.data_channel.pipelines.python_engine.client import (
    PythonEngineError,
    execute_code,
    extract_payload,
    tail_stdout,
)
from app.world_model import schemas
from app.world_model.models import (
    ENGINE_TYPES,
    SCRIPT_VERSION_KEEP,
    SERVICE_STATUS_OFFLINE,
    SERVICE_STATUS_ONLINE,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    WorldModelCallRecord,
    WorldModelProject,
    WorldModelScriptVersion,
    WorldModelService,
)

logger = logging.getLogger(__name__)

# 新建项目的脚本模板：声明平台统一的推演入口契约
SCRIPT_TEMPLATE = '''def simulate(context, actions, horizon):
    """
    推演模型入口函数（平台统一契约）。

    参数：
        context: dict  — 当前状态快照（来自数字孪生/知识图谱的业务对象状态）
        actions: list  — 候选行动列表；无干预推演（纯预测）时为空列表
        horizon: int   — 推演时域（步数/期数，语义由模型自行定义）

    返回：
        dict（JSON 可序列化），建议包含：
          - trajectory: list  — 各时点的状态/指标轨迹
          - confidence: float — 置信度（0~1）
          - boundary:   str   — 结果适用边界说明
    """
    # 示例：简单的趋势外推占位实现，请替换为你的推演逻辑
    base = (context or {}).get("current_value", 0)
    trajectory = [base for _ in range(int(horizon or 1))]
    return {
        "trajectory": trajectory,
        "confidence": None,
        "boundary": "占位实现，未建模",
    }
'''

# 调试执行注入的收尾代码：调用入口函数并把返回值序列化到输出标记之间。
# 输出标记与 result 行提取共用（python_engine.client 的 __OB_RESULT_*__）。
# 注意：{test_input} 必须以 Python 字符串字面量形式嵌入（repr），
# 直接嵌入 JSON 文本会把 true/false/null 带进 Python 表达式导致 NameError。
_DEBUG_EPILOGUE_TEMPLATE = '''

# ── OntologyBuild 世界模型调试执行（自动注入，请勿删除） ──
import json as _ob_json
if "simulate" not in globals():
    raise NameError("脚本未定义入口函数 simulate(context, actions, horizon)")
_ob_test_input = _ob_json.loads({test_input})
_ob_payload = simulate(
    context=_ob_test_input.get("context") or {{}},
    actions=_ob_test_input.get("actions") or [],
    horizon=_ob_test_input.get("horizon") or 1,
)
print()
print("__OB_RESULT_BEGIN__")
print(_ob_json.dumps(_ob_payload, ensure_ascii=False, default=str))
print("__OB_RESULT_END__")
'''

# 测试入参 JSON 序列化后的长度上限（防止把超大状态快照塞进内核代码）
_TEST_INPUT_CHARS = 100_000


def _load_project(db: Session, project_id: str) -> WorldModelProject:
    project = db.get(WorldModelProject, project_id)
    if project is None:
        raise HTTPException(404, "推演模型不存在或已被删除。")
    return project


# ──────────────────────────── 项目 CRUD ────────────────────────────


def list_projects(
    db: Session,
    *,
    keyword: str = "",
    engine_type: str = "",
    page: int = 1,
    size: int = 100,
) -> schemas.ProjectListResponse:
    query = db.query(WorldModelProject)
    if keyword.strip():
        like = f"%{keyword.strip()}%"
        query = query.filter(
            WorldModelProject.name.like(like)
            | WorldModelProject.description.like(like))
    if engine_type:
        if engine_type not in ENGINE_TYPES:
            raise HTTPException(400, f"未知引擎类型：{engine_type}")
        query = query.filter(WorldModelProject.engine_type == engine_type)
    total = query.count()
    rows = (
        query.order_by(WorldModelProject.updated_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    version_counts = dict(
        db.query(
            WorldModelScriptVersion.project_id,
            func.count(WorldModelScriptVersion.id),
        )
        .group_by(WorldModelScriptVersion.project_id)
        .all()
    )
    service_statuses = dict(
        db.query(WorldModelService.project_id, WorldModelService.status)
        .filter(WorldModelService.project_id.in_([row.id for row in rows]))
        .all()
    ) if rows else {}
    return schemas.ProjectListResponse(
        items=[
            schemas.ProjectSummary(
                id=row.id,
                name=row.name,
                description=row.description or "",
                engine_type=row.engine_type,
                status=row.status,
                version_count=int(version_counts.get(row.id, 0)),
                service_status=service_statuses.get(row.id),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ],
        total=total,
    )


def create_project(
    db: Session, body: schemas.ProjectCreate, current_user,
) -> WorldModelProject:
    project = WorldModelProject(
        name=body.name.strip(),
        description=body.description.strip(),
        engine_type=body.engine_type,
        script=SCRIPT_TEMPLATE,
        status=STATUS_DRAFT,
        created_by=getattr(current_user, "id", None),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def get_project(db: Session, project_id: str) -> WorldModelProject:
    return _load_project(db, project_id)


def update_project(
    db: Session, project_id: str, body: schemas.ProjectUpdate,
) -> WorldModelProject:
    project = _load_project(db, project_id)
    if body.name is not None:
        project.name = body.name.strip()
    if body.description is not None:
        project.description = body.description.strip()
    if body.engine_type is not None:
        project.engine_type = body.engine_type
    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, project_id: str) -> None:
    """删除项目：显式清理子表（SQLite 默认不启用外键级联，与 PG 行为对齐）。

    版本随项目删除；调用记录属于审计数据，保留但解除项目关联。
    """
    project = _load_project(db, project_id)
    db.query(WorldModelScriptVersion).filter(
        WorldModelScriptVersion.project_id == project.id).delete()
    db.query(WorldModelCallRecord).filter(
        WorldModelCallRecord.project_id == project.id).update(
        {WorldModelCallRecord.project_id: None})
    db.delete(project)
    db.commit()


# ──────────────────────────── 调试执行与保存 ────────────────────────────


def _normalize_test_input(test_input: dict) -> dict:
    """测试入参归一：只保留契约三键，缺失时补默认值。"""
    raw = test_input or {}
    if not isinstance(raw, dict):
        raise HTTPException(400, "测试入参必须是 JSON 对象。")
    return {
        "context": raw.get("context") or {},
        "actions": raw.get("actions") or [],
        "horizon": raw.get("horizon") or 1,
    }


def _build_debug_code(script: str, test_input: dict) -> str:
    normalized = _normalize_test_input(test_input)
    test_input_json = json.dumps(normalized, ensure_ascii=False, default=str)
    if len(test_input_json) > _TEST_INPUT_CHARS:
        raise HTTPException(
            400,
            f"测试入参过大（{_TEST_INPUT_CHARS // 1000}K 字符上限）："
            "请裁剪 context 快照后再执行。",
        )
    # repr() 产出合法 Python 字符串字面量，避免 JSON 的 true/false/null
    # 进入内核代码后被当成 Python 标识符（NameError）
    return script + _DEBUG_EPILOGUE_TEMPLATE.format(test_input=repr(test_input_json))


def _run_debug(script: str, test_input: dict) -> schemas.ScriptExecutionResult:
    if not script.strip():
        raise HTTPException(400, "脚本内容为空，无法执行。")
    code = _build_debug_code(script, test_input)
    try:
        # full_stdout：输出标记的结果块可能超过尾部截断上限，
        # 必须在完整 stdout 上解析，解析后再截回尾部回传
        execution = execute_code(code, full_stdout=True)
    except PythonEngineError as exc:
        # 网关未配置/不可达等基础设施失败：话术与数据通道保持一致
        raise HTTPException(502, str(exc)) from exc
    payload = None
    error = execution.error
    if not error:
        try:
            payload = extract_payload(execution.stdout)
        except PythonEngineError as exc:
            error = str(exc)
    return schemas.ScriptExecutionResult(
        ok=error is None,
        payload=payload,
        stdout=tail_stdout(execution.stdout),
        error=error,
        traceback=execution.traceback,
        duration_ms=execution.duration_ms,
        kernel_id=execution.kernel_id,
    )


def execute_project_script(
    db: Session, project_id: str, body: schemas.ScriptExecuteRequest,
) -> schemas.ScriptExecutionResult:
    """调试执行：内核试跑并返回 simulate 的输出，不落库。"""
    _load_project(db, project_id)
    return _run_debug(body.script, body.test_input)


def save_project_script(
    db: Session, project_id: str, body: schemas.ScriptSaveRequest, current_user,
) -> schemas.ScriptSaveResult:
    """保存脚本：服务端重新执行复核，通过才落库并冻结版本。"""
    project = _load_project(db, project_id)
    execution = _run_debug(body.script, body.test_input)
    if not execution.ok:
        return schemas.ScriptSaveResult(ok=False, execution=execution)

    project.script = body.script
    next_version_no = (
        db.query(func.max(WorldModelScriptVersion.version_no))
        .filter(WorldModelScriptVersion.project_id == project.id)
        .scalar()
        or 0
    ) + 1
    version = WorldModelScriptVersion(
        project_id=project.id,
        version_no=next_version_no,
        script=body.script,
        test_input=_normalize_test_input(body.test_input),
        duration_ms=execution.duration_ms,
        created_by=getattr(current_user, "id", None),
    )
    db.add(version)
    db.flush()

    # 修剪历史版本：只保留最近 SCRIPT_VERSION_KEEP 版
    stale = (
        db.query(WorldModelScriptVersion)
        .filter(WorldModelScriptVersion.project_id == project.id)
        .order_by(WorldModelScriptVersion.version_no.desc())
        .offset(SCRIPT_VERSION_KEEP)
        .all()
    )
    for row in stale:
        db.delete(row)

    db.commit()
    return schemas.ScriptSaveResult(
        ok=True, execution=execution, version_no=next_version_no)


def list_script_versions(
    db: Session, project_id: str,
) -> list[schemas.ScriptVersionItem]:
    _load_project(db, project_id)
    rows = (
        db.query(WorldModelScriptVersion)
        .filter(WorldModelScriptVersion.project_id == project_id)
        .order_by(WorldModelScriptVersion.version_no.desc())
        .all()
    )
    return [
        schemas.ScriptVersionItem(
            id=row.id,
            version_no=row.version_no,
            test_input=row.test_input,
            duration_ms=row.duration_ms,
            created_by=row.created_by,
            created_at=row.created_at,
        )
        for row in rows
    ]


def get_script_version(
    db: Session, project_id: str, version_id: str,
) -> schemas.ScriptVersionDetail:
    _load_project(db, project_id)
    row = db.get(WorldModelScriptVersion, version_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(404, "脚本版本不存在。")
    return schemas.ScriptVersionDetail(
        id=row.id,
        version_no=row.version_no,
        script=row.script,
        test_input=row.test_input,
        duration_ms=row.duration_ms,
        created_by=row.created_by,
        created_at=row.created_at,
    )


# ──────────────────────────── 调用记录（只读） ────────────────────────────


def list_call_records(
    db: Session,
    *,
    keyword: str = "",
    result: str = "all",
    start=None,
    end=None,
    page: int = 1,
    size: int = 20,
) -> schemas.CallRecordListResponse:
    query = db.query(WorldModelCallRecord)
    if keyword.strip():
        like = f"%{keyword.strip()}%"
        query = query.filter(
            WorldModelCallRecord.service_name.like(like)
            | WorldModelCallRecord.caller.like(like))
    if result == "failed":
        query = query.filter(WorldModelCallRecord.ok.is_(False))
    elif result != "all":
        raise HTTPException(400, f"未知结果筛选：{result}")
    if start is not None:
        query = query.filter(WorldModelCallRecord.created_at >= start)
    if end is not None:
        query = query.filter(WorldModelCallRecord.created_at <= end)
    total = query.count()
    rows = (
        query.order_by(WorldModelCallRecord.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return schemas.CallRecordListResponse(
        items=[
            schemas.CallRecordItem(
                id=row.id,
                project_id=row.project_id,
                service_name=row.service_name,
                caller=row.caller,
                ok=row.ok,
                duration_ms=row.duration_ms,
                error=row.error,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
    )


def get_call_record(db: Session, record_id: str) -> WorldModelCallRecord:
    row = db.get(WorldModelCallRecord, record_id)
    if row is None:
        raise HTTPException(404, "调用记录不存在。")
    return row


def call_records_overview(db: Session) -> schemas.CallRecordOverview:
    total = db.query(func.count(WorldModelCallRecord.id)).scalar() or 0
    failed = (
        db.query(func.count(WorldModelCallRecord.id))
        .filter(WorldModelCallRecord.ok.is_(False))
        .scalar()
        or 0
    )
    avg_duration = (
        db.query(func.coalesce(func.avg(WorldModelCallRecord.duration_ms), 0))
        .scalar()
        or 0
    )
    return schemas.CallRecordOverview(
        total=int(total),
        failed=int(failed),
        avg_duration_ms=int(avg_duration),
    )


# ──────────────────────────── 推演服务（发布 / 状态 / 调用） ────────────────────────────


def count_versions(db: Session, project_id: str) -> int:
    return (
        db.query(func.count(WorldModelScriptVersion.id))
        .filter(WorldModelScriptVersion.project_id == project_id)
        .scalar()
        or 0
    )


def get_project_service(
    db: Session, project_id: str,
) -> WorldModelService | None:
    _load_project(db, project_id)
    return (
        db.query(WorldModelService)
        .filter(WorldModelService.project_id == project_id)
        .first()
    )


def publish_service(
    db: Session, project_id: str, body: schemas.ServicePublishRequest, current_user,
) -> WorldModelService:
    """发布为推演服务：冻结版本 + 本体语义注册，生成调用端点并上线。

    一个项目对应一个在线服务（UI 合并语义）：重新发布 = 覆盖更新同一服务。
    """
    project = _load_project(db, project_id)
    if body.version_id:
        version = db.get(WorldModelScriptVersion, body.version_id)
        if version is None or version.project_id != project.id:
            raise HTTPException(404, "指定的脚本版本不存在。")
    else:
        version = (
            db.query(WorldModelScriptVersion)
            .filter(WorldModelScriptVersion.project_id == project.id)
            .order_by(WorldModelScriptVersion.version_no.desc())
            .first()
        )
        if version is None:
            raise HTTPException(
                400, "尚未保存任何脚本版本：请先在开发页执行通过并保存，再发布。")

    service = (
        db.query(WorldModelService)
        .filter(WorldModelService.project_id == project.id)
        .first()
    )
    if service is None:
        service = WorldModelService(
            project_id=project.id,
            name=body.name.strip(),
            created_by=getattr(current_user, "id", None),
        )
        db.add(service)
        db.flush()  # 先取 id 以生成端点路径
    service.name = body.name.strip()
    service.description = body.description.strip()
    service.version_id = version.id
    service.status = SERVICE_STATUS_ONLINE  # 发布即上线
    service.endpoint_path = f"/api/v2/world-model/services/{service.id}/invoke"
    # 语义注册：值引用本体概念（本体 id + 对象类型 id），供 Agent 结构化检索
    service.applicable_object_types = {
        "ontology_id": body.applicable_ontology_id,
        "object_type_ids": body.applicable_object_type_ids,
    }
    service.preconditions = [item.model_dump() for item in body.preconditions]
    project.status = STATUS_PUBLISHED
    db.commit()
    db.refresh(service)
    return service


def set_service_status(
    db: Session, project_id: str, status: str,
) -> WorldModelService:
    service = get_project_service(db, project_id)
    if service is None:
        raise HTTPException(404, "该项目尚未发布推演服务。")
    service.status = status
    db.commit()
    db.refresh(service)
    return service


def invoke_service(
    db: Session, service_id: str, body: schemas.InvokeRequest, current_user,
) -> schemas.InvokeResult:
    """调用推演服务：执行冻结版本的脚本并写入调用记录（审计闭环）。"""
    service = db.get(WorldModelService, service_id)
    if service is None:
        raise HTTPException(404, "推演服务不存在或已被删除。")
    if service.status != SERVICE_STATUS_ONLINE:
        raise HTTPException(409, "推演服务未在线，无法调用（请先上线）。")
    version = (
        db.get(WorldModelScriptVersion, service.version_id)
        if service.version_id else None
    )
    if version is None:
        raise HTTPException(409, "推演服务未绑定可用的脚本版本。")

    caller = getattr(current_user, "username", "") or ""
    record = WorldModelCallRecord(
        project_id=service.project_id,
        service_id=service.id,
        service_name=service.name,
        caller=caller,
        ok=False,
        request_payload=body.model_dump(),
    )
    try:
        result = _run_debug(version.script, body.model_dump())
    except HTTPException as exc:
        # 网关不可达等基础设施失败：也留痕（审计），然后原样抛出
        record.error = str(exc.detail)
        db.add(record)
        db.commit()
        raise
    record.ok = result.ok
    record.duration_ms = result.duration_ms
    record.error = result.error
    if result.ok:
        record.response_payload = {"result": result.payload}
    db.add(record)
    db.commit()
    return schemas.InvokeResult(
        ok=result.ok,
        payload=result.payload if result.ok else None,
        error=result.error,
        duration_ms=result.duration_ms,
        call_id=record.id,
    )


def service_out(db: Session, service: WorldModelService) -> schemas.ServiceOut:
    """服务输出（含版本号解析）。"""
    version_no = None
    if service.version_id:
        version = db.get(WorldModelScriptVersion, service.version_id)
        version_no = version.version_no if version else None
    return schemas.ServiceOut(
        id=service.id,
        project_id=service.project_id,
        version_id=service.version_id,
        version_no=version_no,
        name=service.name,
        description=service.description or "",
        status=service.status,
        endpoint_path=service.endpoint_path,
        applicable_object_types=service.applicable_object_types,
        preconditions=service.preconditions,
        created_at=service.created_at,
        updated_at=service.updated_at,
    )
