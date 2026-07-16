"""
数据管家 — 服务层

集中三件事，router / toolkit / runner 都只经这里触碰状态：
  1. 从系统设置加载 n8n 客户端（解密 API Key，未配置给出可操作的报错）
  2. 受管记录的生命周期辅助（创建/纳管登记影子行、归档、发布封版守卫）
  3. 发布与启停时的 n8n 远端状态同步（发布是单向封版，数据管家只编排草稿）
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.workflow_config import WorkflowConfig
from app.settings.workflows.n8n_client import N8nClient, enforce_n8n_url_policy
from app.config import settings
from app.shared.encryption import decrypt
from app.data_channel.steward.models import N8nPipeline, STATUS_ARCHIVED, STATUS_DRAFT

#: 受管工作流在 definition 里的引擎标记 — 现有画布/运行路径据此绕行
ENGINE_N8N = "n8n"


class StewardError(Exception):
    """业务可读的错误 — router 转 4xx，toolkit 转工具错误回给 LLM 自我修正。"""


class ValidationAttestationError(StewardError):
    """试跑/字段校验凭证与当前 n8n 真身不再匹配。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json_hash(value) -> str:
    """稳定 JSON SHA-256：字典键顺序不影响凭证，数组顺序仍保留语义。"""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
    try:
        api_url = enforce_n8n_url_policy(
            cfg.api_url, environment=settings.environment)
    except ValueError as exc:
        raise StewardError(f"n8n 地址不符合当前环境安全策略：{exc}") from exc
    return N8nClient(api_url=api_url, api_key=api_key,
                     timeout_seconds=cfg.timeout_seconds or 10)


def bootstrap_blank_workflow(db: Session, name: str, description: str = "",
                             user_id: str | None = None) -> N8nPipeline:
    """流水线列表「新建 n8n 流水线」：后台自动在 n8n 创建骨架工作流并纳管为草稿。

    骨架 = Webhook 触发器（平台调度约定：path=ob-*、POST、responseMode=lastNode）
    → NoOp 输出节点。用户随后在数据管家对话中完善取数逻辑，在编辑向导中
    发布后才可被调度。
    """
    import uuid as _uuid

    name = (name or "").strip()
    if not name:
        raise StewardError("流水线名称不能为空。")
    dup = (db.query(N8nPipeline)
           .filter(N8nPipeline.name == name, N8nPipeline.status != STATUS_ARCHIVED).first())
    if dup:
        raise StewardError(f"已存在同名受管流水线「{name}」，请换一个名称。")

    client = get_n8n_client(db)
    # 路径本身就是生产 webhook 的 bearer-like 能力地址：随机后缀至少 128 bit，
    # 不能再使用 6/10 位短 token（可枚举且会随流水线数量增加碰撞概率）。
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:24]
    token = secrets.token_hex(16)
    webhook_path = f"ob-{slug}-{token}" if slug else f"ob-{token}"
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

