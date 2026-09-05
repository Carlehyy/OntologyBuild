"""v2 Pipeline API — n8n / python 两种采集引擎的 CRUD、校验、发布与运行"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import Optional
from app.database import SessionLocal
from app.deps import get_current_user
from app.data_channel.pipelines.models import Pipeline
from app.data_channel.pipelines import (
    execution_service,
    management_service,
    validation_service,
)
from app.data_channel.pipelines.contracts import (
    EnabledBody,
    PipelineCreate,
    PipelineResponse,
    PipelineUpdate,
    PublishBody,
    ScriptBody,
    ValidateDefinitionsBody,
    ValidateDefinitionsError,
    ValidateDefinitionsResult,
    ValidateResult,
)
from app.data_channel.pipelines.python_engine import (
    service as python_engine_service,
)
from app.data_channel.pipelines.dependency_service import (
    reject_if_sync_chain_refs as _reject_if_sync_chain_refs,
)
from app.data_channel.pipelines.execution_service import (
    DRY_RUN_BUCKET as _DRY_RUN_BUCKET,
    dry_run_uri as _dry_run_uri,
    format_pipeline as _format_pipeline,
)
from app.data_channel.steward.models import N8nPipeline
# 确保 Dataset 模型先导入以解析 FK
import app.data_channel.datasets.models  # noqa: F401


_is_n8n_pipeline = validation_service.is_n8n_pipeline
_column_definitions_hash = validation_service.column_definitions_hash
_pipeline_execution_hash = validation_service.pipeline_execution_hash
_current_execution_hash = validation_service.current_execution_hash
_invalidate_publish_attestation = validation_service.invalidate_publish_attestation
_require_publish_attestation = (
    validation_service.require_publish_attestation
)
_require_production_executable = (
    validation_service.require_production_executable
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def pipeline_access_guard(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Pipeline 的统一写权限边界。

    读接口对所有已认证用户开放；写接口仅 admin/editor。已有 owner 的
    Pipeline 只允许 owner 或 admin 修改/发布/执行。迁移前 created_by 为空的
    存量 Pipeline 暂允许 editor 接管，避免升级后全部不可维护。
    """
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return current_user
    role = str(getattr(current_user, "role", "viewer") or "viewer")
    if role not in {"admin", "editor"}:
        raise HTTPException(403, "Viewer is read-only")
    pipeline_id = request.path_params.get("pipeline_id")
    if not pipeline_id or role == "admin":
        return current_user
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    # 404 由端点以统一文案返回；guard 只负责已存在资源的授权。
    if pipeline is not None:
        owner_id = pipeline.created_by
        if not owner_id and _is_n8n_pipeline(pipeline):
            governance = db.query(N8nPipeline).filter(
                N8nPipeline.pipeline_id == pipeline.id
            ).first()
            # n8n 影子行没有 owner 时默认拒绝，而不是沿用画布存量的 editor
            # 接管兼容策略。治理记录能证明 owner 时则按该 owner 授权。
            owner_id = governance.created_by if governance else "__unowned_n8n__"
            owner_id = owner_id or "__unowned_n8n__"
        if owner_id not in (None, "", current_user.id):
            raise HTTPException(403, "Only the pipeline owner or an admin may modify it")
    return current_user


def pipeline_lifecycle_guard(
    request: Request,
    db: Session = Depends(get_db),
):
    """串行化 n8n 远端 active 状态与本地生命周期事务。

    dry-run/正式执行会在 runner 中持有同一把 ``n8n::<workflow_id>`` 锁；这里
    只覆盖会改变发布/启用/归档状态的 API，避免外层锁与 /run 自身取锁重入。
    """
    suffix = request.url.path.rstrip("/")
    lifecycle_write = (
        request.method == "DELETE"
        or suffix.endswith("/publish")
        or suffix.endswith("/enabled")
    )
    pipeline_id = request.path_params.get("pipeline_id")
    if not lifecycle_write or not pipeline_id:
        yield
        return
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if pipeline is None or not _is_n8n_pipeline(pipeline):
        yield
        return

    from app.data_channel.steward import service as steward_service
    from app.data_channel.datasets.lock import (
        DatasetLockTimeout,
        dataset_write_lock,
    )

    rec = steward_service.record_for_pipeline(db, pipeline)
    if rec is None:
        # 具体端点会返回面向场景的 409/400；没有可信 workflow id 时不能猜锁键。
        yield
        return
    try:
        with dataset_write_lock(
            f"n8n::{rec.n8n_workflow_id}",
            bind=db.get_bind(),
            wait_timeout=30,
            stale_after=900,
        ):
            # 等锁期间另一请求可能已提交状态变化，端点必须读到最新真身。
            db.expire_all()
            yield
    except DatasetLockTimeout as exc:
        raise HTTPException(
            409, "该 n8n 流水线正在执行预览、运行或切换状态，请稍后重试。"
        ) from exc


