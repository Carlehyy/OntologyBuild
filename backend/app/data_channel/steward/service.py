"""
数据管家 — 服务层

集中三件事，router / toolkit / runner 都只经这里触碰状态：
  1. 从系统设置加载 n8n 客户端（解密 API Key，未配置给出可操作的报错）
  2. 治理状态机的所有迁移（submit / approve / reject / revoke / demote / archive）
  3. approved ⇄ v2_pipelines 影子流水线的注册与同步
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.workflow_config import WorkflowConfig
from app.settings.workflows.n8n_client import N8nClient
from app.shared.encryption import decrypt
from app.data_channel.steward.models import (
    N8nPipeline,
    STATUS_APPROVED, STATUS_ARCHIVED, STATUS_DRAFT, STATUS_PENDING, STATUS_REJECTED,
)

logger = logging.getLogger(__name__)

#: 受管工作流在 definition 里的引擎标记 — 现有画布/运行路径据此绕行
ENGINE_N8N = "n8n"


class StewardError(Exception):
    """业务可读的错误 — router 转 4xx，toolkit 转工具错误回给 LLM 自我修正。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── n8n 客户端 ────────────────────────────────────────────────────

def n8n_config_status(db: Session) -> dict:
    cfg = db.query(WorkflowConfig).filter(WorkflowConfig.id == "default").first()
    return {
        "configured": bool(cfg and cfg.api_url and cfg.api_key_encrypted),
        "enabled": bool(cfg and cfg.enabled),
        "api_url": (cfg.api_url if cfg else "") or "",
    }


def get_n8n_client(db: Session) -> N8nClient:
    cfg = db.query(WorkflowConfig).filter(WorkflowConfig.id == "default").first()
    if not cfg or not cfg.api_url or not cfg.api_key_encrypted:
        raise StewardError("尚未配置 n8n：请到「系统设置 → 工作流引擎」填写 n8n 地址与 API Key 并通过连接测试。")
    if not cfg.enabled:
        raise StewardError("n8n 集成当前处于停用状态：请到「系统设置 → 工作流引擎」启用后再使用数据管家。")
    try:
        api_key = decrypt(cfg.api_key_encrypted)
    except Exception as exc:  # noqa: BLE001 — Fernet key 变更等
        raise StewardError("已保存的 n8n API Key 无法解密，请到系统设置重新保存。") from exc
    return N8nClient(api_url=cfg.api_url, api_key=api_key,
                     timeout_seconds=cfg.timeout_seconds or 10)


def bootstrap_blank_workflow(db: Session, name: str, description: str = "",
                             user_id: str | None = None) -> N8nPipeline:
    """流水线列表「新建 n8n 流水线」：后台自动在 n8n 创建骨架工作流并纳管为草稿。

    骨架 = Webhook 触发器（平台调度约定：path=ob-*、POST、responseMode=lastNode）
    → NoOp 输出节点。用户随后在数据管家对话中完善取数逻辑，审批后才可被调度。
    """
    import re
    import uuid as _uuid

    name = (name or "").strip()
    if not name:
        raise StewardError("流水线名称不能为空。")
    dup = (db.query(N8nPipeline)
           .filter(N8nPipeline.name == name, N8nPipeline.status != STATUS_ARCHIVED).first())
    if dup:
        raise StewardError(f"已存在同名受管流水线「{name}」，请换一个名称。")

    client = get_n8n_client(db)
    # 中文名转不出可读 slug，统一用随机短径，避免撞车
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:24]
    webhook_path = f"ob-{slug}-{_uuid.uuid4().hex[:6]}" if slug else f"ob-{_uuid.uuid4().hex[:10]}"
    webhook_node = {
        "id": str(_uuid.uuid4()), "name": "Webhook",
        "type": "n8n-nodes-base.webhook", "typeVersion": 2,
        "position": [0, 300],
        "parameters": {"path": webhook_path, "httpMethod": "POST", "responseMode": "lastNode"},
    }
    output_node = {
        "id": str(_uuid.uuid4()), "name": "输出",
        "type": "n8n-nodes-base.noOp", "typeVersion": 1,
        "position": [260, 300], "parameters": {},
    }
    created = client.create_workflow({
        "name": name,
        "nodes": [webhook_node, output_node],
        "connections": {"Webhook": {"main": [[{"node": "输出", "type": "main", "index": 0}]]}},
        "settings": {},
    })
    rec = N8nPipeline(
        name=name,
        description=(description or "").strip(),
        n8n_workflow_id=str(created.get("id")),
        workflow_snapshot=N8nClient.sanitize_workflow(created),
        created_by=user_id,
    )
    db.add(rec)
    db.flush()
    # 创建即在流水线列表可见（draft 影子行）；审批只负责升级为 published
    ensure_shadow_pipeline(db, rec)
    db.commit()
    db.refresh(rec)
    return rec


