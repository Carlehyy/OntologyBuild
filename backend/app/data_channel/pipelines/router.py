"""v2 Pipeline API — 支持新 DSL (nodes/edges) + 旧 steps 格式兼容"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone
from app.database import SessionLocal
from app.config import settings
from app.deps import get_current_user
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineVersion
from app.data_channel.steward.models import N8nPipeline
# 确保 Dataset 模型先导入以解析 FK
import app.models.v2.dataset  # noqa: F401

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


# ── Pydantic Models ────────────────────────────────────────────────

class PipelineCreate(BaseModel):
    name: str
    domain: str = "通用"
    description: str = ""
    source_dataset_id: Optional[str] = None
    route: Optional[str] = None  # A|B|C (legacy)
    spec: Optional[dict] = None  # legacy steps
    definition: Optional[dict] = None  # new DSL: {nodes: [...], edges: [...]}


class PipelineUpdate(BaseModel):
    # 状态经 publish/归档端点、启用经 PATCH /enabled——通用更新不允许
    # 携带 status/enabled，防止绕过状态机（封版/启用约束）。
    name: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    source_dataset_id: Optional[str] = None
    route: Optional[str] = None
    spec: Optional[dict] = None
    definition: Optional[dict] = None
    column_definitions: Optional[list] = None  # [{source_key, field_key, field_name, field_type, is_primary_key, nullable}]


class PipelineResponse(BaseModel):
    id: str
    name: str
    domain: Optional[str] = "通用"
    description: Optional[str] = ""
    source_dataset_id: Optional[str] = None
    route: Optional[str] = None
    spec: Optional[dict] = None
    definition: Optional[dict] = None
    status: str = "draft"
    # 与列表端点同一口径——response_model 会过滤未声明字段，漏声明会让
    # 详情/更新接口悄悄吞掉 enabled/契约（列表返回全量、详情缺字段的坑）
    engine: Optional[str] = None
    enabled: Optional[bool] = None
    column_definitions: Optional[list] = None
    branch: Optional[str] = "main"
    version: int = 1
    target_curated_ids: Optional[list] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class ValidateResult(BaseModel):
    valid: bool
    errors: list[dict] = []
    warnings: list[dict] = []


def _is_n8n_pipeline(pl: Pipeline) -> bool:
    """数据管家托管的 n8n 影子流水线 — 编排在数据管家，画布路径绕行；
    生命周期（发布/停启用/归档）与画布流水线同走本路由。"""
    return ((pl.definition or {}).get("engine") == "n8n")


def _column_definitions_hash(definitions: list | None) -> str:
    """Canonical contract fingerprint used by n8n publish attestations."""
    from app.data_channel.datasets.lake_gate import normalize_definitions
    from app.data_channel.steward.service import canonical_json_hash

    return canonical_json_hash(normalize_definitions(definitions))


def _require_production_executable(pl: Pipeline) -> None:
    """Production writes may only execute an immutable, enabled release."""
    if settings.environment != "production":
        return
    if (pl.status or "") != "published":
        raise HTTPException(409, "生产环境只允许运行已发布的流水线；草稿请使用 dry-run 预览。")
    if not bool(pl.enabled):
        raise HTTPException(409, "流水线当前未启用，生产运行已拒绝。")


def _reject_if_sync_chain_refs(db: Session, pipeline_id: str, *, action: str) -> None:
    """同步任务把该流水线设为链式触发目标时拦截删除/归档。"""
    from app.data_channel.sync_tasks.models import DataSyncTask

    refs = db.query(DataSyncTask).filter(
        DataSyncTask.trigger_pipeline_id == pipeline_id).all()
    if refs:
        names = "、".join(t.name for t in refs[:3])
        raise HTTPException(
            400,
            f"流水线被 {len(refs)} 个同步任务设为链式触发目标（{names}{'…' if len(refs) > 3 else ''}），"
            f"不能{action}。请先在这些同步任务中解除「同步后触发流水线」的配置。")


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
    """创建新 Pipeline。支持旧 steps 格式和新 nodes/edges DSL。"""
    # 重名校验
    existing = db.query(Pipeline).filter(
        Pipeline.name == body.name,
        Pipeline.domain == body.domain,
    ).first()
    if existing:
        raise HTTPException(400, "已存在同名 Pipeline，请更换名称。")

    # 从 definition 推断 route，如无法推断默认为 'A'
    inferred_route = body.route
    if not inferred_route and body.definition:
        nodes = body.definition.get("nodes", [])
        types = {n.get("type") for n in nodes if n.get("type")}
        if "transform" in types:
            # 根据 Transform 节点配置推断
            pass  # 保留默认
        inferred_route = inferred_route or "A"
    pl = Pipeline(
        name=body.name,
        domain=body.domain or "通用",
        description=body.description or "",
        source_dataset_id=body.source_dataset_id,
        route=inferred_route or "A",  # SQLite 该列有 NOT NULL 约束
        spec=body.spec or {},
        definition=body.definition,
        status="draft",
        branch="main",
        version=1,
        created_by=current_user.id,
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)
    return _format_pipeline(pl)


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
    q = db.query(Pipeline)
    if search:
        q = q.filter(
            Pipeline.name.ilike(f"%{search}%") | Pipeline.id.ilike(f"%{search}%")
        )
    if domain:
        q = q.filter(Pipeline.domain == domain)
    if engine in {"n8n", "canvas"}:
        engine_value = Pipeline.definition["engine"].as_string()
        if engine == "n8n":
            q = q.filter(engine_value == "n8n")
        else:
            q = q.filter(or_(
                Pipeline.definition.is_(None),
                engine_value.is_(None),
                engine_value != "n8n",
            ))
    if enabled is not None:
        q = q.filter(Pipeline.enabled.is_(enabled))
    if status:
        q = q.filter(Pipeline.status == status)
    else:
        # 归档保留身份、发布快照与运行审计，但不再出现在日常工作列表；
        # 审计查询仍可显式传 status=archived 查看。
        q = q.filter(Pipeline.status != "archived")
    total = q.count()
    q = q.order_by(Pipeline.created_at.desc(), Pipeline.id.desc())
    if paginated:
        q = q.offset((page - 1) * page_size).limit(page_size)
    else:
        q = q.limit(100)
    pipeline_rows = q.all()
    pipeline_ids = [pl.id for pl in pipeline_rows]
    task_counts: dict[str, int] = {}
    if pipeline_ids:
        from app.data_channel.pipeline_tasks.models import PipelineTask

        task_counts = dict(
            db.query(PipelineTask.pipeline_id, func.count(PipelineTask.id))
            .filter(PipelineTask.pipeline_id.in_(pipeline_ids))
            .group_by(PipelineTask.pipeline_id)
            .all()
        )

    results = []
    for pl in pipeline_rows:
        d = _format_pipeline(pl)
        # 关联口径只看任务与流水线是否建立关系，不看任务是否启用。
        d["task_count"] = int(task_counts.get(pl.id, 0))
        # n8n 流水线：附加 n8n_workflow_id 供前端拼接跳转地址
        if _is_n8n_pipeline(pl):
            n8n_def = (pl.definition or {}).get("n8n") or {}
            steward_id = n8n_def.get("steward_id")
            if steward_id:
                n8n_rec = db.query(N8nPipeline).filter(N8nPipeline.id == steward_id).first()
                if n8n_rec:
                    d["definition"] = dict(d.get("definition") or {})
                    d["definition"]["n8n"] = {**n8n_def, "n8n_workflow_id": n8n_rec.n8n_workflow_id}
        # 添加最近运行信息
        last_run = db.query(PipelineRun).filter(
            PipelineRun.pipeline_id == pl.id
        ).order_by(PipelineRun.created_at.desc()).first()
        if last_run:
            d["last_run_status"] = last_run.status
            d["last_run_at"] = (
                last_run.started_at.isoformat() if last_run.started_at else None
            )
            d["last_run_error"] = last_run.error_log or ""
        results.append(d)
    if paginated:
        return {
            "items": results,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    return results


@router.get("/{pipeline_id}", response_model=PipelineResponse)
def get_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    return _format_pipeline(pl)


@router.put("/{pipeline_id}", response_model=PipelineResponse)
def update_pipeline(pipeline_id: str, body: PipelineUpdate, db: Session = Depends(get_db)):
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")

    update_data = body.model_dump(exclude_unset=True)

    # ── n8n 影子流水线：编排（definition 等）归数据管家对话；平台侧放行
    #    文案与字段契约（契约仍受下面的发布封版保护，已发布 → 不可改）──
    N8N_ALLOWED = {"name", "description", "domain", "column_definitions"}
    if _is_n8n_pipeline(pl):
        blocked = sorted(set(update_data) - N8N_ALLOWED)
        if blocked:
            raise HTTPException(
                400,
                f"该流水线由数据管家托管（n8n 引擎），字段 {', '.join(blocked)} "
                f"请在数据管家对话中修改。")

        # 字段定义校验发生在向导第 3 步，随后前端才 PUT 保存。只有 canonical
        # hash 完全相同时才保留凭证；任何实质变化都必须重新跑第 2/3 步。
        if "column_definitions" in update_data:
            from app.data_channel.steward import service as steward_service

            rec = steward_service.record_for_pipeline(db, pl)
            attestation = (
                steward_service.validation_attestation(rec) if rec is not None else None
            )
            definitions_changed = (
                attestation
                and attestation.get("column_definitions_hash")
                != _column_definitions_hash(update_data.get("column_definitions"))
            )
            if definitions_changed:
                steward_service.invalidate_validation_attestation(rec)

    # ── 已发布封版：编排与字段契约不可修改；基础展示信息随时可维护 ──
    # name/description 不参与执行快照，发布版本仍由 PipelineVersion 中的
    # definition/column_definitions 保持不可变。
    if (pl.status or "") == "published" and update_data:
        blocked = sorted(set(update_data) - {"name", "description"})
        if blocked:
            raise HTTPException(
                409,
                "流水线已发布，名称与描述仍可修改，但编排、字段契约及数据源配置已封版。"
                f"不可修改字段：{', '.join(blocked)}。",
            )

    if "column_definitions" in update_data:
        from app.data_channel.datasets.lake_gate import normalize_definitions
        update_data["column_definitions"] = normalize_definitions(
            update_data.get("column_definitions"))

    # ── 改名重名校验（与创建同一口径）──
    if "name" in update_data:
        new_name = (update_data.get("name") or "").strip()
        if not new_name:
            raise HTTPException(400, "流水线名称不能为空")
        dup = db.query(Pipeline).filter(
            Pipeline.name == new_name,
            Pipeline.domain == (update_data.get("domain") or pl.domain),
            Pipeline.id != pl.id,
        ).first()
        if dup:
            raise HTTPException(400, "已存在同名 Pipeline，请更换名称。")
        update_data["name"] = new_name

    for k, v in update_data.items():
        setattr(pl, k, v)
    pl.updated_at = datetime.now(timezone.utc)

    # n8n：名称/描述同步回数据管家治理记录，两边保持同一身份
    if _is_n8n_pipeline(pl) and ({"name", "description"} & set(update_data)):
        steward_id = ((pl.definition or {}).get("n8n") or {}).get("steward_id")
        rec = db.query(N8nPipeline).filter(
            N8nPipeline.id == steward_id).first() if steward_id else None
        if rec:
            if "name" in update_data:
                rec.name = pl.name
            if "description" in update_data:
                rec.description = pl.description or ""

    db.commit()
    db.refresh(pl)
    return _format_pipeline(pl)


@router.delete("/{pipeline_id}")
def delete_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")

    # ── n8n：删除语义是归档（严格停用远端，保留影子身份、发布版本与
    #    运行记录）。引用保护在 archive 内统一做。──
    if _is_n8n_pipeline(pl):
        from app.data_channel.steward import service as steward_service

        rec = steward_service.record_for_pipeline(db, pl)
        if rec is None:
            raise HTTPException(
                409,
                "n8n 流水线缺少数据管家治理记录；为避免破坏版本与运行审计链，归档已中止。",
            )
        try:
            client = steward_service.get_n8n_client(db)
            steward_service.archive(db, rec, client)
        except steward_service.StewardError as e:
            raise HTTPException(400, str(e))
        return {"status": "archived", "id": pipeline_id}

    # 引用保护：被调度任务引用的流水线不可删——删了任务会静默失效
    refs = _pipeline_task_refs(db, pipeline_id)
    if refs:
        names = "、".join(t.name for t in refs[:3])
        raise HTTPException(
            400,
            f"流水线已被 {len(refs)} 个调度任务引用（{names}{'…' if len(refs) > 3 else ''}），"
            f"请先在数据任务池删除或改绑这些任务。")
    _reject_if_sync_chain_refs(db, pipeline_id, action="删除")
    # 级联删除 runs + versions
    db.query(PipelineRun).filter(PipelineRun.pipeline_id == pipeline_id).delete()
    db.query(PipelineVersion).filter(PipelineVersion.pipeline_id == pipeline_id).delete()
    db.delete(pl)
    db.commit()
    return {"status": "deleted", "id": pipeline_id}


# ── Validate ──────────────────────────────────────────────────────

@router.post("/{pipeline_id}/validate", response_model=ValidateResult)
def validate_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    """校验 Pipeline definition 是否合法——包含结构性检查和语义性检查。"""
    from app.models.v2.dataset import Dataset, DatasetVersion

    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")

    errors = []
    warnings = []
    definition = pl.definition

    # ── n8n 引擎（数据管家托管）：校验绑定与平台调度入口。
    #    validate 本来就是发布前置步骤，因此不可调度的 Schedule/Manual-only
    #    工作流应在这里直接给出 error，而不是等发布远端动作阶段才失败。──
    if _is_n8n_pipeline(pl):
        from app.data_channel.steward.service import (
            StewardError,
            record_for_pipeline,
            validate_managed_workflow_contract,
        )

        rec = record_for_pipeline(db, pl)
        if rec is None:
            errors.append({"node_id": "", "severity": "error",
                           "message": "缺少数据管家治理记录，无法运行。请删除后在数据管家重新新建该流水线。"})
        else:
            try:
                validate_managed_workflow_contract(rec.workflow_snapshot)
            except StewardError as exc:
                errors.append({
                    "node_id": "",
                    "severity": "error",
                    "message": str(exc),
                })
        return ValidateResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    # ── 旧格式兼容 ────────────────────────────────────────
    if not definition:
        if not pl.source_dataset_id:
            errors.append({
                "node_id": "", "severity": "error",
                "message": "Pipeline 未绑定源数据集(source_dataset_id)，无法执行。请先创建同步任务将数据导入数据集。",
            })
        else:
            ds = db.query(Dataset).filter(Dataset.id == pl.source_dataset_id).first()
            if not ds:
                errors.append({
                    "node_id": "", "severity": "error",
                    "message": f"绑定的源数据集({pl.source_dataset_id})不存在，可能已被删除。",
                })
            else:
                ver = db.query(DatasetVersion).filter(
                    DatasetVersion.dataset_id == ds.id
                ).order_by(DatasetVersion.version_no.desc()).first()
                if not ver or (ver.rowcount or 0) == 0:
                    warnings.append({
                        "node_id": "", "severity": "warning",
                        "message": f"源数据集「{ds.name}」暂无数据版本，请先执行同步任务拉取数据。",
                    })
        return ValidateResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    # ── 新 DSL 格式 ────────────────────────────────────────
    nodes = definition.get("nodes", [])
    edges = definition.get("edges", [])

    if not nodes:
        errors.append({"node_id": "", "severity": "error", "message": "Pipeline 至少需要一个节点。"})

    node_ids = set()
    node_types = {}
    node_labels: dict[str, str] = {}
    connector_configs: dict[str, dict] = {}
    storage_configs: dict[str, dict] = {}

    for n in nodes:
        nid = n.get("id", "")
        node_ids.add(nid)
        ntype = n.get("type", "")
        node_types[nid] = ntype
        node_labels[nid] = n.get("label") or n.get("data", {}).get("label") or nid
        if ntype == "connector":
            connector_configs[nid] = n.get("config") or {}
        elif ntype == "storage":
            storage_configs[nid] = n.get("config") or {}

    # The runtime supports one linear branch per connector.  Compile here as a
    # publish/run guard so an unsupported graph can never be flattened into a
    # different execution plan.
    try:
        from app.data_channel.pipelines.dag_compiler import compile_definition
        compile_definition(definition)
    except Exception as exc:
        compile_errors = getattr(exc, "errors", None) or [str(exc)]
        for message in compile_errors:
            errors.append({"node_id": "", "severity": "error", "message": message})

    # 检查连接规则
    for edge in edges:
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        if src not in node_ids:
            errors.append({"node_id": src, "severity": "error", "message": f"边引用了不存在的源节点: {src}"})
        if tgt not in node_ids:
            errors.append({"node_id": tgt, "severity": "error", "message": f"边引用了不存在的目标节点: {tgt}"})

        src_type = node_types.get(src, "")
        tgt_type = node_types.get(tgt, "")

        if src_type == "connector" and tgt_type == "transform":
            errors.append({
                "node_id": edge.get("id", ""), "severity": "error",
                "message": "Connector 不能直接连接 Transform，需要经过 Storage。",
            })
        if src_type == "connector" and tgt_type == "output":
            errors.append({
                "node_id": edge.get("id", ""), "severity": "error",
                "message": "Connector 不能直接连接 Output。",
            })
        if src_type == "output":
            errors.append({
                "node_id": edge.get("id", ""), "severity": "error",
                "message": "Output 节点不能作为边的起点。",
            })

    has_connector = any(t == "connector" for t in node_types.values())
    has_storage = any(t == "storage" for t in node_types.values())
    has_output = any(t == "output" for t in node_types.values())

    # ── 语义检查：数据可用性 ──────────────────────────────
    if has_connector:
        all_connectors_empty = True
        for nid, cfg in connector_configs.items():
            files = cfg.get("files", []) or []
            has_any_file = False
            if files:
                for fi in files:
                    ds_id = fi.get("dataset_id")
                    ds = db.query(Dataset).filter(Dataset.id == ds_id).first() if ds_id else None
                    if ds:
                        has_any_file = True
                        ver = db.query(DatasetVersion).filter(
                            DatasetVersion.dataset_id == ds.id
                        ).order_by(DatasetVersion.version_no.desc()).first()
                        if ver and (ver.rowcount or 0) > 0:
                            all_connectors_empty = False  # at least one connector has data
                        else:
                            warnings.append({
                                "node_id": nid, "severity": "warning",
                                "message": f"Connector「{node_labels.get(nid, nid)}」引用的数据集「{ds.name}」暂无数据，请先执行同步。",
                            })
                    else:
                        if ds_id:
                            errors.append({
                                "node_id": nid, "severity": "error",
                                "message": f"Connector「{node_labels.get(nid, nid)}」引用的数据集({ds_id})不存在。",
                            })
            if not has_any_file:
                warnings.append({
                    "node_id": nid, "severity": "warning",
                    "message": f"Connector「{node_labels.get(nid, nid)}」未配置文件连接，请点击节点添加数据文件。",
                })

        if all_connectors_empty:
            # 如果 connector 节点都没有文件，检查是否有 source_dataset_id 作为后备
            if pl.source_dataset_id:
                ds = db.query(Dataset).filter(Dataset.id == pl.source_dataset_id).first()
                if ds:
                    ver = db.query(DatasetVersion).filter(
                        DatasetVersion.dataset_id == ds.id
                    ).order_by(DatasetVersion.version_no.desc()).first()
                    if ver and (ver.rowcount or 0) > 0:
                        all_connectors_empty = False  # 有后备数据源
                        warnings.append({
                            "node_id": "", "severity": "warning",
                            "message": "Connector 节点未配置文件连接，但 Pipeline 已绑定源数据集，将使用该数据集作为输入。",
                        })
            if all_connectors_empty:
                errors.append({
                    "node_id": "", "severity": "error",
                    "message": "所有 Connector 节点均未配置数据文件，且未绑定源数据集。请点击节点添加文件连接，或先创建同步任务。",
                })

    # 如果没有 connector 但有 storage，检查 source_dataset_id
    if not has_connector and pl.source_dataset_id:
        ds = db.query(Dataset).filter(Dataset.id == pl.source_dataset_id).first()
        if not ds:
            errors.append({
                "node_id": "", "severity": "error",
                "message": f"绑定的源数据集({pl.source_dataset_id})不存在。",
            })
        else:
            ver = db.query(DatasetVersion).filter(
                DatasetVersion.dataset_id == ds.id
            ).order_by(DatasetVersion.version_no.desc()).first()
            if not ver or (ver.rowcount or 0) == 0:
                warnings.append({
                    "node_id": "", "severity": "warning",
                    "message": f"源数据集「{ds.name}」暂无数据版本。",
                })

    if not has_connector and not pl.source_dataset_id:
        errors.append({
            "node_id": "", "severity": "error",
            "message": "Pipeline 未绑定任何数据源。请通过「同步任务」将数据导入数据集，再将 Pipeline 绑定到该数据集。",
        })

    if has_connector and not has_output:
        errors.append({
            "node_id": "", "severity": "error",
            "message": "存在 Connector 但没有 Output 节点，禁止发布或运行。",
        })

    return ValidateResult(valid=len(errors) == 0, errors=errors, warnings=warnings)


# ── Validate Definitions ──────────────────────────────────────────

class ValidateDefinitionsBody(BaseModel):
    column_definitions: list  # [{source_key, field_key, field_name, field_type, is_primary_key, nullable}]


class ValidateDefinitionsError(BaseModel):
    field_key: str
    message: str
    severity: str  # "error" | "warning"


class ValidateDefinitionsResult(BaseModel):
    valid: bool  # 无 error 级问题（warning 不阻断）
    errors: list[dict] = []  # [{field_key, message, severity}]


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
    import json as _json
    from app.data_channel.datasets.lake_gate import (
        FIELD_KEY_RE, _cell_type_ok, normalize_definitions, split_pk,
        validate_contract_structure)
    from app.models.v2.dataset import Dataset

    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")

    try:
        from app.services.storage_service import get_storage_service
        raw = get_storage_service().get_object(_dry_run_uri(pipeline_id, dry_run_id))
        payload = _json.loads(raw.decode("utf-8"))
    except ValueError:
        raise HTTPException(400, "非法的 dry_run_id")
    except Exception:
        raise HTTPException(404, "试运行结果不存在或已过期，请回到「执行预览」重新执行流水线")
    if payload.get("pipeline_id") != pipeline_id:
        raise HTTPException(400, "试运行结果与流水线不匹配")
    stored_dry_run_id = payload.get("dry_run_id")
    if (stored_dry_run_id not in (None, dry_run_id)
            or (_is_n8n_pipeline(pl) and stored_dry_run_id != dry_run_id)):
        raise HTTPException(400, "试运行结果 id 与暂存内容不匹配，平台已拒绝使用该结果")

    outputs = payload.get("outputs") or []
    from app.data_channel.steward import service as steward_service
    output_checksum = str(payload.get("output_checksum") or "")
    computed_output_checksum = steward_service.canonical_json_hash(outputs)
    checksum_invalid = bool(output_checksum) and output_checksum != computed_output_checksum
    checksum_required_but_missing = _is_n8n_pipeline(pl) and not output_checksum
    if checksum_invalid or checksum_required_but_missing:
        if _is_n8n_pipeline(pl):
            rec_for_invalidation = steward_service.record_for_pipeline(db, pl)
            if rec_for_invalidation is not None:
                steward_service.invalidate_validation_attestation(rec_for_invalidation)
                db.commit()
        raise HTTPException(
            400,
            "试运行输出校验和缺失或不匹配，暂存内容不能作为发布依据。请重新执行预览。",
        )
    if len(outputs) > 1:
        raise HTTPException(
            400, "该流水线单次执行产出多个数据集，流水线级字段契约暂不适用（主键请在任务/资产湖粒度管理）。")
    rows: list[dict] = [r for r in ((outputs[0].get("rows") or []) if outputs else [])
                        if isinstance(r, dict)]

    actual_columns: set[str] = set()
    for row in rows:
        actual_columns.update(str(k) for k in row.keys())

    errors: list[dict] = []
    defs = normalize_definitions(body.column_definitions)
    if _is_n8n_pipeline(pl) and not defs:
        errors.append({
            "field_key": "",
            "severity": "error",
            "message": "n8n 流水线发布前必须定义至少一个输出字段，并完成主键/类型校验",
        })
    if payload.get("truncated"):
        errors.append({"field_key": "", "severity": "error",
                       "message": f"试运行输出超过暂存上限，本次仅有 {len(rows):,} 行，无法完成全量发布校验"})

    for message in validate_contract_structure(body.column_definitions):
        errors.append({"field_key": "", "severity": "error", "message": message})

    # ── 1. 结构校验：字段标识命名合法且唯一；原始列存在 ──
    seen_fk: set[str] = set()
    for d in defs:
        fk = d["field_key"]
        if not FIELD_KEY_RE.match(fk):
            errors.append({"field_key": fk, "severity": "error",
                           "message": f"字段标识「{fk}」不合法：须以字母或下划线开头，仅含字母/数字/下划线（入湖列名约束）"})
        if fk in seen_fk:
            errors.append({"field_key": fk, "severity": "error",
                           "message": f"字段标识「{fk}」重复：多个列映射到了同一个入湖列名"})
        seen_fk.add(fk)
        if d["source_key"] not in actual_columns:
            errors.append({"field_key": fk, "severity": "error",
                           "message": f"原始列「{d['source_key']}」在流水线输出中不存在"})

    # ── 2. 类型匹配（全量，契约不符阻断发布）──
    for d in defs:
        expected = d["field_type"]
        sk = d["source_key"]
        if sk not in actual_columns:
            continue
        invalid = [i + 1 for i, row in enumerate(rows)
                   if not _cell_type_ok(row.get(sk), expected)]
        if invalid:
            errors.append({"field_key": d["field_key"], "severity": "error",
                           "message": f"字段类型声明为「{expected}」，但第 {invalid[:5]} 行无法按该类型解析"})

    # ── 3. 主键：全量非空 + 组合唯一 ──
    pk_defs = [d for d in defs if d["is_primary_key"] and d["source_key"] in actual_columns]
    if pk_defs and rows:
        pk_display = "、".join(d["field_key"] for d in pk_defs)
        seen_pk: dict[tuple, int] = {}
        for i, row in enumerate(rows):
            values: list[str] = []
            empty_col = None
            for d in pk_defs:
                v = row.get(d["source_key"])
                if v is None or str(v).strip() == "":
                    empty_col = d["field_key"]
                    break
                values.append(str(v).strip())
            if empty_col is not None:
                errors.append({"field_key": empty_col, "severity": "error",
                               "message": f"主键列「{empty_col}」第 {i + 1} 行为空：主键值必须全量非空"})
                break
            key = tuple(values)
            if key in seen_pk:
                errors.append({"field_key": pk_display, "severity": "error",
                               "message": f"主键组合「{pk_display}」第 {seen_pk[key] + 1} 行与第 {i + 1} 行重复（全量校验，共 {len(rows)} 行）"})
                break
            seen_pk[key] = i

    # ── 4. 非主键列的空值约束（全量）──
    for d in defs:
        sk = d["source_key"]
        if d["nullable"] or d["is_primary_key"] or sk not in actual_columns:
            continue
        null_count = sum(1 for row in rows
                         if row.get(sk) is None or str(row.get(sk) or "").strip() == "")
        if null_count > 0:
            errors.append({"field_key": d["field_key"], "severity": "error",
                           "message": f"列「{d['field_key']}」不允许为空，但全量数据中存在 {null_count} 个空值"})

    # ── 5. 与湖中已固化主键的差异（提示不阻断：全量覆盖运行会按新契约
    #      重写湖中声明，这是变更主键的受控通道；增量/合并入库在对齐前会失败）──
    pk = ",".join(d["field_key"] for d in defs if d["is_primary_key"])
    if pk:
        for cid in (pl.target_curated_ids or []):
            ds = db.query(Dataset).filter(Dataset.id == cid).first()
            declared = ((ds.schema_json or {}).get("primary_key") or "") if ds else ""
            if declared and split_pk(declared) != split_pk(pk):
                errors.append({"field_key": pk, "severity": "warning",
                               "message": f"主键与资产湖数据集「{ds.name}」已固化的主键（{declared}）不一致："
                                          f"下次「全量覆盖」运行将重写湖中声明并重建实例身份；"
                                          f"增量/合并（append/upsert）入库在对齐前会硬失败"})

    # n8n 发布凭证 = canonical 字段契约 + 本次完整输出 + 试跑时及当前 live
    # workflow 身份。字段校验通过并不意味着可以拿旧输出发布新 revision。
    rec = None
    live_evidence = None
    if _is_n8n_pipeline(pl):
        rec = steward_service.record_for_pipeline(db, pl)
        if rec is None:
            errors.append({
                "field_key": "",
                "severity": "error",
                "message": "平台内部治理记录不完整，无法安全发布",
            })
        else:
            engine_meta = payload.get("engine_meta") or {}
            dry_run_evidence = engine_meta.get("workflow_evidence") or {}
            try:
                client = steward_service.get_n8n_client(db)
                live_workflow = client.get_workflow(rec.n8n_workflow_id)
                # 兼容旧版/部分 n8n API：预览阶段即使没有 activeVersionId，
                # runner 仍会保存执行前后核对过的 snapshot hash。平台可结合
                # 当前 live revision 自动补全内部证据，无需用户理解或重跑“凭证”。
                if (not dry_run_evidence
                        and engine_meta.get("workflow_snapshot_hash")):
                    from app.settings.workflows.n8n_client import N8nClient
                    dry_run_evidence = {
                        "revision": N8nClient.workflow_revision(live_workflow),
                        "snapshot_hash": engine_meta["workflow_snapshot_hash"],
                    }
                live_evidence = steward_service.require_workflow_validation_evidence(
                    dry_run_evidence,
                    live_workflow,
                    context="字段定义校验时",
                )
            except steward_service.StewardError as exc:
                errors.append({
                    "field_key": "",
                    "severity": "error",
                    "message": str(exc),
                })
            except Exception as exc:  # noqa: BLE001
                errors.append({
                    "field_key": "",
                    "severity": "error",
                    "message": f"无法读取当前 n8n workflow，平台内部一致性检查失败：{exc}",
                })

    has_blocking = any(e["severity"] == "error" for e in errors)
    if rec is not None:
        state = dict(rec.last_test_result or {})
        if has_blocking or live_evidence is None:
            state.pop("validation_attestation", None)
        else:
            state["validation_attestation"] = {
                "version": 1,
                "column_definitions_hash": _column_definitions_hash(
                    body.column_definitions),
                "workflow_revision": live_evidence["revision"],
                "workflow_snapshot_hash": live_evidence["snapshot_hash"],
                "dry_run_id": dry_run_id,
                "output_checksum": output_checksum,
                "dry_run_created_at": payload.get("created_at"),
                "validated_at": datetime.now(timezone.utc).isoformat(),
            }
        rec.last_test_result = state
        db.commit()
    return ValidateDefinitionsResult(valid=not has_blocking, errors=errors)


# ── Publish / Unpublish ───────────────────────────────────────────

class PublishBody(BaseModel):
    enable: bool = False  # 发布并同时启用（向导「发布并启用」勾选）


@router.post("/{pipeline_id}/publish")
def publish_pipeline(pipeline_id: str, body: PublishBody | None = None,
                     db: Session = Depends(get_db),
                     current_user=Depends(get_current_user)):
    """发布 Pipeline：永久封版身份、编排与字段契约，之后不可修改或撤回。

    发布是所有引擎共同的生命周期唯一入口（画布与 n8n 一致）。n8n 引擎
    发布时附加：校验触发器 → 激活 n8n workflow → 固化 webhook/期望列。
    发布前校验：definition 结构 + 契约结构（字段标识命名/唯一）。契约与
    数据的一致性校验（全量非空/唯一/类型）在向导第 3 步完成——发布不重
    跑流水线（n8n 试运行会真实触发生产 workflow）。契约主键与湖中已固化
    主键不一致不拦发布：「全量覆盖」运行会按新契约重写湖中声明（变更主
    键的受控通道），增量/合并运行则会在准入闸门硬失败——差异以 warnings
    随响应返回。
    """
    from app.data_channel.datasets.lake_gate import (
        contract_pk, split_pk, validate_contract_structure)
    from app.models.v2.dataset import Dataset

    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    if (pl.status or "") == "published":
        raise HTTPException(400, "流水线已是已发布状态。")

    # 结构校验
    validation = validate_pipeline(pipeline_id, db)
    if not validation.valid:
        raise HTTPException(400, f"Pipeline 校验失败，无法发布: {validation.errors}")

    # 契约结构校验：命名合法 + 唯一（画布与 n8n 同一套）
    structure_errors = validate_contract_structure(pl.column_definitions)
    if structure_errors:
        raise HTTPException(
            400, f"字段契约结构非法：{'；'.join(structure_errors)}。请回到「设置主键组」修正。")

    # ── n8n：发布 = 激活 workflow + 固化 definition（webhook/期望列/revision）。
    #    激活是远端副作用，后续本地事务若失败必须补偿停用。──
    desired_enabled = bool(body and body.enable)
    n8n_activation = None
    if _is_n8n_pipeline(pl):
        from app.data_channel.steward import service as steward_service

        rec = steward_service.record_for_pipeline(db, pl)
        if rec is None:
            raise HTTPException(400, "缺少数据管家治理记录，无法发布。请删除后在数据管家重新新建该流水线。")
        attestation = steward_service.validation_attestation(rec)
        required_attestation_fields = {
            "column_definitions_hash",
            "workflow_revision",
            "workflow_snapshot_hash",
            "dry_run_id",
            "output_checksum",
        }
        if not attestation or any(
            not attestation.get(field) for field in required_attestation_fields
        ):
            evidence_error = str(
                (rec.last_test_result or {}).get("publish_evidence_error") or ""
            ).strip()
            evidence_detail = (
                f"内部一致性检查详情：{evidence_error.rstrip('。.!！')}"
                if evidence_error
                else "内部一致性检查尚未完成"
            )
            raise HTTPException(
                400,
                "平台尚未完成最近一次执行预览与字段定义的一致性确认，暂时无法安全发布。"
                f"{evidence_detail}。",
            )
        if attestation["column_definitions_hash"] != _column_definitions_hash(
                pl.column_definitions):
            steward_service.invalidate_validation_attestation(rec)
            db.commit()
            raise HTTPException(
                400,
                "字段定义在最近一次校验后发生变化，平台已自动使旧校验结果失效。"
                "请重新校验字段定义。",
            )
        try:
            client = steward_service.get_n8n_client(db)
            revision = steward_service.activate_for_publish(
                db,
                rec,
                client,
                keep_active=desired_enabled,
                validation_attestation=attestation,
            )
        except steward_service.ValidationAttestationError as e:
            steward_service.invalidate_validation_attestation(rec)
            db.commit()
            raise HTTPException(400, str(e))
        except steward_service.StewardError as e:
            raise HTTPException(400, str(e))
        n8n_activation = (rec, client)

    try:
        if n8n_activation is not None:
            rec, _client = n8n_activation
            # revision 来自激活后再次读取的远端真身，和本地状态/版本快照同事务固化。
            steward_service.ensure_shadow_pipeline(
                db, rec, published_revision=revision)
            release = ((pl.definition or {}).get("n8n") or {})
            contract = release.get("managed_contract") or {}
            frozen_revision = release.get("revision") or {}
            if not contract.get("output_node_name") or not contract.get("webhook_path"):
                raise HTTPException(500, "发布事务未形成完整 n8n 输入/输出契约，发布已中止。")
            if not all(frozen_revision.get(field) for field in ("versionId", "updatedAt")):
                raise HTTPException(500, "平台未能确认发布版本，发布已安全中止。")

        # 契约主键 vs 湖中已固化主键：不一致 → 警告随响应返回（不再拦截）
        warnings: list[str] = []
        pk = contract_pk(pl.column_definitions)
        if pk:
            for cid in (pl.target_curated_ids or []):
                ds = db.query(Dataset).filter(Dataset.id == cid).first()
                declared = ((ds.schema_json or {}).get("primary_key") or "") if ds else ""
                if declared and split_pk(declared) != split_pk(pk):
                    warnings.append(
                        f"契约主键（{pk}）与资产湖数据集「{ds.name}」已固化的主键（{declared}）不一致："
                        f"下次「全量覆盖」运行将重写湖中声明并重建实例身份；增量/合并入库在对齐前会失败。")

        # 版本号语义 = 已发布快照序号：首次发布保持当前号，再发布才递增
        has_prior_published = db.query(PipelineVersion).filter(
            PipelineVersion.pipeline_id == pipeline_id,
            PipelineVersion.status == "published").count() > 0
        pl.status = "published"
        pl.version = (pl.version or 1) + (1 if has_prior_published else 0)
        # 发布与启用是两个不同状态：未勾选「发布并启用」时，本地 disabled，
        # n8n 也必须最终 inactive；不能沿用发布前或历史值。
        pl.enabled = desired_enabled
        pl.updated_at = datetime.now(timezone.utc)
        # 版本快照与状态翻转同一事务提交——分两段 commit 会在中断时留下
        # 「已发布却无快照」的孤儿状态（契约是封版核心工件，必须可回溯）
        db.add(PipelineVersion(
            pipeline_id=pipeline_id,
            version=pl.version,
            definition=pl.definition,
            column_definitions=pl.column_definitions,
            status="published",
            created_by=current_user.id,
        ))
        db.commit()
    except Exception as exc:  # noqa: BLE001 — 本地事务失败必须撤销远端激活
        db.rollback()
        if n8n_activation is not None:
            rec, client = n8n_activation
            try:
                steward_service.compensate_failed_publish(rec, client)
            except steward_service.StewardError as compensation_error:
                raise HTTPException(500, str(compensation_error)) from exc
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(500, f"平台发布事务失败，n8n 已恢复为发布前的停用状态：{exc}") from exc

    return {
        "id": pl.id,
        "status": pl.status,
        "version": pl.version,
        "enabled": True if pl.enabled is None else bool(pl.enabled),
        "warnings": warnings or None,
    }


@router.post("/{pipeline_id}/unpublish")
def unpublish_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    """兼容旧客户端的明确拒绝端点：发布是不可逆版本边界。"""
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    if (pl.status or "") != "published":
        raise HTTPException(409, "流水线尚未发布，不存在撤回操作。")
    raise HTTPException(
        409,
        "已发布流水线是不可变版本，不支持撤回或重新编辑。"
        "如需变更，请新建流水线；旧版本可先停用，确认替代版本稳定后归档。",
    )


# ── Versions ──────────────────────────────────────────────────────

@router.get("/{pipeline_id}/versions")
def list_versions(pipeline_id: str, db: Session = Depends(get_db)):
    """查看版本历史。"""
    versions = db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_id == pipeline_id
    ).order_by(PipelineVersion.version.desc()).all()
    return [
        {
            "id": v.id,
            "version": v.version,
            "status": v.status,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in versions
    ]


# ── Run (保留原有) ────────────────────────────────────────────────

@router.post("/{pipeline_id}/run")
def run_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    _require_production_executable(pl)

    run = PipelineRun(pipeline_id=pipeline_id, status="pending", started_at=datetime.now(timezone.utc))
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        _ensure_broker_reachable()
        from app.tasks.v2.pipeline_run import pipeline_run_task
        pipeline_run_task.delay(pipeline_id, run.id)
    except Exception as e:
        # Celery/Redis 不可用时立即标记失败，避免 run 永远停在 pending。
        # 运行失败只写 run，不动 pipeline.status（生命周期与运行态分离）
        run.status = "failed"
        run.error_log = f"任务派发失败 (Celery/Redis 不可用?): {e}"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return {"run_id": run.id, "status": "failed", "error": run.error_log}

    return {"run_id": run.id, "status": "pending"}


def _ensure_broker_reachable(timeout: float = 2.0):
    """快速预检 Celery broker 可达性 — kombu 自身的连接重试会阻塞请求数十秒"""
    import socket
    from urllib.parse import urlparse
    from app.config import settings
    u = urlparse(settings.redis_url)
    sock = socket.create_connection((u.hostname or "localhost", u.port or 6379), timeout=timeout)
    sock.close()


@router.get("/{pipeline_id}/runs")
def list_runs(pipeline_id: str, db: Session = Depends(get_db)):
    runs = db.query(PipelineRun).filter(PipelineRun.pipeline_id == pipeline_id).order_by(PipelineRun.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "status": r.status,
            "stats": r.stats,
            "error_log": r.error_log or "",
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in runs
    ]


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Run not found")
    return {
        "id": run.id,
        "status": run.status,
        "stats": run.stats,
        "error_log": run.error_log,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.post("/{pipeline_id}/run-sync")
def run_pipeline_sync(pipeline_id: str, db: Session = Depends(get_db)):
    """同步执行 Pipeline（无需 Celery/Redis，适用于开发/测试）。

    运行成败只落在 PipelineRun 上，不改 pipeline.status——发布是显式动作。
    """
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    _require_production_executable(pl)

    run = PipelineRun(pipeline_id=pipeline_id, status="pending", started_at=datetime.now(timezone.utc))
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        from app.tasks.v2.pipeline_run import pipeline_run_task
        pipeline_run_task(pipeline_id, run.id)
        db.refresh(run)
        return {"run_id": run.id, "status": run.status, "stats": run.stats, "error": run.error_log}
    except Exception as e:
        run.status = "failed"
        run.error_log = str(e)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return {"run_id": run.id, "status": "failed", "error": str(e)}


# ── 启用开关 + 试运行预览（不落湖；正式入湖仅由数据任务池触发）────────

class EnabledBody(BaseModel):
    enabled: bool


@router.patch("/{pipeline_id}/enabled")
def set_pipeline_enabled(pipeline_id: str, body: EnabledBody, db: Session = Depends(get_db)):
    """启用/停用流水线：停用后任务池调度与同步链式触发都不执行。

    只有已发布的流水线才能启用（画布与 n8n 同一规则）。只要已被任务池
    关联，就锁定启用状态；任务自身是否启用不影响该保护。
    """
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    if body.enabled and (pl.status or "") != "published":
        raise HTTPException(400, "只有已发布的流水线才能启用。请先在编辑向导中完成发布。")

    current_enabled = True if pl.enabled is None else bool(pl.enabled)
    if bool(body.enabled) == current_enabled:
        return _format_pipeline(pl)

    refs = _pipeline_task_refs(db, pipeline_id)
    if refs:
        names = "、".join(t.name for t in refs[:3])
        suffix = "…" if len(refs) > 3 else ""
        raise HTTPException(
            409,
            f"流水线「{pl.name}」已被 {len(refs)} 个数据任务关联（{names}{suffix}），"
            "为避免影响任务调度，不允许更改启用状态。"
            "请先在数据任务池删除或改绑这些任务，解除关联后再操作。",
        )

    n8n_transition = None
    if _is_n8n_pipeline(pl):
        from app.data_channel.steward import service as steward_service

        rec = steward_service.record_for_pipeline(db, pl)
        if rec is None:
            raise HTTPException(409, "n8n 流水线缺少数据管家治理记录，无法安全启停。")
        try:
            n8n_client = steward_service.get_n8n_client(db)
            previous_remote_active = steward_service.set_published_enabled(
                pl, rec, n8n_client, enabled=bool(body.enabled))
        except steward_service.StewardError as exc:
            raise HTTPException(400, str(exc))
        n8n_transition = (rec, n8n_client, previous_remote_active)

    pl.enabled = bool(body.enabled)
    pl.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 -- compensate the external side effect
        db.rollback()
        if n8n_transition is not None:
            rec, n8n_client, previous_remote_active = n8n_transition
            try:
                steward_service.restore_remote_active(
                    pl,
                    rec,
                    n8n_client,
                    enabled=previous_remote_active,
                    context="启停事务补偿",
                )
            except steward_service.StewardError as compensation_exc:
                raise HTTPException(
                    500,
                    f"平台启停事务失败（{exc}），且恢复 n8n 原状态失败（{compensation_exc}）。"
                    "请立即人工核对。",
                ) from exc
        raise HTTPException(500, f"平台启停事务失败，n8n 原状态已恢复：{exc}") from exc
    db.refresh(pl)
    return _format_pipeline(pl)


_DRY_RUN_BUCKET = "raw-datasets"  # 复用既有桶（本地回退同样生效），免新增配置


def _dry_run_uri(pipeline_id: str, dry_run_id: str) -> str:
    """服务端确定性重建暂存 URI——不信任客户端回传，防任意对象读取。"""
    import uuid as _uuid
    _uuid.UUID(dry_run_id)  # 非法 id 直接 ValueError → 400
    return f"s3://{_DRY_RUN_BUCKET}/dry-runs/{pipeline_id}/{dry_run_id}.json"


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
    import json as _json
    import uuid as _uuid
    from types import SimpleNamespace
    from datetime import datetime as _dt, timezone as _tz

    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")

    engine_meta: dict = {}
    try:
        if _is_n8n_pipeline(pl):
            from app.data_channel.steward.runner import (
                collect_n8n_rows, collect_test_rows, persist_test_result)
            from app.data_channel.steward.service import record_for_pipeline
            from app.tasks.v2.pipeline_run import _strip_content
            rec = record_for_pipeline(db, pl)
            if rec is not None and (pl.status or "") != "published":
                # 未发布的 workflow 未激活、生产 webhook 未注册——走数据管家的
                # 试跑通道（临时激活→触发→恢复），否则向导第 2 步必失败，
                # 未发布 n8n 永远设不了字段契约（先设契约、后发布的流程死锁）
                # 预览与发布凭证解耦：远端 n8n 版本若不返回 activeVersionId，
                # 已成功产生的输出仍应展示；缺失的发布证据只在第 3 步校验时阻断发布。
                rows, engine_meta = collect_test_rows(
                    db, rec, require_publish_evidence=False)
                if engine_meta.get("error"):
                    raise RuntimeError(f"n8n 执行失败：{engine_meta['error']}")
                persist_test_result(db, rec, rows, engine_meta)
            else:
                # 已发布（或治理记录缺失时让 collect_n8n_rows 给出准确报错）
                rows, engine_meta = collect_n8n_rows(db, pl)
            outputs = [{
                "source": {"dataset_id": None, "filename": pl.name, "route": "A", "kind": "n8n"},
                "table_name": None, "rows": _strip_content(rows),
                "rows_in": len(rows), "rows_out": len(rows),
                "route": "A", "meta": {}, "multi_source": False,
            }]
        else:
            from app.tasks.v2.pipeline_run import collect_pipeline_output
            outputs = collect_pipeline_output(db, pl)
    except Exception as e:  # noqa: BLE001 — 试运行失败原样透出给弹窗
        raise HTTPException(400, f"试运行失败：{e}")

    # 资产湖准入闸门预检：按将要写入的目标数据集逐个检查（不落任何数据）。
    # 单产物流水线同时应用字段契约——预览看到的列名/校验与正式入湖完全一致。
    # 目标数据集与入湖同一套解析（id 绑定优先）：流水线改名后预检的仍是
    # 原产物资产，而不是名字派生出来的新空壳
    from app.data_channel.datasets.lake_gate import LakeGateError, gate_rows
    from app.tasks.v2.pipeline_run import resolve_curated_target

    contract_defs = pl.column_definitions if len(outputs) == 1 else None
    preview = []
    for o in outputs:
        curated, derived_name = resolve_curated_target(
            db, pl, o["source"], o["multi_source"], o["table_name"])
        ds_name = curated.name if curated is not None else derived_name
        gate_error = None
        gate_info: dict = {"pk": "", "pk_source": "", "warnings": [], "drift": None}
        preview_rows = o["rows"]
        try:
            g = gate_rows(curated or SimpleNamespace(name=ds_name, schema_json=None),
                          o["rows"], None, column_definitions=contract_defs)
            preview_rows = g["rows"]
            gate_info = {"pk": g["pk"], "pk_source": g["pk_source"],
                         "warnings": g["warnings"], "drift": g["drift"]}
        except LakeGateError as e:
            gate_error = str(e)
        if contract_defs is None and (pl.column_definitions or []):
            gate_info["warnings"] = [*gate_info["warnings"],
                                     "多产物流水线暂不应用流水线级字段契约（契约粒度=单产物）"]

        columns: list[str] = []
        for row in preview_rows[:50]:
            for k in row.keys():
                if k not in columns:
                    columns.append(k)
        preview.append({
            "dataset_name": ds_name,
            "dataset_exists": curated is not None,
            "rows_out": o["rows_out"],
            "columns": columns,
            "sample": preview_rows[:max_rows],
            "gate_error": gate_error,
            **gate_info,
        })

    # ── 暂存上限：全量输出整包进对象存储、校验时整包回读，行数不设防会把
    # 内存与存储一起拖垮。超限时截断暂存并在预览/校验里明说（不静默）。──
    _MAX_STAGE_ROWS = 100_000
    budget = _MAX_STAGE_ROWS
    staged_outputs: list[dict] = []
    truncated = False
    for i, o in enumerate(outputs):
        rows_o = o.get("rows") or []
        keep = rows_o if len(rows_o) <= budget else rows_o[:budget]
        if len(keep) < len(rows_o):
            truncated = True
            if i < len(preview):
                preview[i]["warnings"] = [*preview[i].get("warnings", []),
                                          f"输出 {len(rows_o):,} 行超出暂存上限 {_MAX_STAGE_ROWS:,} 行："
                                          f"「展开查看全部」与第 3 步全量校验仅覆盖前 {len(keep):,} 行"]
        budget -= len(keep)
        staged_outputs.append({**o, "rows": keep})

    dry_run_id = str(_uuid.uuid4())
    from app.data_channel.steward.service import canonical_json_hash
    output_checksum = canonical_json_hash(staged_outputs)
    payload = {
        "pipeline_id": pl.id,
        "dry_run_id": dry_run_id,
        "created_at": _dt.now(_tz.utc).isoformat(),
        "engine_meta": engine_meta,
        "truncated": truncated,
        "outputs": staged_outputs,
        "output_checksum": output_checksum,
    }
    from app.services.storage_service import get_storage_service
    storage = get_storage_service()
    # 机会式清理：同一流水线只保留最新一次暂存，避免 dry-run 结果无限堆积
    try:
        for uri in storage.list_prefix(_DRY_RUN_BUCKET, f"dry-runs/{pl.id}/"):
            storage.delete_object(uri)
    except Exception:  # noqa: BLE001 — 清理失败（如本地回退模式）不影响本次暂存
        pass
    storage.put_bytes(
        _DRY_RUN_BUCKET, f"dry-runs/{pl.id}/{dry_run_id}.json",
        _json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
        content_type="application/json")

    total_out = sum(o["rows_out"] for o in outputs)
    return {
        "dry_run_id": dry_run_id,
        "engine": (pl.definition or {}).get("engine") or "canvas",
        "rows_in": sum(o["rows_in"] for o in outputs),
        "rows_out": total_out,
        "outputs": preview,
    }


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
    import json as _json
    from app.data_channel.datasets.lake_gate import LakeGateError, apply_column_contract

    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    try:
        from app.services.storage_service import get_storage_service
        raw = get_storage_service().get_object(_dry_run_uri(pipeline_id, dry_run_id))
        payload = _json.loads(raw.decode("utf-8"))
    except ValueError:
        raise HTTPException(400, "非法的 dry_run_id")
    except Exception:
        raise HTTPException(404, "试运行结果不存在或已过期，请重新执行")
    if payload.get("pipeline_id") != pipeline_id:
        raise HTTPException(400, "试运行结果与流水线不匹配")

    outputs = payload.get("outputs") or []
    if output_index >= len(outputs):
        raise HTTPException(404, "产物序号超出范围")
    rows = [r for r in (outputs[output_index].get("rows") or []) if isinstance(r, dict)]
    if len(outputs) == 1 and (pl.column_definitions or []):
        try:
            rows, _w = apply_column_contract(rows, pl.column_definitions)
        except LakeGateError:
            pass  # 契约违规不阻断查看数据本身，校验结论由校验接口给出

    columns: list[str] = []
    seen: set[str] = set()
    for r in rows[:200]:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                columns.append(str(k))
    start = (page - 1) * page_size
    return {
        "total": len(rows),
        "page": page,
        "page_size": page_size,
        "columns": columns,
        "rows": rows[start:start + page_size],
    }


@router.post("/{pipeline_id}/dry-run/{dry_run_id}/commit", deprecated=True)
def commit_dry_run(pipeline_id: str, dry_run_id: str):
    """兼容旧客户端的明确拒绝入口。

    试执行只生成预览，不具备资产写入权限；数据任务池是流水线入湖的唯一入口。
    """
    raise HTTPException(
        409,
        "试执行结果不能直接写入资产湖。请在数据任务池创建并执行任务，由任务统一完成入湖。",
    )


class PreviewStepBody(BaseModel):
    op: str
    params: dict = {}
    sample_data: list[dict] = []


@router.post("/preview-step")
def preview_step(body: PreviewStepBody):
    """预览某个 Transform 步骤的输出"""
    try:
        from app.services.v2.pipeline.steps.cleansing import CleansingStep
        from app.services.v2.pipeline.steps.schema_inference import SchemaInferenceStep
        from app.services.v2.pipeline.base import PipelineContext

        ctx = PipelineContext(dataset_id="", version_no=1, route="A", spec={})
        data = body.sample_data or [{"col": "sample"}]

        if body.op in ("drop_duplicates", "fill_nulls", "normalize_dates"):
            step = CleansingStep()
            data = step.run(ctx, data)
        elif body.op == "schema_inference":
            step = SchemaInferenceStep()
            data = step.run(ctx, data)

        return {"op": body.op, "rows_in": len(body.sample_data), "rows_out": len(data), "preview": data[:20]}
    except Exception as e:
        return {"op": body.op, "error": str(e), "rows_in": 0, "rows_out": 0, "preview": []}


# ── Helper ────────────────────────────────────────────────────────

def _format_pipeline(pl: Pipeline) -> dict:
    return {
        "id": pl.id,
        "name": pl.name,
        "domain": pl.domain or "通用",
        "description": pl.description or "",
        "source_dataset_id": pl.source_dataset_id,
        "route": pl.route,
        "spec": pl.spec or {},
        "definition": pl.definition,
        "status": pl.status or "draft",
        # 来源引擎：canvas=系统自定义画布 / n8n=数据管家托管（前端来源列据此渲染）
        "engine": ((pl.definition or {}).get("engine") or "canvas"),
        "enabled": True if pl.enabled is None else bool(pl.enabled),
        "column_definitions": pl.column_definitions,
        "branch": pl.branch or "main",
        "version": pl.version or 1,
        "target_curated_ids": pl.target_curated_ids or [],
        "created_at": pl.created_at.isoformat() if pl.created_at else None,
        "updated_at": pl.updated_at.isoformat() if pl.updated_at else None,
    }
