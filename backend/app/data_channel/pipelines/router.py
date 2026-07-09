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
from app.data_channel.steward.models import N8nPipeline
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
    # 状态经 publish/unpublish 端点、启用经 PATCH /enabled——通用更新不允许
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
    生命周期（发布/撤回/删除）与画布流水线同走本路由。"""
    return ((pl.definition or {}).get("engine") == "n8n")


def _reject_if_sync_chain_refs(db: Session, pipeline_id: str, *, action: str) -> None:
    """同步任务把该流水线设为链式触发目标时拦截删除/撤回发布。

    与任务池引用同一道理：删除靠 FK SET NULL、撤回靠运行期跳过兜底，
    但两者都只剩日志可见——链路静默断掉不如让操作者当场看见并解绑。"""
    from app.data_channel.sync_tasks.models import DataSyncTask

    refs = db.query(DataSyncTask).filter(
        DataSyncTask.trigger_pipeline_id == pipeline_id).all()
    if refs:
        names = "、".join(t.name for t in refs[:3])
        raise HTTPException(
            400,
            f"流水线被 {len(refs)} 个同步任务设为链式触发目标（{names}{'…' if len(refs) > 3 else ''}），"
            f"不能{action}。请先在这些同步任务中解除「同步后触发流水线」的配置。")


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

    # ── 已发布封版：definition / column_definitions / spec 不可修改 ──
    # 按 key 存在性判断而非值非空——显式传 null 同样是修改（会把封版字段清空）
    PROTECTED_FIELDS = ("definition", "column_definitions", "spec")
    if (pl.status or "") == "published":
        protected_present = [k for k in PROTECTED_FIELDS if k in update_data]
        if protected_present:
            raise HTTPException(
                400,
                f"流水线已发布，封版字段不可修改：{', '.join(protected_present)}。"
                f"如需修改，请先撤回发布（要求没有调度任务引用），或复制为新流水线。"
            )

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

    # ── n8n：删除 = 归档治理记录（停用 workflow + 影子行移除；n8n 侧
    #    工作流保留，不自动删除）。引用保护在 archive 内统一做。──
    if _is_n8n_pipeline(pl):
        from app.data_channel.steward import service as steward_service

        rec = steward_service.record_for_pipeline(db, pl)
        if rec is None:
            # 治理记录丢失的孤儿影子行：按普通流水线清理，别把删除堵死
            pass
        else:
            client = None
            try:
                client = steward_service.get_n8n_client(db)
            except steward_service.StewardError:
                pass  # n8n 未配置也允许删除平台侧记录
            try:
                steward_service.archive(db, rec, client)
            except steward_service.StewardError as e:
                raise HTTPException(400, str(e))
            return {"status": "deleted", "id": pipeline_id}

    # 引用保护：被调度任务引用的流水线不可删——删了任务会静默失效
    from app.data_channel.pipeline_tasks.models import PipelineTask
    refs = db.query(PipelineTask).filter(PipelineTask.pipeline_id == pipeline_id).all()
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

    # ── n8n 引擎（数据管家托管）：校验绑定完整性，发布资格不在此处
    #    （发布与否由本路由的 publish/unpublish 管理，validate 需在
    #    未发布时也通过，否则发布流程死锁）────────────────────
    if _is_n8n_pipeline(pl):
        from app.data_channel.steward.service import find_webhook_path, record_for_pipeline

        n8n_def = (definition or {}).get("n8n") or {}
        rec = record_for_pipeline(db, pl)
        if rec is None:
            errors.append({"node_id": "", "severity": "error",
                           "message": "缺少数据管家治理记录，无法运行。请删除后在数据管家重新新建该流水线。"})
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
      2. 类型 —— field_type 与全量数据的值类型一致（不符 → 警告）
      3. 主键 —— 主键组合在全量数据中非空且唯一（→ 错误）
      4. 空值 —— nullable=false 的列在全量数据中无空值（→ 错误）
      5. 湖契约 —— 主键与目标资产湖数据集已固化的主键不冲突（→ 错误）
    """
    import json as _json
    from app.data_channel.datasets.lake_gate import (
        FIELD_KEY_RE, _value_type, normalize_definitions, split_pk)
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

    outputs = payload.get("outputs") or []
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
    if payload.get("truncated"):
        errors.append({"field_key": "", "severity": "warning",
                       "message": f"试运行输出超过暂存上限，本次校验仅覆盖已暂存的 {len(rows):,} 行（前缀校验，非全量）"})

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

    # ── 2. 类型匹配（全量，警告不阻断）──
    for d in defs:
        expected = d["field_type"]
        sk = d["source_key"]
        if sk not in actual_columns or expected in ("string", "json"):
            continue
        observed: set[str] = set()
        for row in rows:
            actual = _value_type(row.get(sk))
            if actual is None or actual == expected:
                continue
            if expected == "float" and actual == "integer":
                continue
            observed.add(actual)
        if observed:
            errors.append({"field_key": d["field_key"], "severity": "warning",
                           "message": f"字段类型声明为「{expected}」，但全量数据中出现「{'、'.join(sorted(observed))}」"})

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

    has_blocking = any(e["severity"] == "error" for e in errors)
    return ValidateDefinitionsResult(valid=not has_blocking, errors=errors)


