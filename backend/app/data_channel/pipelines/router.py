"""v2 Pipeline API — 支持新 DSL (nodes/edges) + 旧 steps 格式兼容"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone
from app.database import SessionLocal
from app.deps import get_current_user
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineVersion
# 确保 Dataset 模型先导入以解析 FK
import app.models.v2.dataset  # noqa: F401

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
    name: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    source_dataset_id: Optional[str] = None
    route: Optional[str] = None
    spec: Optional[dict] = None
    definition: Optional[dict] = None
    status: Optional[str] = None


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
    """数据管家托管的 n8n 影子流水线 — 生命周期由审批流管理，画布路径绕行。"""
    return ((pl.definition or {}).get("engine") == "n8n")


# ── CRUD ──────────────────────────────────────────────────────────

@router.post("", response_model=PipelineResponse, status_code=201)
def create_pipeline(body: PipelineCreate, db: Session = Depends(get_db)):
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
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)
    return _format_pipeline(pl)


@router.get("", response_model=list[dict])
def list_pipelines(
    search: str = "",
    domain: str = "",
    status: str = "",
    db: Session = Depends(get_db),
):
    """Pipeline 列表，支持按名称/ID/域/状态搜索。"""
    q = db.query(Pipeline)
    if search:
        q = q.filter(
            Pipeline.name.ilike(f"%{search}%") | Pipeline.id.ilike(f"%{search}%")
        )
    if domain:
        q = q.filter(Pipeline.domain == domain)
    if status:
        q = q.filter(Pipeline.status == status)
    q = q.order_by(Pipeline.updated_at.desc()).limit(100)
    results = []
    for pl in q:
        d = _format_pipeline(pl)
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
    if _is_n8n_pipeline(pl):
        raise HTTPException(400, "该流水线由数据管家托管（n8n 引擎），请在数据管家对话中修改，或在审批面板管理其状态。")

    update_data = body.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(pl, k, v)
    pl.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(pl)
    return _format_pipeline(pl)


@router.delete("/{pipeline_id}")
def delete_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    if _is_n8n_pipeline(pl):
        raise HTTPException(400, "该流水线由数据管家托管（n8n 引擎），请到数据管家审批面板先转回草稿再归档。")
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

    # ── n8n 引擎（数据管家托管）────────────────────────────
    if _is_n8n_pipeline(pl):
        from app.data_channel.steward.models import N8nPipeline, STATUS_APPROVED
        from app.data_channel.steward.service import find_webhook_path

        n8n_def = (definition or {}).get("n8n") or {}
        rec = db.query(N8nPipeline).filter(
            N8nPipeline.id == n8n_def.get("steward_id")).first() if n8n_def.get("steward_id") else None
        if rec is None:
            errors.append({"node_id": "", "severity": "error",
                           "message": "缺少数据管家治理记录，无法运行。请到数据管家页面重新审批注册。"})
        elif rec.status != STATUS_APPROVED:
            errors.append({"node_id": "", "severity": "error",
                           "message": f"该 n8n 流水线未获批准（当前状态 {rec.status}），请先在数据管家中完成审批。"})
        elif not (n8n_def.get("webhook_path") or find_webhook_path(rec.workflow_snapshot)):
            warnings.append({"node_id": "", "severity": "warning",
                             "message": "工作流没有 Webhook 触发器，平台无法主动调度（仅能由 n8n 内部定时自跑，产物不会自动入湖）。"})
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
        warnings.append({
            "node_id": "", "severity": "warning",
            "message": "存在 Connector 但没有 Output 节点，Pipeline 不会产生输出。",
        })

    return ValidateResult(valid=len(errors) == 0, errors=errors, warnings=warnings)


# ── Publish ───────────────────────────────────────────────────────

@router.post("/{pipeline_id}/publish")
def publish_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    """发布 Pipeline 为稳定版本。"""
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    if _is_n8n_pipeline(pl):
        raise HTTPException(400, "n8n 流水线的发布由数据管家审批流程管理：请在数据管家页面提交审批并由用户批准。")

    # 先校验
    validation = validate_pipeline(pipeline_id, db)
    if not validation.valid:
        raise HTTPException(400, f"Pipeline 校验失败，无法发布: {validation.errors}")

    pl.status = "published"
    pl.version = (pl.version or 1) + 1
    pl.updated_at = datetime.now(timezone.utc)
    db.commit()

    # 保存版本快照
    version_record = PipelineVersion(
        pipeline_id=pipeline_id,
        version=pl.version,
        definition=pl.definition,
        status="published",
    )
    db.add(version_record)
    db.commit()

    return {
        "id": pl.id,
        "status": pl.status,
        "version": pl.version,
    }


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

    prev_status = pl.status
    pl.status = "running"
    run = PipelineRun(pipeline_id=pipeline_id, status="pending", started_at=datetime.now(timezone.utc))
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        _ensure_broker_reachable()
        from app.tasks.v2.pipeline_run import pipeline_run_task
        pipeline_run_task.delay(pipeline_id, run.id)
    except Exception as e:
        # Celery/Redis 不可用时立即标记失败，避免 run 永远停在 pending
        run.status = "failed"
        run.error_log = f"任务派发失败 (Celery/Redis 不可用?): {e}"
        run.finished_at = datetime.now(timezone.utc)
        # n8n 影子流水线的 published 状态是任务池调度资格，派发失败不清除
        pl.status = prev_status if _is_n8n_pipeline(pl) else "failed"
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
    """同步执行 Pipeline（无需 Celery/Redis，适用于开发/测试）"""
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")

    pl.status = "running"
    run = PipelineRun(pipeline_id=pipeline_id, status="pending", started_at=datetime.now(timezone.utc))
    db.add(run)
    db.commit()
    db.refresh(run)

    is_n8n = _is_n8n_pipeline(pl)
    try:
        from app.tasks.v2.pipeline_run import pipeline_run_task
        prev_status = pl.status
        pipeline_run_task(pipeline_id, run.id)
        db.refresh(run)
        db.refresh(pl)
        # 运行成功不隐式发布草稿管道：恢复原状态（发布必须走 publish 流程）。
        # n8n 引擎的状态复位由 runner 自管（已批准恒为 published），此处不插手。
        if not is_n8n:
            if run.status == "success":
                if pl.status == "running":
                    pl.status = prev_status if prev_status not in ("running", "failed") else "draft"
            else:
                pl.status = "failed"
        db.commit()
        return {"run_id": run.id, "status": run.status, "stats": run.stats, "error": run.error_log}
    except Exception as e:
        if not is_n8n:
            pl.status = "failed"
        db.commit()
        return {"run_id": run.id, "status": "failed", "error": str(e)}


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
        "branch": pl.branch or "main",
        "version": pl.version or 1,
        "target_curated_ids": pl.target_curated_ids or [],
        "created_at": pl.created_at.isoformat() if pl.created_at else None,
        "updated_at": pl.updated_at.isoformat() if pl.updated_at else None,
    }
