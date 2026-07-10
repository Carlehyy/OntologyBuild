"""
数据管家 — 服务层

集中三件事，router / toolkit / runner 都只经这里触碰状态：
  1. 从系统设置加载 n8n 客户端（解密 API Key，未配置给出可操作的报错）
  2. 受管记录的生命周期辅助（创建/纳管登记影子行、归档、发布封版守卫）
  3. 发布/撤回发布时的 n8n 激活与停用（被 pipelines 的 publish/unpublish
     端点调用——发布唯一入口在流水线编辑向导，数据管家只编排不发布）
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.workflow_config import WorkflowConfig
from app.settings.workflows.n8n_client import N8nClient
from app.shared.encryption import decrypt
from app.data_channel.steward.models import N8nPipeline, STATUS_ARCHIVED, STATUS_DRAFT

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
    → NoOp 输出节点。用户随后在数据管家对话中完善取数逻辑，在编辑向导中
    发布后才可被调度。
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
    # 创建即在流水线列表可见（draft 影子行）；发布在编辑向导完成
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
        "connections": (workflow or {}).get("connections") or {},
        "has_trigger": any(str(n.get("type", "")).startswith(TRIGGER_TYPE_PREFIXES) for n in nodes),
        "webhook_path": find_webhook_path(workflow),
    }


def record_out(db: Session, rec: N8nPipeline, *, active: bool | None = None) -> dict:
    """记录的 API/工具统一出参。发布状态取自影子流水线（生命周期唯一真源）；
    active 需调 n8n 才知道，可选传入。"""
    out = {
        "id": rec.id,
        "name": rec.name,
        "description": rec.description or "",
        "n8nWorkflowId": rec.n8n_workflow_id,
        "status": rec.status,
        "pipelineId": rec.pipeline_id,
        "pipelineStatus": shadow_status(db, rec),
        "conversationId": rec.conversation_id,
        "summary": summarize_workflow(rec.workflow_snapshot),
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


def record_for_pipeline(db: Session, pl) -> N8nPipeline | None:
    """从影子流水线（v2_pipelines, engine=n8n）反查治理记录。"""
    sid = ((pl.definition or {}).get("n8n") or {}).get("steward_id")
    return db.query(N8nPipeline).filter(N8nPipeline.id == sid).first() if sid else None


# ── 发布封版守卫（生命周期唯一真源 = 影子流水线 status） ──────────

def shadow_pipeline(db: Session, rec: N8nPipeline):
    if not rec.pipeline_id:
        return None
    from app.models.v2.pipeline import Pipeline

    return db.query(Pipeline).filter(Pipeline.id == rec.pipeline_id).first()


def shadow_status(db: Session, rec: N8nPipeline) -> str:
    pl = shadow_pipeline(db, rec)
    return (pl.status or "draft") if pl is not None else "draft"


def require_unpublished(db: Session, rec: N8nPipeline) -> None:
    """编排守卫：已发布的流水线封版，编排前必须先在编辑向导撤回发布。"""
    if shadow_status(db, rec) == "published":
        raise StewardError(
            f"流水线「{rec.name}」已发布（封版），不能修改编排。"
            f"请先在流水线列表的编辑向导中「撤回发布」，再回来继续编排。")


def require_orchestrable(db: Session, rec: N8nPipeline, client: N8nClient) -> None:
    """编排守卫（数据管家仅有的写权限边界）：只允许编排「未发布 且 未启用」的流水线。

    - 未发布：影子流水线 status != published（发布=封版，改前须撤回发布）
    - 未启用：n8n 侧 active == False（发布会激活；若有人在 n8n 界面手动开了
      开关，会出现"未发布却已激活"的漂移，此处 live 校验一并拦住）

    正常流程里两条件同真同假；分开校验是为了兜住 n8n 侧手动激活的漂移，
    严格对齐"针对未发布和未启用的 n8n 流水线辅助编排"的职权边界。
    """
    require_unpublished(db, rec)
    try:
        active = bool(client.get_workflow(rec.n8n_workflow_id).get("active"))
    except Exception:  # noqa: BLE001 — n8n 不可达时不因探测失败误伤编排
        active = False
    if active:
        raise StewardError(
            f"流水线「{rec.name}」在 n8n 侧处于已启用状态，数据管家只能编排未启用的流水线。"
            f"请先在 n8n 界面停用该工作流（或在流水线编辑向导中撤回发布）后再编排。")


# ── 发布 / 撤回发布的 n8n 侧动作（被 pipelines publish/unpublish 调用） ──

def activate_for_publish(db: Session, rec: N8nPipeline, client: N8nClient) -> None:
    """发布 n8n 流水线：拉最新快照 → 校验触发器 → 激活 workflow。

    状态翻转/版本快照由 publish 端点的通用封版逻辑承担；这里只做 n8n
    特有的部分。激活失败原样抛出，发布随之中止。
    """
    workflow = client.get_workflow(rec.n8n_workflow_id)
    rec.workflow_snapshot = N8nClient.sanitize_workflow(workflow)
    summary = summarize_workflow(rec.workflow_snapshot)
    if not summary["has_trigger"]:
        raise StewardError("工作流没有任何触发器节点（Webhook / Schedule Trigger），无法发布。"
                           "请先在数据管家中补全编排。")
    try:
        client.activate_workflow(rec.n8n_workflow_id)
    except Exception as exc:  # noqa: BLE001 — 激活失败原样透出
        raise StewardError(f"激活 n8n 工作流失败：{exc}") from exc


def deactivate_on_unpublish(rec: N8nPipeline, client: N8nClient) -> None:
    """撤回发布：停用 n8n workflow（本就未激活/已删除时不阻塞撤回）。"""
    _safe_deactivate(client, rec.n8n_workflow_id)


def archive(db: Session, rec: N8nPipeline, client: N8nClient | None,
            delete_workflow: bool = False) -> N8nPipeline:
    """归档 = 退出平台管理：停用 workflow + 影子行从流水线列表移除。

    两个入口（数据管家面板「归档」、流水线列表「删除」）共用；被调度任务
    或同步链引用时拒绝——删了任务会静默失效。
    """
    _reject_if_shadow_referenced(db, rec)
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


def _reject_if_shadow_referenced(db: Session, rec: N8nPipeline) -> None:
    if not rec.pipeline_id:
        return
    from app.data_channel.pipeline_tasks.models import PipelineTask

    refs = db.query(PipelineTask).filter(PipelineTask.pipeline_id == rec.pipeline_id).all()
    if refs:
        names = "、".join(t.name for t in refs[:3])
        raise StewardError(
            f"流水线已被 {len(refs)} 个调度任务引用（{names}{'…' if len(refs) > 3 else ''}），"
            f"请先在数据任务池删除或改绑这些任务。")
    from app.data_channel.pipelines.router import _reject_if_sync_chain_refs
    from fastapi import HTTPException

    try:
        _reject_if_sync_chain_refs(db, rec.pipeline_id, action="归档")
    except HTTPException as exc:
        raise StewardError(str(exc.detail)) from exc


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
            # 发布契约：以最近一次试跑的列集合为期望列，运行期资产湖闸门
            # 据此做漂移检测（警告不阻断——湖中主键契约才是硬校验）
            "expected_columns": (rec.last_test_result or {}).get("columns") or None,
        },
    }


def ensure_shadow_pipeline(db: Session, rec: N8nPipeline):
    """确保治理记录有对应的 v2_pipelines 影子行，并同步名称/定义。

    创建即登记（draft）——n8n 流水线从诞生起就出现在流水线列表里；
    数据管家只是 AI 编排工具，发布与否由编辑向导的 publish 决定
    （published 表示「可被调度」）。本函数不触碰影子行的 status。
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
    pl.updated_at = _now()
    return pl


def _remove_shadow_pipeline(db: Session, rec: N8nPipeline) -> None:
    """归档 → 影子流水线从列表移除（runs/versions 手动级联，与画布删除同一口径）。

    治理记录本身保留（status=archived）作为审计痕迹。
    """
    if not rec.pipeline_id:
        return
    from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineVersion

    db.query(PipelineRun).filter(PipelineRun.pipeline_id == rec.pipeline_id).delete()
    db.query(PipelineVersion).filter(PipelineVersion.pipeline_id == rec.pipeline_id).delete()
    pl = db.query(Pipeline).filter(Pipeline.id == rec.pipeline_id).first()
    if pl is not None:
        db.delete(pl)
    rec.pipeline_id = None