# ── Publish / Unpublish ───────────────────────────────────────────

class PublishBody(BaseModel):
    enable: bool = False  # 发布并同时启用（向导「发布并启用」勾选）


@router.post("/{pipeline_id}/publish")
def publish_pipeline(pipeline_id: str, body: PublishBody | None = None,
                     db: Session = Depends(get_db)):
    """发布 Pipeline：封版编排与字段契约，此后仅名称/描述可修改。

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

    # ── n8n：发布 = 激活 workflow + 固化 definition（webhook/期望列）。
    #    激活放在状态翻转之前——激活失败发布必须中止；反向顺序会留下
    #    「平台已发布、n8n 未激活」的必失败组合 ──
    if _is_n8n_pipeline(pl):
        from app.data_channel.steward import service as steward_service

        rec = steward_service.record_for_pipeline(db, pl)
        if rec is None:
            raise HTTPException(400, "缺少数据管家治理记录，无法发布。请删除后在数据管家重新新建该流水线。")
        try:
            client = steward_service.get_n8n_client(db)
            steward_service.activate_for_publish(db, rec, client)
        except steward_service.StewardError as e:
            raise HTTPException(400, str(e))
        # 刷新影子 definition：固化 webhook_path 与试跑列（运行期漂移检测基线）
        steward_service.ensure_shadow_pipeline(db, rec)

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
    if body and body.enable:
        pl.enabled = True
    pl.updated_at = datetime.now(timezone.utc)
    # 版本快照与状态翻转同一事务提交——分两段 commit 会在中断时留下
    # 「已发布却无快照」的孤儿状态（契约是封版核心工件，必须可回溯）
    db.add(PipelineVersion(
        pipeline_id=pipeline_id,
        version=pl.version,
        definition=pl.definition,
        column_definitions=pl.column_definitions,
        status="published",
    ))
    db.commit()

    return {
        "id": pl.id,
        "status": pl.status,
        "version": pl.version,
        "enabled": True if pl.enabled is None else bool(pl.enabled),
        "warnings": warnings or None,
    }


@router.post("/{pipeline_id}/unpublish")
def unpublish_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    """撤回发布（封版的受控逃生通道）：仅允许未被任何调度任务引用的流水线。

    撤回后回到草稿态并自动停用，维持「只有已发布才能启用」的不变量。
    n8n 引擎同走此口（同时停用 n8n workflow）——撤回后即可回数据管家继续编排。
    """
    from app.data_channel.pipeline_tasks.models import PipelineTask

    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    if (pl.status or "") != "published":
        raise HTTPException(400, "流水线未发布，无需撤回。")
    refs = db.query(PipelineTask).filter(PipelineTask.pipeline_id == pipeline_id).all()
    if refs:
        names = "、".join(t.name for t in refs[:3])
        raise HTTPException(
            400,
            f"流水线已被 {len(refs)} 个调度任务引用（{names}{'…' if len(refs) > 3 else ''}），不能撤回发布。"
            f"请先在数据任务池删除或改绑这些任务。")
    _reject_if_sync_chain_refs(db, pipeline_id, action="撤回发布")

    # n8n：撤回发布同时停用 workflow（n8n 侧已删/本就停用时不阻塞）
    if _is_n8n_pipeline(pl):
        from app.data_channel.steward import service as steward_service

        rec = steward_service.record_for_pipeline(db, pl)
        if rec is not None:
            try:
                client = steward_service.get_n8n_client(db)
            except steward_service.StewardError as e:
                raise HTTPException(400, f"撤回发布需要停用 n8n 工作流：{e}")
            steward_service.deactivate_on_unpublish(rec, client)

    pl.status = "draft"
    pl.enabled = False
    pl.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(pl)
    return _format_pipeline(pl)


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


# ── 启用开关 + 试运行预览（不落湖）→ 确认后入湖 ──────────────────────

class EnabledBody(BaseModel):
    enabled: bool


@router.patch("/{pipeline_id}/enabled")
def set_pipeline_enabled(pipeline_id: str, body: EnabledBody, db: Session = Depends(get_db)):
    """启用/停用流水线：停用后任务池调度与同步链式触发都不执行。

    只有已发布的流水线才能启用（画布与 n8n 同一规则）；停用任何状态都允许。
    """
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    if body.enabled and (pl.status or "") != "published":
        raise HTTPException(400, "只有已发布的流水线才能启用。请先在编辑向导中完成发布。")
    pl.enabled = bool(body.enabled)
    pl.updated_at = datetime.now(timezone.utc)
    db.commit()
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
                rows, engine_meta = collect_test_rows(db, rec)
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
    payload = {
        "pipeline_id": pl.id,
        "created_at": _dt.now(_tz.utc).isoformat(),
        "engine_meta": engine_meta,
        "truncated": truncated,
        "outputs": staged_outputs,
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
        "can_save": total_out > 0 and not any(p["gate_error"] for p in preview),
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


@router.post("/{pipeline_id}/dry-run/{dry_run_id}/commit")
def commit_dry_run(pipeline_id: str, dry_run_id: str, db: Session = Depends(get_db)):
    """把某次试运行的输出按原样写入资产湖（不重新执行流水线）。

    走与正式运行完全相同的入湖通道（准入闸门 + 版本化），并生成一条
    PipelineRun 记录保住血缘：产物版本能回溯到这次确认入湖。
    """
    import json as _json
    from app.services.v2.pipeline.base import PipelineContext
    from app.services.v2.dataset_service import DatasetService
    from app.data_channel.datasets.lake_gate import LakeGateError
    from app.tasks.v2.pipeline_run import _save_curated_dataset

    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    # 治理边界：未发布的 n8n 流水线——临时激活试跑出来的数据只供预览与
    # 契约校验，不允许经「确认入湖」流入正式资产
    if _is_n8n_pipeline(pl) and (pl.status or "") != "published":
        raise HTTPException(
            400, "该 n8n 流水线尚未发布，试跑数据不能写入资产湖。"
                 "请先在编辑向导中「发布并启用」，之后即可正常入湖。")
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
    if payload.get("truncated"):
        raise HTTPException(
            400, "该次试运行输出超过暂存上限、预览数据已截断，按预览入湖会丢行。"
                 "请通过任务调度或「立即运行」完成完整入湖。")

    run = PipelineRun(
        pipeline_id=pipeline_id, status="running",
        started_at=datetime.now(timezone.utc),
        stats={"triggered_by": "preview-commit", "dry_run_id": dry_run_id},
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    svc = DatasetService(db)
    saved = []
    try:
        for o in payload.get("outputs") or []:
            ctx = PipelineContext(
                dataset_id=o["source"].get("dataset_id") or "",
                version_no=1, route=o.get("route") or "A", spec={})
            ctx.rows_in = int(o.get("rows_in") or 0)
            ctx.rows_out = int(o.get("rows_out") or 0)
            ctx.meta = dict(o.get("meta") or {})
            if payload.get("engine_meta"):
                ctx.meta["n8n_execution"] = payload["engine_meta"]
            saved.append(_save_curated_dataset(
                db, svc, pl, o["source"], o.get("rows") or [], ctx,
                bool(o.get("multi_source")), table_name=o.get("table_name"),
                write_opts=None))
    except LakeGateError as e:
        run.status = "failed"
        run.error_log = str(e)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        run.status = "failed"
        run.error_log = str(e)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(500, f"入湖失败：{e}")

    curated_ids = [s["curated_dataset_id"] for s in saved]
    pl.target_curated_ids = curated_ids
    run.status = "success"
    run.finished_at = datetime.now(timezone.utc)
    run.dataset_version_id = next(
        (s.get("dataset_version_id") for s in saved if s.get("dataset_version_id")), None)
    run.stats = {
        **(run.stats or {}),
        "engine": (pl.definition or {}).get("engine") or "canvas",
        "rows_in": sum(s.get("rows_in") or 0 for s in saved),
        "rows_out": sum(s.get("rows_out") or 0 for s in saved),
        "lake_rows": sum(s.get("lake_rows") or 0 for s in saved),
        "gate_warnings": [w for s in saved for w in (s.get("gate_warnings") or [])] or None,
        "curated_dataset_id": curated_ids[0] if curated_ids else None,
        "curated_dataset_ids": curated_ids,
        "meta": {"outputs": saved},
    }
    db.commit()
    return {
        "run_id": run.id,
        "curated_dataset_ids": curated_ids,
        "lake_rows": sum(s.get("lake_rows") or 0 for s in saved),
        "outputs": saved,
    }


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