# ── workflow JSON 摘要与约定 ──────────────────────────────────────

TRIGGER_TYPE_PREFIXES = ("n8n-nodes-base.webhook", "n8n-nodes-base.scheduleTrigger",
                         "n8n-nodes-base.cron", "n8n-nodes-base.manualTrigger")


def find_webhook_path(workflow: dict | None) -> str | None:
    """取第一个 Webhook 触发节点的 path — 平台调度该流水线的入口。"""
    for node in (workflow or {}).get("nodes") or []:
        if str(node.get("type", "")) == "n8n-nodes-base.webhook":
            path = (node.get("parameters") or {}).get("path")
            if path:
                return str(path)
    return None


def summarize_workflow(workflow: dict | None) -> dict:
    nodes = (workflow or {}).get("nodes") or []
    return {
        "node_count": len(nodes),
        "nodes": [{
            "name": n.get("name"),
            "type": str(n.get("type", "")).replace("n8n-nodes-base.", ""),
            "disabled": bool(n.get("disabled")),
        } for n in nodes],
        "has_trigger": any(str(n.get("type", "")).startswith(TRIGGER_TYPE_PREFIXES) for n in nodes),
        "webhook_path": find_webhook_path(workflow),
    }


def record_out(rec: N8nPipeline, *, active: bool | None = None) -> dict:
    """记录的 API/工具统一出参。active 需调 n8n 才知道，可选传入。"""
    out = {
        "id": rec.id,
        "name": rec.name,
        "description": rec.description or "",
        "n8nWorkflowId": rec.n8n_workflow_id,
        "status": rec.status,
        "pipelineId": rec.pipeline_id,
        "conversationId": rec.conversation_id,
        "summary": summarize_workflow(rec.workflow_snapshot),
        "submittedAt": rec.submitted_at.isoformat() if rec.submitted_at else None,
        "approvedBy": rec.approved_by,
        "approvedAt": rec.approved_at.isoformat() if rec.approved_at else None,
        "rejectReason": rec.reject_reason,
        "createdAt": rec.created_at.isoformat() if rec.created_at else None,
        "updatedAt": rec.updated_at.isoformat() if rec.updated_at else None,
    }
    if active is not None:
        out["active"] = active
    return out


def require_record(db: Session, record_id: str) -> N8nPipeline:
    rec = db.query(N8nPipeline).filter(N8nPipeline.id == record_id,
                                       N8nPipeline.status != STATUS_ARCHIVED).first()
    if not rec:
        raise StewardError(f"数据管家流水线记录不存在或已归档: {record_id}")
    return rec


# ── 状态机迁移 ────────────────────────────────────────────────────