router = APIRouter(dependencies=[
    Depends(pipeline_access_guard),
    Depends(pipeline_lifecycle_guard),
])


def _pipeline_task_refs(db: Session, pipeline_id: str):
    """返回任务池中与流水线建立关系的任务，不区分任务自身是否启用。"""
    from app.data_channel.pipeline_tasks.models import PipelineTask

    return db.query(PipelineTask).filter(
        PipelineTask.pipeline_id == pipeline_id
    ).order_by(PipelineTask.created_at.desc()).all()


# ── CRUD ──────────────────────────────────────────────────────────

@router.post("", response_model=PipelineResponse, status_code=201)
def create_pipeline(
    body: PipelineCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """创建新 Pipeline（definition.engine 仅接受 n8n / python）。"""
    return management_service.create_pipeline(
        body,
        db,
        current_user,
        format_pipeline_fn=_format_pipeline,
    )


@router.get("")
def list_pipelines(
    search: str = "",
    domain: str = "",
    status: str = "",
    engine: str = "",
    enabled: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    paginated: bool = False,
    db: Session = Depends(get_db),
):
    """Pipeline 列表：按创建时间倒序，可选服务端分页。

    ``paginated=false`` 保留原有数组返回，兼容其他消费页面；列表工作台
    显式传 ``paginated=true`` 获取 items/total/page/page_size。
    """
    return management_service.list_pipelines(
        search=search,
        domain=domain,
        status=status,
        engine=engine,
        enabled=enabled,
        page=page,
        page_size=page_size,
        paginated=paginated,
        db=db,
        is_n8n_pipeline_fn=_is_n8n_pipeline,
        format_pipeline_fn=_format_pipeline,
    )


@router.get("/{pipeline_id}", response_model=PipelineResponse)
def get_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    return management_service.get_pipeline(
        pipeline_id,
        db,
        format_pipeline_fn=_format_pipeline,
    )


@router.put("/{pipeline_id}", response_model=PipelineResponse)
def update_pipeline(pipeline_id: str, body: PipelineUpdate, db: Session = Depends(get_db)):
    return management_service.update_pipeline(
        pipeline_id,
        body,
        db,
        is_n8n_pipeline_fn=_is_n8n_pipeline,
        column_definitions_hash_fn=_column_definitions_hash,
        pipeline_execution_hash_fn=_pipeline_execution_hash,
        invalidate_publish_attestation_fn=_invalidate_publish_attestation,
        format_pipeline_fn=_format_pipeline,
    )


@router.delete("/{pipeline_id}")
def delete_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    return management_service.delete_pipeline(
        pipeline_id,
        db,
        is_n8n_pipeline_fn=_is_n8n_pipeline,
        pipeline_task_refs_fn=_pipeline_task_refs,
        reject_sync_chain_refs_fn=_reject_if_sync_chain_refs,
    )


# ── Validate ──────────────────────────────────────────────────────

@router.post("/{pipeline_id}/validate", response_model=ValidateResult)
def validate_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    """校验 Pipeline definition 是否合法——包含结构性检查和语义性检查。"""
    return validation_service.validate_pipeline_definition(pipeline_id, db)


# ── Validate Definitions ──────────────────────────────────────────

@router.post("/{pipeline_id}/validate-definitions", response_model=ValidateDefinitionsResult)
def validate_column_definitions(
    pipeline_id: str,
    body: ValidateDefinitionsBody,
    dry_run_id: str = Query(..., description="第 2 步试运行的暂存 id"),
    db: Session = Depends(get_db),
):
    """校验 column_definitions 与流水线实际产出是否一致（全量数据）。

    复用第 2 步 dry-run 暂存的完整输出，不重新执行流水线——n8n 试运行会真实
    触发生产 workflow，重复执行既有副作用又会导致「校验的数据 ≠ 预览的数据」。
    检查项：
      1. 结构 —— 字段标识命名合法且唯一；原始列在实际产出中存在
      2. 类型 —— field_type 与全量数据的值类型一致（不符 → 错误）
      3. 主键 —— 主键组合在全量数据中非空且唯一（→ 错误）
      4. 空值 —— nullable=false 的列在全量数据中无空值（→ 错误）
      5. 湖契约 —— 提示与目标资产湖已固化主键的差异（增量入库仍会硬阻断）
    """
    return validation_service.validate_column_definitions(
        pipeline_id,
        body,
        dry_run_id,
        db,
    )


# ── Publish / Unpublish ───────────────────────────────────────────

@router.post("/{pipeline_id}/publish")
def publish_pipeline(pipeline_id: str, body: PublishBody | None = None,
                     db: Session = Depends(get_db),
                     current_user=Depends(get_current_user)):
    """发布 Pipeline：永久封版身份、编排与字段契约，之后不可修改或撤回。

    发布是所有引擎共同的生命周期唯一入口（python 与 n8n 一致）。n8n 引擎
    发布时附加：校验触发器 → 激活 n8n workflow → 固化 webhook/期望列。
    发布前校验：definition 结构 + 契约结构（字段标识命名/唯一）。契约与
    数据的一致性校验（全量非空/唯一/类型）在向导第 3 步完成——发布不重
    跑流水线（n8n 试运行会真实触发生产 workflow）。契约主键与湖中已固化
    主键不一致不拦发布：「全量覆盖」运行会按新契约重写湖中声明（变更主
    键的受控通道），增量/合并运行则会在准入闸门硬失败——差异以 warnings
    随响应返回。
    """
    return validation_service.publish_pipeline_release(
        pipeline_id,
        body,
        db,
        current_user,
        validate_pipeline_fn=validate_pipeline,
    )


@router.post("/{pipeline_id}/unpublish")
def unpublish_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    """兼容旧客户端的明确拒绝端点：发布是不可逆版本边界。"""
    return management_service.reject_unpublish(pipeline_id, db)


@router.post("/{pipeline_id}/clone", response_model=PipelineResponse, status_code=201)
def clone_pipeline(pipeline_id: str, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    """克隆流水线结构为未发布、未启用的草稿副本（名称追加「_复制」）。

    python 引擎复制脚本定义与字段契约；n8n 引擎在 n8n 远端复制 workflow
    （webhook 路径重新生成、保持未激活）并新建治理记录与影子行。
    """
    return management_service.clone_pipeline(
        pipeline_id,
        db,
        current_user,
        is_n8n_pipeline_fn=_is_n8n_pipeline,
        format_pipeline_fn=_format_pipeline,
    )


# ── Versions ──────────────────────────────────────────────────────

@router.get("/{pipeline_id}/versions")
def list_versions(pipeline_id: str, db: Session = Depends(get_db)):
    """查看版本历史。"""
    return management_service.list_versions(pipeline_id, db)


# ── Run (保留原有) ────────────────────────────────────────────────

@router.post("/{pipeline_id}/run")
def run_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    return execution_service.enqueue_pipeline_run(
        pipeline_id,
        db,
        require_production_executable_fn=_require_production_executable,
    )


@router.get("/{pipeline_id}/runs")
def list_runs(pipeline_id: str, db: Session = Depends(get_db), limit: int = 50):
    return execution_service.list_pipeline_runs(pipeline_id, db, limit=limit)


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    return execution_service.get_pipeline_run(run_id, db)


@router.post("/{pipeline_id}/run-sync")
def run_pipeline_sync(pipeline_id: str, db: Session = Depends(get_db)):
    """同步执行 Pipeline（无需 Celery/Redis，仅开发/测试可用；生产环境 404）。

    运行成败只落在 PipelineRun 上，不改 pipeline.status——发布是显式动作。
    """
    return execution_service.run_pipeline_synchronously(
        pipeline_id,
        db,
        require_production_executable_fn=_require_production_executable,
    )


# ── 启用开关 + 试运行预览（不落湖；正式入湖仅由数据任务池触发）────────

@router.patch("/{pipeline_id}/enabled")
def set_pipeline_enabled(pipeline_id: str, body: EnabledBody, db: Session = Depends(get_db)):
    """启用/停用流水线：停用后任务池调度与同步链式触发都不执行。

    只有已发布的流水线才能启用（python 与 n8n 同一规则）。只要已被任务池
    关联，就锁定启用状态；任务自身是否启用不影响该保护。
    """
    return execution_service.set_pipeline_enabled(
        pipeline_id,
        body,
        db,
        task_refs_fn=_pipeline_task_refs,
        is_n8n_pipeline_fn=_is_n8n_pipeline,
        format_pipeline_fn=_format_pipeline,
    )


@router.post("/{pipeline_id}/dry-run")
def dry_run_pipeline(
    pipeline_id: str,
    db: Session = Depends(get_db),
    max_rows: int = Query(100, ge=1, le=10000, description="预览和校验的最大行数，默认 100"),
):
    """试运行：真实执行采集与加工，但【不写资产湖】。

    返回逐产物的行数预览 + 资产湖准入闸门预检结果（主键契约/列漂移），
    完整输出暂存到对象存储；用户在弹窗确认后调 commit 端点按原样入湖。
    n8n 引擎：试运行同样会触发生产 workflow（其执行方式即如此）。
    """
    return execution_service.dry_run_pipeline(
        pipeline_id,
        db,
        max_rows,
        is_n8n_pipeline_fn=_is_n8n_pipeline,
        dry_run_bucket=_DRY_RUN_BUCKET,
    )


@router.get("/{pipeline_id}/dry-run/{dry_run_id}/rows")
def dry_run_rows(
    pipeline_id: str,
    dry_run_id: str,
    output_index: int = Query(0, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """分页读取某次试运行暂存的完整输出——「展开查看全部数据」的数据源。

    单产物流水线同样应用字段契约改名，保证与预览/入湖看到的列名一致。
    """
    return execution_service.dry_run_rows(
        pipeline_id,
        dry_run_id,
        output_index,
        page,
        page_size,
        db,
        dry_run_uri_fn=_dry_run_uri,
    )


@router.post("/{pipeline_id}/dry-run/{dry_run_id}/commit", deprecated=True)
def commit_dry_run(pipeline_id: str, dry_run_id: str):
    """兼容旧客户端的明确拒绝入口。

    试执行只生成预览，不具备资产写入权限；数据任务池是流水线入湖的唯一入口。
    """
    return execution_service.reject_dry_run_commit()


# ── Python 脚本引擎 ───────────────────────────────────────────────

@router.post("/{pipeline_id}/script/execute")
def execute_pipeline_script(
    pipeline_id: str,
    body: ScriptBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """执行 Python 脚本（不落库）：真实在内核执行编辑器中的当前内容。

    返回执行结果与平台行格式（list[dict]）校验结论，供脚本编辑页展示；
    脚本级失败以 ok=false + traceback 承载，网关类故障返回 502。
    同一用户对同一流水线同时只允许一次进行中执行（409），可经取消端点终止。
    """
    return python_engine_service.execute_pipeline_script(
        pipeline_id, body, db, current_user=current_user)


@router.post("/{pipeline_id}/script/cancel")
def cancel_pipeline_script(
    pipeline_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """取消当前用户在该流水线上进行中的脚本执行；内核在下一轮询周期内销毁。"""
    return python_engine_service.cancel_pipeline_script(
        pipeline_id, db, current_user=current_user)


@router.put("/{pipeline_id}/script")
def save_pipeline_script(
    pipeline_id: str,
    body: ScriptBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """保存 Python 脚本：服务端重新执行并复验输出格式，通过才落库。

    落库同时把该版脚本冻结进保存历史。已发布流水线脚本封版（409）。
    保存成功会使既有发布校验凭证失效，发布前必须重新执行预览并校验
    字段定义。
    """
    return python_engine_service.save_pipeline_script(
        pipeline_id,
        body,
        db,
        format_pipeline_fn=_format_pipeline,
        current_user=current_user,
    )


@router.get("/{pipeline_id}/script/versions")
def list_script_versions(
    pipeline_id: str,
    db: Session = Depends(get_db),
):
    """Python 脚本的保存历史（最近在前，含脚本全文，供查看/恢复）。"""
    return python_engine_service.list_script_versions(pipeline_id, db)