_WEBHOOK_TYPE = "n8n-nodes-base.webhook"
_FORBIDDEN_MANAGED_TRIGGER_TYPES = {
    "n8n-nodes-base.scheduleTrigger",
    "n8n-nodes-base.cron",
    "n8n-nodes-base.manualTrigger",
}
_STATIC_WEBHOOK_PATH_RE = re.compile(r"^[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*$")
_HIGH_ENTROPY_PATH_RE = re.compile(
    r"(?:^|[-/])(?:[0-9a-f]{32,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


def validate_managed_workflow_contract(
    workflow: dict | None,
    *,
    allow_legacy_webhook_path: bool = False,
) -> dict:
    """Validate the deliberately narrow n8n profile the platform can ingest safely.

    n8n stores flow edges as ``connections[source].main[output_index]``.  The
    platform supports one enabled POST Webhook root and one reachable terminal
    data node.  Drafts may be arbitrary while edited, but preview/publish call
    this guard and fail closed before any activation or ingestion.
    """
    raw_nodes = (workflow or {}).get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise StewardError("工作流没有任何节点，不能作为平台托管流水线。")

    enabled_nodes = [node for node in raw_nodes
                     if isinstance(node, dict) and not bool(node.get("disabled"))]
    if not enabled_nodes:
        raise StewardError("工作流没有任何已启用节点，不能发布。")

    by_name: dict[str, dict] = {}
    node_ids: set[str] = set()
    for node in enabled_nodes:
        name = str(node.get("name") or "").strip()
        node_id = str(node.get("id") or "").strip()
        if not name or not node_id:
            raise StewardError("每个已启用节点都必须具有非空且稳定的 id/name。")
        if name in by_name:
            raise StewardError(f"节点名称「{name}」重复；n8n execution 的 runData 按名称索引，无法安全定位输出。")
        if node_id in node_ids:
            raise StewardError(f"节点 id「{node_id}」重复，无法形成不可变发布契约。")
        by_name[name] = node
        node_ids.add(node_id)

    webhooks = [node for node in enabled_nodes if str(node.get("type")) == _WEBHOOK_TYPE]
    if len(webhooks) != 1:
        raise StewardError(
            f"平台托管流水线必须且只能有 1 个已启用 Webhook 触发器，当前为 {len(webhooks)} 个。")
    webhook = webhooks[0]
    webhook_name = str(webhook["name"])
    params = webhook.get("parameters") or {}
    if str(params.get("httpMethod") or "").upper() != "POST":
        raise StewardError("平台固定以 POST 调度 n8n，Webhook 的 httpMethod 必须明确设为 POST。")
    if str(params.get("responseMode") or "") != "lastNode":
        raise StewardError("Webhook responseMode 必须设为 lastNode，平台不支持立即响应、流式响应或 Respond 节点旁路。")

    auth = str(params.get("authentication") or "none").strip().lower()
    if auth not in ("", "none") or bool(webhook.get("credentials")):
        raise StewardError(
            "当前平台尚未配置托管 Webhook 凭据，Webhook authentication 必须为 none；"
            "系统不会伪造 HMAC 或假装已发送认证信息。")

    path = str(params.get("path") or "").strip()
    if not path or not _STATIC_WEBHOOK_PATH_RE.fullmatch(path):
        raise StewardError("Webhook path 必须是非空静态安全路径，不能包含 :variable、模板、查询串或首尾斜杠。")
    if not allow_legacy_webhook_path and not _HIGH_ENTROPY_PATH_RE.search(path):
        raise StewardError("Webhook path 必须以至少 128 bit 的随机 token（32 位十六进制或 UUID）结尾。")

    for node in enabled_nodes:
        node_type = str(node.get("type") or "")
        if node_type in _FORBIDDEN_MANAGED_TRIGGER_TYPES:
            raise StewardError(
                f"平台托管流水线禁止已启用的 {node_type.rsplit('.', 1)[-1]}；运行计划必须由数据任务池统一管理。")
        if node is not webhook and node_type.lower().endswith("trigger"):
            raise StewardError(f"平台托管流水线存在额外触发器「{node.get('name')}」，只能保留唯一 Webhook 根节点。")

    enabled_names = set(by_name)
    adjacency: dict[str, set[str]] = {name: set() for name in enabled_names}
    incoming: set[str] = set()
    connections = (workflow or {}).get("connections") or {}
    if not isinstance(connections, dict):
        raise StewardError("workflow.connections 必须是对象。")

    for source, groups in connections.items():
        source = str(source)
        if source not in by_name:
            # Disabled nodes may remain on the canvas, but they must not participate
            # in the managed main flow.
            if isinstance(groups, dict) and groups.get("main"):
                raise StewardError(f"连接源节点「{source}」不存在或已禁用，不能参与托管执行流。")
            continue
        if not isinstance(groups, dict):
            raise StewardError(f"节点「{source}」的 connections 结构非法。")
        lanes = groups.get("main")
        if lanes is None or lanes == []:
            continue
        if not isinstance(lanes, list):
            raise StewardError(f"节点「{source}」的 main 输出必须是分支数组。")
        for lane_index, lane in enumerate(lanes):
            if not isinstance(lane, list) or not lane:
                raise StewardError(
                    f"节点「{source}」的 main[{lane_index}] 是悬空分支；每条声明分支都必须汇入唯一输出节点。")
            for edge in lane:
                if not isinstance(edge, dict) or not edge.get("node"):
                    raise StewardError(f"节点「{source}」的 main[{lane_index}] 包含非法连接。")
                if str(edge.get("type") or "main") != "main":
                    raise StewardError(f"节点「{source}」的托管执行流只支持 main 连接。")
                target = str(edge["node"])
                if target not in by_name:
                    raise StewardError(f"连接目标节点「{target}」不存在或已禁用。")
                adjacency[source].add(target)
                incoming.add(target)

    roots = enabled_names - incoming
    if roots != {webhook_name}:
        extra = "、".join(sorted(roots - {webhook_name})) or "（Webhook 不是根节点）"
        raise StewardError(f"托管执行流只能有唯一 Webhook 根节点；检测到额外根节点：{extra}。")

    reachable: set[str] = set()
    stack = [webhook_name]
    while stack:
        current = stack.pop()
        if current in reachable:
            continue
        reachable.add(current)
        stack.extend(adjacency[current] - reachable)
    if reachable != enabled_names:
        missing = "、".join(sorted(enabled_names - reachable))
        raise StewardError(f"存在无法从 Webhook 到达的已启用节点：{missing}。")

    sinks = [name for name in reachable if not adjacency[name]]
    if len(sinks) != 1 or sinks[0] == webhook_name:
        raise StewardError(
            f"托管执行流必须汇入 1 个且仅 1 个末端输出节点，当前为 {len(sinks)} 个："
            f"{'、'.join(sorted(sinks)) or '无'}。")
    output = by_name[sinks[0]]
    return {
        "webhook_node_id": str(webhook["id"]),
        "webhook_node_name": webhook_name,
        "webhook_path": path,
        "output_node_id": str(output["id"]),
        "output_node_name": str(output["name"]),
    }


def find_webhook_path(workflow: dict | None) -> str | None:
    """宽松视图：仅当恰有一个 enabled Webhook 时返回 path。

    发布/试跑必须使用 ``validate_managed_workflow_contract``；本函数只用于草稿
    列表展示，不能充当发布资格证明。
    """
    webhooks = [node for node in (workflow or {}).get("nodes") or []
                if isinstance(node, dict)
                and not bool(node.get("disabled"))
                and str(node.get("type", "")) == _WEBHOOK_TYPE]
    if len(webhooks) != 1:
        return None
    path = (webhooks[0].get("parameters") or {}).get("path")
    return str(path) if path else None


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


def refresh_draft_snapshot(db: Session, rec: N8nPipeline, workflow: dict) -> tuple[dict, bool]:
    """Refresh the mutable working snapshot without touching a published release.

    Read-only detail, health and credential checks used to overwrite
    ``workflow_snapshot`` unconditionally.  Once published, that field is the
    only retained topology evidence for legacy releases and must be immutable.
    """
    snapshot = N8nClient.sanitize_workflow(workflow)
    if shadow_status(db, rec) == "published":
        return snapshot, False
    rec.workflow_snapshot = snapshot
    return snapshot, True


def require_unpublished(db: Session, rec: N8nPipeline) -> None:
    """编排守卫：发布是不可逆封版，已发布记录永远不能回到可编排状态。"""
    if shadow_status(db, rec) == "published":
        raise StewardError(
            f"流水线「{rec.name}」已发布（封版），不能修改编排。"
            "如需调整，请新建一条流水线并在验证通过后发布；旧版本可停用或归档。")


def require_orchestrable(db: Session, rec: N8nPipeline, client: N8nClient) -> None:
    """编排守卫（数据管家仅有的写权限边界）：只允许编排「未发布 且 未启用」的流水线。

    - 未发布：影子流水线 status != published（发布后不可逆、不可再编排）
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
            "如果它尚未发布，请先在 n8n 界面停用；如果已经发布，请新建流水线完成变更。")


# ── 发布与启停的 n8n 侧动作 ────────────────────────────────────

_REVISION_FIELDS = ("versionId", "activeVersionId", "updatedAt")
# inactive 草稿在部分 n8n 公共 API 版本中不会返回 activeVersionId。草稿一致性
# 由当前编辑版本 + 更新时间 + canonical workflow snapshot 共同证明；activeVersionId
# 只在远端确实返回时作为额外校验项，不能成为用户配置字段契约的前置条件。
_REQUIRED_REVISION_FIELDS = ("versionId", "updatedAt")


def _require_complete_revision(workflow: dict, *, context: str) -> dict:
    revision = N8nClient.workflow_revision(workflow)
    missing = [field for field in _REQUIRED_REVISION_FIELDS if not revision.get(field)]
    if missing:
        raise StewardError(
            f"{context}无法确认当前 n8n 工作流版本，平台已安全中止操作。"
            "请稍后重试；若持续失败，请由管理员检查 n8n 公共 API 兼容性。")
    return revision


def workflow_validation_evidence(workflow: dict, *, context: str) -> dict:
    """Build the workflow half of a publish attestation from the live n8n row."""
    return {
        "revision": _require_complete_revision(workflow, context=context),
        "snapshot_hash": canonical_json_hash(N8nClient.sanitize_workflow(workflow)),
    }


def require_workflow_validation_evidence(
    expected: dict | None,
    workflow: dict,
    *,
    context: str,
) -> dict:
    """Fail closed when the workflow changed after the reviewed dry-run."""
    expected = expected or {}
    expected_revision = expected.get("revision") or expected.get("workflow_revision") or {}
    expected_snapshot_hash = (
        expected.get("snapshot_hash") or expected.get("workflow_snapshot_hash") or ""
    )
    if not expected_revision or not expected_snapshot_hash:
        raise ValidationAttestationError(
            f"{context}平台内部一致性检查尚未完成，操作已安全中止。"
        )
    current = workflow_validation_evidence(workflow, context=context)
    drift = [
        field for field in _REQUIRED_REVISION_FIELDS
        if current["revision"].get(field) != expected_revision.get(field)
    ]
    # activeVersionId 对 inactive 草稿不是稳定公共字段；只有预览时和当前读取
    # 都返回它时才作为附加漂移证据。
    if (expected_revision.get("activeVersionId")
            and current["revision"].get("activeVersionId")
            and current["revision"]["activeVersionId"]
            != expected_revision["activeVersionId"]):
        drift.append("activeVersionId")
    if current["snapshot_hash"] != expected_snapshot_hash:
        drift.append("workflowSnapshot")
    if drift:
        raise ValidationAttestationError(
            f"{context}检测到 n8n 工作流在试跑校验后发生漂移（{', '.join(drift)}）。"
            "平台已自动使旧校验结果失效。请返回执行预览，平台会重新完成一致性校验。"
        )
    return current


def validation_attestation(rec: N8nPipeline) -> dict | None:
    value = (rec.last_test_result or {}).get("validation_attestation")
    return value if isinstance(value, dict) else None


def invalidate_validation_attestation(rec: N8nPipeline) -> None:
    """Remove only the publish proof; retain the latest preview sample for diagnostics."""
    state = dict(rec.last_test_result or {})
    state.pop("validation_attestation", None)
    rec.last_test_result = state


def _set_remote_active(
    rec: N8nPipeline,
    client: N8nClient,
    *,
    enabled: bool,
    context: str,
) -> tuple[bool, dict]:
    """Set and *confirm* the remote active flag, returning its prior state.

    n8n activation/deactivation is an external side effect rather than part of
    our database transaction.  Every lifecycle transition therefore reads the
    state before the change and reads it again afterwards; callers can use the
    returned prior state to compensate a later local commit failure.
    """
    was_active: bool | None = None
    try:
        before = client.get_workflow(rec.n8n_workflow_id)
        was_active = bool(before.get("active"))
        if was_active != enabled:
            if enabled:
                client.activate_workflow(rec.n8n_workflow_id)
            else:
                client.deactivate_workflow(rec.n8n_workflow_id)
        confirmed = client.get_workflow(rec.n8n_workflow_id)
        if bool(confirmed.get("active")) != enabled:
            target = "active" if enabled else "inactive"
            raise RuntimeError(f"n8n 接口返回后工作流仍未处于 {target} 状态")
    except Exception as exc:  # noqa: BLE001 -- remote errors become a business error
        action = "启用" if enabled else "停用"
        # A timeout can be raised after n8n has applied the switch.  When the
        # pre-state is known, make a best-effort *confirmed* rollback before
        # returning an error so local state is not silently left split-brain.
        if was_active is not None and was_active != enabled:
            try:
                current = client.get_workflow(rec.n8n_workflow_id)
                if bool(current.get("active")) != was_active:
                    if was_active:
                        client.activate_workflow(rec.n8n_workflow_id)
                    else:
                        client.deactivate_workflow(rec.n8n_workflow_id)
                restored = client.get_workflow(rec.n8n_workflow_id)
                if bool(restored.get("active")) != was_active:
                    raise RuntimeError("补偿后远端状态仍不一致")
            except Exception as compensation_exc:  # noqa: BLE001
                raise StewardError(
                    f"{context}：{action} n8n 工作流失败（{exc}），且恢复原状态失败"
                    f"（{compensation_exc}）。请立即人工核对远端状态。") from exc
        raise StewardError(f"{context}：{action} n8n 工作流失败：{exc}") from exc
    return was_active, confirmed


def set_remote_active_for_preview(rec: N8nPipeline, client: N8nClient, *,
                                  enabled: bool) -> dict:
    """Confirmed temporary switch used by a lock-protected manual preview."""
    _was_active, confirmed = _set_remote_active(
        rec,
        client,
        enabled=enabled,
        context="执行预览临时启用" if enabled else "执行预览恢复停用",
    )
    return confirmed


def activate_for_publish(
    db: Session,
    rec: N8nPipeline,
    client: N8nClient,
    *,
    keep_active: bool = False,
    validation_attestation: dict | None = None,
) -> dict:
    """发布 n8n 流水线：拉最新快照 → 校验 Webhook → 激活并锁定 revision。

    状态翻转/版本快照由 publish 端点的通用封版逻辑承担；这里只做 n8n
    特有的部分。返回的 revision 必须固化到影子 definition；是否在发布
    完成后保持 active 由 ``keep_active`` 决定，发布与启用是两个独立状态。
    激活后任何校验失败都会先补偿停用，避免留下「平台草稿、n8n 已激活」。
    """
    workflow = client.get_workflow(rec.n8n_workflow_id)
    rec.workflow_snapshot = N8nClient.sanitize_workflow(workflow)
    require_workflow_validation_evidence(
        validation_attestation, workflow, context="发布前")
    contract_before_activation = validate_managed_workflow_contract(workflow)
    if bool(workflow.get("active")):
        raise StewardError(
            "该草稿工作流已在 n8n 侧激活，平台无法确认其生命周期来源。"
            "请先在 n8n 停用，再由平台执行发布。")

    activated = False
    try:
        # Mark the side-effect attempt before the call: a transport error can
        # arrive after n8n already applied activation, so failure handling must
        # still attempt and confirm compensation.
        activated = True
        _was_active, published_workflow = _set_remote_active(
            rec, client, enabled=True, context="发布")
        published_contract = validate_managed_workflow_contract(published_workflow)
        if published_contract != contract_before_activation:
            raise StewardError("n8n 工作流在激活期间发生拓扑变化，无法形成稳定发布契约。")
        # 校验和激活之间仍可能被 n8n UI 并发修改；远端副作用完成后再核对一次，
        # 不匹配则走下方补偿停用，不能发布未经试跑审核的新版本。
        require_workflow_validation_evidence(
            validation_attestation, published_workflow, context="发布激活后")
        revision = _require_complete_revision(published_workflow, context="发布后")
        if (revision.get("activeVersionId")
                and revision["versionId"] != revision["activeVersionId"]):
            raise StewardError(
                "n8n 当前编辑版本与实际激活版本不一致"
                f"（versionId={revision['versionId']}，"
                f"activeVersionId={revision['activeVersionId']}），无法形成可靠发布快照。")
        if not bool(published_workflow.get("active")):
            raise StewardError("n8n 激活接口返回后工作流仍未处于 active 状态，发布已中止。")
        final_workflow = published_workflow
        if not keep_active:
            _was_active, final_workflow = _set_remote_active(
                rec, client, enabled=False, context="发布但不启用")
            final_revision = _require_complete_revision(final_workflow, context="发布停用后")
            drift = [field for field in _REQUIRED_REVISION_FIELDS
                     if final_revision.get(field) != revision.get(field)]
            if (revision.get("activeVersionId")
                    and final_revision.get("activeVersionId")
                    and final_revision["activeVersionId"]
                    != revision["activeVersionId"]):
                drift.append("activeVersionId")
            if drift:
                raise StewardError(
                    "n8n 工作流在发布后停用期间发生版本变化"
                    f"（{', '.join(drift)}），无法形成稳定发布快照。")
        rec.workflow_snapshot = N8nClient.sanitize_workflow(final_workflow)
        return revision
    except Exception as exc:  # noqa: BLE001 — 远端错误统一转换为业务错误
        if activated:
            try:
                _set_remote_active(rec, client, enabled=False, context="发布失败补偿")
            except StewardError as compensation_exc:
                raise StewardError(
                    f"发布失败（{exc}），且补偿停用 n8n 工作流也失败（{compensation_exc}）。"
                    "远端可能仍处于激活状态，请立即人工核对。") from exc
        if isinstance(exc, StewardError):
            raise
        raise StewardError(f"激活 n8n 工作流失败：{exc}") from exc


def compensate_failed_publish(rec: N8nPipeline, client: N8nClient) -> None:
    """本地发布事务失败后，严格撤销刚完成的远端激活。"""
    try:
        _set_remote_active(rec, client, enabled=False, context="发布事务补偿")
    except StewardError as exc:
        raise StewardError(
            f"平台发布事务失败，且补偿停用 n8n 工作流失败：{exc}。"
            "远端可能仍处于激活状态，请立即人工核对。") from exc


def require_published_revision(
    pl,
    remote_workflow: dict,
    *,
    require_active: bool = True,
) -> dict:
    """运行前确认远端仍是发布时审核过的同一 revision。"""
    published = (((pl.definition or {}).get("n8n") or {}).get("revision") or {})
    missing_published = [
        field for field in _REQUIRED_REVISION_FIELDS if not published.get(field)
    ]
    if missing_published:
        raise StewardError(
            "该流水线的发布快照缺少 n8n revision 信息，不能安全运行。"
            "请停用并归档该历史版本，然后新建流水线替代。")
    current = _require_complete_revision(remote_workflow, context="运行前")
    drift = [
        field for field in _REQUIRED_REVISION_FIELDS
        if current.get(field) != published.get(field)
    ]
    if (published.get("activeVersionId") and current.get("activeVersionId")
            and current["activeVersionId"] != published["activeVersionId"]):
        drift.append("activeVersionId")
    if drift:
        details = "，".join(
            f"{field}: 发布={published.get(field)} / 当前={current.get(field)}"
            for field in drift)
        raise StewardError(
            f"检测到 n8n 工作流在平台发布后发生版本漂移（{details}）。"
            "为避免按旧数据契约执行新逻辑，本次运行已中止；请停用并归档旧版本，再新建流水线替代。")
    if require_active and not bool(remote_workflow.get("active")):
        raise StewardError("n8n 工作流已在远端被停用，与平台发布快照不一致，本次运行已中止。")
    return current


def resolve_published_runtime_release(pl, rec: N8nPipeline,
                                      remote_workflow: dict, *,
                                      require_active: bool = True) -> dict:
    """Resolve the immutable n8n runtime contract, including safe legacy releases.

    Releases created before managed_contract/revision were introduced cannot be
    sent back to draft now that publish is irreversible.  They are accepted only
    when the live writable workflow is byte-for-byte equivalent (canonical JSON)
    to the snapshot retained at publish time.  In that narrow case we can derive
    the unique output and current active revision without guessing topology.

    New releases always take the strict path and must carry both artifacts in the
    pipeline definition.  The legacy path deliberately permits old short webhook
    paths; new publish validation continues to require 128-bit paths.
    """
    if (pl.status or "") != "published":
        raise StewardError("该 n8n 流水线尚未发布，不能被调度运行。请先完成发布。")

    n8n_def = ((pl.definition or {}).get("n8n") or {})
    managed_contract = n8n_def.get("managed_contract") or {}
    published_revision = n8n_def.get("revision") or {}
    complete_contract = bool(
        managed_contract.get("webhook_path")
        and managed_contract.get("output_node_name")
        and managed_contract.get("output_node_id")
    )
    complete_revision = all(
        published_revision.get(field) for field in _REQUIRED_REVISION_FIELDS
    )
    if complete_contract and complete_revision:
        require_published_revision(pl, remote_workflow, require_active=require_active)
        return {
            "managed_contract": managed_contract,
            "revision": published_revision,
            "legacy_compatibility": False,
        }

    published_snapshot = rec.workflow_snapshot or {}
    if not published_snapshot:
        raise StewardError(
            "该历史发布版本缺少可核验的 n8n 工作流快照，平台已拒绝猜测输出节点。"
            "请停用并归档该版本，然后新建流水线替代。")
    live_snapshot = N8nClient.sanitize_workflow(remote_workflow)
    if canonical_json_hash(live_snapshot) != canonical_json_hash(published_snapshot):
        raise StewardError(
            "该历史发布版本缺少完整运行契约，且 n8n 当前定义与发布时保留的快照不一致。"
            "为避免执行未经审核的逻辑，本次运行已中止；请停用并归档旧版本，再新建流水线替代。")

    contract = validate_managed_workflow_contract(
        published_snapshot, allow_legacy_webhook_path=True)
    revision = _require_complete_revision(remote_workflow, context="历史发布版本兼容校验时")
    if (revision.get("activeVersionId")
            and revision["versionId"] != revision["activeVersionId"]):
        raise StewardError(
            "该历史发布版本的 n8n 当前编辑版本与激活版本不一致，不能安全执行。"
            "请先停用旧版本并新建流水线替代。")
    if require_active and not bool(remote_workflow.get("active")):
        raise StewardError("n8n 工作流已在远端被停用，与平台启用状态不一致，本次运行已中止。")
    return {
        "managed_contract": contract,
        "revision": revision,
        "legacy_compatibility": True,
    }


def set_published_enabled(
    pl,
    rec: N8nPipeline,
    client: N8nClient,
    *,
    enabled: bool,
) -> bool:
    """Apply a published pipeline's enable switch to n8n and verify revision.

    Returns the remote active state observed before the transition.  Enabling
    is fail-closed on revision drift both before and after activation; disabling
    remains available even if the workflow drifted so an unsafe workflow can
    always be stopped.
    """
    try:
        before = client.get_workflow(rec.n8n_workflow_id)
    except Exception as exc:  # noqa: BLE001
        raise StewardError(f"读取 n8n 工作流状态失败，启停操作已中止：{exc}") from exc
    if enabled:
        require_published_revision(pl, before, require_active=False)
    was_active, confirmed = _set_remote_active(
        rec, client, enabled=enabled, context="启用流水线" if enabled else "停用流水线")
    if enabled:
        try:
            require_published_revision(pl, confirmed, require_active=True)
        except StewardError as exc:
            if not was_active:
                try:
                    _set_remote_active(rec, client, enabled=False, context="启用失败补偿")
                except StewardError as compensation_exc:
                    raise StewardError(
                        f"启用校验失败（{exc}），且补偿停用失败（{compensation_exc}）。"
                        "请立即人工核对远端状态。") from exc
            raise
    rec.workflow_snapshot = N8nClient.sanitize_workflow(confirmed)
    return was_active


def restore_remote_active(
    pl,
    rec: N8nPipeline,
    client: N8nClient,
    *,
    enabled: bool,
    context: str,
    verify_revision: bool = True,
) -> None:
    """Restore a previously observed remote state after a local DB failure."""
    _was_active, confirmed = _set_remote_active(
        rec, client, enabled=enabled, context=context)
    if enabled and verify_revision:
        require_published_revision(pl, confirmed, require_active=True)


def archive(db: Session, rec: N8nPipeline, client: N8nClient | None,
            delete_workflow: bool = False) -> N8nPipeline:
    """归档 = 停用 workflow + 保留不可变的平台审计链。

    两个入口（数据管家面板「归档」、流水线列表「删除」）共用；被调度任务
    或同步链引用时拒绝。影子 Pipeline、发布版本和运行记录均保留，影子行
    标为 archived + disabled，并从默认列表隐藏。
    """
    _reject_if_shadow_referenced(db, rec)
    if client is None:
        raise StewardError("归档前必须连接 n8n 并确认工作流已停用；当前无法取得 n8n 客户端。")
    if delete_workflow:
        raise StewardError("归档只退出平台调度并保留审计，不删除 n8n 工作流。")

    try:
        was_active, _confirmed = _set_remote_active(
            rec, client, enabled=False, context="归档")
    except StewardError as exc:
        raise StewardError(f"归档前无法确认 n8n 工作流已停用：{exc}") from exc
    rec.status = STATUS_ARCHIVED
    _archive_shadow_pipeline(db, rec)
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        try:
            pl = shadow_pipeline(db, rec)
            restore_remote_active(
                pl, rec, client, enabled=was_active, context="归档事务补偿",
                verify_revision=False)
        except StewardError as compensation_exc:
            raise StewardError(
                f"平台归档事务失败（{exc}），且恢复 n8n 原状态失败（{compensation_exc}）。"
                "请立即人工核对。") from exc
        raise StewardError(f"平台归档事务失败，n8n 原状态已恢复：{exc}") from exc
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


# ── 影子流水线（v2_pipelines, engine=n8n） ────────────────────────

def _shadow_definition(rec: N8nPipeline, *, published_revision: dict | None = None) -> dict:
    managed_contract = (validate_managed_workflow_contract(rec.workflow_snapshot)
                        if published_revision is not None else None)
    return {
        # nodes/edges 留空数组：画布等旧代码读取时不会崩，但引擎标记让它们绕行
        "engine": ENGINE_N8N,
        "nodes": [],
        "edges": [],
        "n8n": {
            "steward_id": rec.id,
            "workflow_id": rec.n8n_workflow_id,
            "webhook_path": (managed_contract or {}).get("webhook_path")
                            or find_webhook_path(rec.workflow_snapshot),
            # 发布时固化唯一入口和唯一输出的 node id/name；runner 只认该输出，
            # 不再把 n8n 的 lastNodeExecuted 当成未经审核的动态选择器。
            "managed_contract": managed_contract,
            # 只在发布事务中写入；draft 编排不会伪造或沿用旧 revision。
            "revision": published_revision,
            # 发布版本同时保留“哪次试跑输出 + 哪套字段定义 + 哪个 workflow
            # revision”通过校验，版本审计不依赖可清理的 dry-run 对象。
            "validation_attestation": (
                validation_attestation(rec) if published_revision is not None else None
            ),
            # 发布契约：以最近一次试跑的列集合为期望列，运行期资产湖闸门
            # 据此做漂移检测（警告不阻断——湖中主键契约才是硬校验）
            "expected_columns": (rec.last_test_result or {}).get("columns") or None,
        },
    }


def ensure_shadow_pipeline(db: Session, rec: N8nPipeline,
                           *, published_revision: dict | None = None):
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
            created_by=rec.created_by,
        )
        db.add(pl)
        db.flush()
        rec.pipeline_id = pl.id
    # 修复旧版本创建的无 owner 影子行；n8n 治理记录 owner 是可证明的来源，
    # 不能因 Pipeline.created_by=NULL 而让任意 editor 接管他人的 workflow。
    if not pl.created_by and rec.created_by:
        pl.created_by = rec.created_by
    pl.name = rec.name
    pl.description = rec.description or pl.description
    pl.definition = _shadow_definition(rec, published_revision=published_revision)
    pl.updated_at = _now()
    return pl


def _archive_shadow_pipeline(db: Session, rec: N8nPipeline) -> None:
    """Archive the shadow without deleting versions, runs, or its identity."""
    if not rec.pipeline_id:
        return
    from app.models.v2.pipeline import Pipeline

    pl = db.query(Pipeline).filter(Pipeline.id == rec.pipeline_id).first()
    if pl is not None:
        pl.status = STATUS_ARCHIVED
        pl.enabled = False
        pl.updated_at = _now()