def submit_for_approval(db: Session, rec: N8nPipeline) -> N8nPipeline:
    if rec.status not in (STATUS_DRAFT, STATUS_REJECTED):
        raise StewardError(f"当前状态为 {rec.status}，只有草稿或被拒绝的记录可以提交审批。")
    summary = summarize_workflow(rec.workflow_snapshot)
    if not summary["has_trigger"]:
        raise StewardError("工作流没有任何触发器节点（Webhook / Schedule Trigger），无法提交审批。")
    rec.status = STATUS_PENDING
    rec.submitted_at = _now()
    rec.reject_reason = None
    db.commit()
    return rec


def approve(db: Session, rec: N8nPipeline, client: N8nClient, approver_id: str | None) -> N8nPipeline:
    """批准：拉最新 workflow 快照 → 激活 → 注册/更新影子流水线（published）。"""
    if rec.status != STATUS_PENDING:
        raise StewardError(f"当前状态为 {rec.status}，只有待审批的记录可以批准。")
    workflow = client.get_workflow(rec.n8n_workflow_id)
    try:
        client.activate_workflow(rec.n8n_workflow_id)
    except Exception as exc:  # noqa: BLE001 — 无触发器等激活失败原样透出
        raise StewardError(f"激活 n8n 工作流失败：{exc}") from exc

    rec.workflow_snapshot = N8nClient.sanitize_workflow(workflow)
    rec.approved_snapshot = rec.workflow_snapshot
    rec.status = STATUS_APPROVED
    rec.approved_by = approver_id
    rec.approved_at = _now()
    rec.reject_reason = None
    _register_shadow_pipeline(db, rec)
    db.commit()
    return rec


def reject(db: Session, rec: N8nPipeline, reason: str | None) -> N8nPipeline:
    if rec.status != STATUS_PENDING:
        raise StewardError(f"当前状态为 {rec.status}，只有待审批的记录可以拒绝。")
    rec.status = STATUS_REJECTED
    rec.reject_reason = (reason or "").strip() or None
    _demote_shadow_pipeline(db, rec)
    db.commit()
    return rec


def revoke(db: Session, rec: N8nPipeline, client: N8nClient) -> N8nPipeline:
    """已批准 → 转回草稿：停用 workflow + 影子流水线降级，需重新审批。"""
    if rec.status != STATUS_APPROVED:
        raise StewardError(f"当前状态为 {rec.status}，只有已批准的记录可以转回草稿。")
    _safe_deactivate(client, rec.n8n_workflow_id)
    rec.status = STATUS_DRAFT
    rec.approved_by = None
    rec.approved_at = None
    _demote_shadow_pipeline(db, rec)
    db.commit()
    return rec


def demote_on_edit(db: Session, rec: N8nPipeline, client: N8nClient) -> bool:
    """agent 修改了已批准/待审批的工作流 → 自动退回草稿（治理硬规则）。

    返回是否发生了降级，供工具结果向 LLM/用户明示。
    """
    if rec.status == STATUS_APPROVED:
        _safe_deactivate(client, rec.n8n_workflow_id)
        rec.status = STATUS_DRAFT
        rec.approved_by = None
        rec.approved_at = None
        _demote_shadow_pipeline(db, rec)
        return True
    if rec.status == STATUS_PENDING:
        rec.status = STATUS_DRAFT
        rec.submitted_at = None
        return True
    return False


def archive(db: Session, rec: N8nPipeline, client: N8nClient | None,
            delete_workflow: bool = False) -> N8nPipeline:
    if rec.status == STATUS_APPROVED:
        raise StewardError("已批准的流水线不能直接归档，请先「转回草稿」。")
    if client is not None:
        _safe_deactivate(client, rec.n8n_workflow_id)
        if delete_workflow:
            try:
                client.delete_workflow(rec.n8n_workflow_id)
            except Exception:  # noqa: BLE001 — n8n 侧已删等场景不阻塞归档
                logger.warning("归档时删除 n8n workflow %s 失败", rec.n8n_workflow_id, exc_info=True)
    rec.status = STATUS_ARCHIVED
    _remove_shadow_pipeline(db, rec)
    db.commit()
    return rec


def _safe_deactivate(client: N8nClient, workflow_id: str) -> None:
    try:
        client.deactivate_workflow(workflow_id)
    except Exception:  # noqa: BLE001 — 本就未激活/已删除时不阻塞治理迁移
        logger.warning("停用 n8n workflow %s 失败（忽略）", workflow_id, exc_info=True)


# ── 影子流水线（v2_pipelines, engine=n8n） ────────────────────────

def _shadow_definition(rec: N8nPipeline) -> dict:
    return {
        # nodes/edges 留空数组：画布等旧代码读取时不会崩，但引擎标记让它们绕行
        "engine": ENGINE_N8N,
        "nodes": [],
        "edges": [],
        "n8n": {
            "steward_id": rec.id,
            "workflow_id": rec.n8n_workflow_id,
            "webhook_path": find_webhook_path(rec.workflow_snapshot),
            # 审批契约：以最近一次试跑的列集合为期望列，运行期资产湖闸门
            # 据此做漂移检测（警告不阻断——湖中主键契约才是硬校验）
            "expected_columns": (rec.last_test_result or {}).get("columns") or None,
        },
    }


def ensure_shadow_pipeline(db: Session, rec: N8nPipeline, *, status: str | None = None):
    """确保治理记录有对应的 v2_pipelines 影子行，并同步名称/定义。

    创建即登记（draft）——n8n 流水线从诞生起就出现在流水线列表里；
    数据管家只是 AI 编排工具，审批决定的是「能否被调度」（published），
    不是「存不存在」。status=None 时保持现状（新建默认 draft）。
    """
    from app.models.v2.pipeline import Pipeline

    pl = db.query(Pipeline).filter(Pipeline.id == rec.pipeline_id).first() if rec.pipeline_id else None
    if pl is None:
        pl = Pipeline(
            name=rec.name,
            domain="智能编排",
            description=rec.description or f"由数据管家对话创建的 n8n 流水线（workflow {rec.n8n_workflow_id}）",
            route="A",  # 列有 NOT NULL 约束；n8n 引擎不使用 route
            spec={},
            status="draft",
        )
        db.add(pl)
        db.flush()
        rec.pipeline_id = pl.id
    pl.name = rec.name
    pl.description = rec.description or pl.description
    pl.definition = _shadow_definition(rec)
    if status is not None:
        pl.status = status
    pl.updated_at = _now()
    return pl


def _register_shadow_pipeline(db: Session, rec: N8nPipeline) -> None:
    """approved → 可被任务池调度：status=published + 版本快照。"""
    from app.models.v2.pipeline import PipelineVersion

    pl = ensure_shadow_pipeline(db, rec, status="published")
    pl.version = (pl.version or 1) + 1
    db.add(PipelineVersion(pipeline_id=pl.id, version=pl.version,
                           definition=pl.definition, status="published"))


def _demote_shadow_pipeline(db: Session, rec: N8nPipeline) -> None:
    """失去 approved 资格 → 影子流水线退回 draft（任务池发布校验自然拦截）。"""
    if not rec.pipeline_id:
        return
    from app.models.v2.pipeline import Pipeline

    pl = db.query(Pipeline).filter(Pipeline.id == rec.pipeline_id).first()
    if pl is not None:
        pl.definition = _shadow_definition(rec)
        pl.status = "draft"
        pl.updated_at = _now()


def _remove_shadow_pipeline(db: Session, rec: N8nPipeline) -> None:
    """归档 → 影子流水线从列表移除（运行记录/版本随外键级联清理）。

    治理记录本身保留（status=archived）作为审计痕迹。
    """
    if not rec.pipeline_id:
        return
    from app.models.v2.pipeline import Pipeline

    pl = db.query(Pipeline).filter(Pipeline.id == rec.pipeline_id).first()
    if pl is not None:
        db.delete(pl)
    rec.pipeline_id = None
