"""
数据管家受限工具集 — LLM 与 n8n 交互的全部动词

治理边界（与本体 agent 同一哲学：agent 干活，人签字）：
  - 创建的 workflow 一律不激活；激活只发生在用户批准（approve）时
  - 修改已批准/待审批的 workflow → 自动停用并退回草稿（demote_on_edit）
  - 提交审批(submit_for_approval)是 agent 可代办的最远一步；批准/拒绝/
    归档/删除只存在于审批面板，工具集里没有这些动词
  - 凭据(credentials)无法经 API 创建 — 工具只能引用用户在 n8n 界面配好的凭据
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import httpx

from sqlalchemy.orm import Session

from app.settings.workflows.n8n_client import N8nApiError, N8nClient
from app.data_channel.steward import service
from app.data_channel.steward.models import N8nPipeline, STATUS_ARCHIVED
from app.data_channel.steward.node_catalog import CATEGORIES, find_nodes
from app.data_channel.steward.service import StewardError

logger = logging.getLogger(__name__)

_EXEC_SAMPLE_ROWS = 5   # get_execution 回填给 LLM 的末节点样例行数


TOOL_DEFS: list[dict] = [
    {
        "name": "steward_overview",
        "description": "查看数据管家全景：n8n 连接健康状况、受管流水线的状态统计。开场或用户问'现在有哪些流水线/什么状态'时先用它。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_pipelines",
        "description": "列出受管的 n8n 数据流水线记录（含审批状态），以及 n8n 实例上尚未纳管的工作流。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "按名称过滤（可选）"},
            },
        },
    },
    {
        "name": "get_workflow",
        "description": "查看一条受管流水线的完整 n8n workflow JSON（nodes/connections）与治理状态。修改任何工作流之前必须先看当前定义。",
        "parameters": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string", "description": "数据管家记录 id（list_pipelines 返回的 id）"},
            },
            "required": ["record_id"],
        },
    },
    {
        "name": "create_workflow",
        "description": "在 n8n 创建新的数据流水线工作流（自动保持未激活的草稿态）。nodes 省略 id/position 时会自动补全；connections 用节点 name 作为键。创建前先与用户确认设计。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工作流名称（同时作为平台流水线名）"},
                "description": {"type": "string", "description": "这条数据流水线的用途说明"},
                "nodes": {
                    "type": "array",
                    "description": "n8n 节点数组：[{name, type, typeVersion, parameters, position?}]",
                    "items": {"type": "object"},
                },
                "connections": {
                    "type": "object",
                    "description": 'n8n 连接表，键为源节点 name：{"节点A": {"main": [[{"node": "节点B", "type": "main", "index": 0}]]}}',
                },
                "settings": {"type": "object", "description": "workflow settings（可选）"},
            },
            "required": ["name", "nodes", "connections"],
        },
    },
    {
        "name": "update_workflow",
        "description": "修改受管流水线的 workflow 定义（整体替换 nodes/connections，先 get_workflow 取当前值改后回传）。注意：修改已批准的流水线会自动停用并退回草稿，需重新审批 — 动手前必须提醒用户。",
        "parameters": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "nodes": {"type": "array", "items": {"type": "object"}},
                "connections": {"type": "object"},
                "settings": {"type": "object"},
            },
            "required": ["record_id"],
        },
    },
    {
        "name": "adopt_workflow",
        "description": "把 n8n 实例上已存在（但未纳管）的工作流纳入数据管家治理，成为草稿记录。不会改变它在 n8n 侧的激活状态。",
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "n8n workflow id"},
                "description": {"type": "string"},
            },
            "required": ["workflow_id"],
        },
    },
    {
        "name": "check_workflow",
        "description": "按平台约定体检一条受管流水线：触发器、连接完整性、Webhook 约定、凭据引用。提交审批前先跑一遍并修复 error 级问题。",
        "parameters": {
            "type": "object",
            "properties": {"record_id": {"type": "string"}},
            "required": ["record_id"],
        },
    },
    {
        "name": "submit_for_approval",
        "description": "把草稿流水线提交给用户审批（批准后才会激活并进入平台流水线体系）。只有用户明确同意后才能调用。",
        "parameters": {
            "type": "object",
            "properties": {"record_id": {"type": "string"}},
            "required": ["record_id"],
        },
    },
    {
        "name": "list_executions",
        "description": "查看某条受管流水线最近的 n8n 执行记录（状态/起止时间），用于排查运行情况。",
        "parameters": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "status": {"type": "string", "enum": ["success", "error", "waiting"]},
                "limit": {"type": "integer"},
            },
            "required": ["record_id"],
        },
    },
    {
        "name": "get_execution",
        "description": "查看一次执行的细节：各节点产出行数、报错信息、末节点样例数据。定位失败原因用这个。",
        "parameters": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string"},
            },
            "required": ["execution_id"],
        },
    },
    {
        "name": "list_node_types",
        "description": f"查平台维护的 n8n 常用节点目录（type/typeVersion/关键参数模板）。拼节点前不确定类型或版本时先查这里。category 可选：{', '.join(CATEGORIES)}。",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": list(CATEGORIES)},
                "keyword": {"type": "string"},
            },
        },
    },
    {
        "name": "test_run",
        "description": "试跑一条受管流水线：临时激活 → POST 它的生产 Webhook → 等执行完成 → 返回行数/列/样例数据与执行状态，结束后恢复原激活状态，不写资产湖。用户说「测一下/看看数据对不对」就用这个（不要用 probe_url 去打 webhook，也不要让用户手动 curl）。失败时结合 get_execution 定位原因。",
        "parameters": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "payload": {"type": "object", "description": "可选：POST 给 Webhook 的 JSON 载荷"},
            },
            "required": ["record_id"],
        },
    },
    {
        "name": "probe_url",
        "description": "探测一个数据源 URL（唯一的对外探测手段）：发送 GET 请求，返回状态码、Content-Type；JSON 响应给出结构摘要与样例，HTML/文本给出标题与截断正文。用户给了网址/API 就先探测再设计流水线，不要凭空假设数据形态。只支持 http/https GET，响应会截断。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http/https 地址"},
                "headers": {
                    "type": "object",
                    "description": "可选请求头（如 Accept、Authorization——需用户在对话里提供的凭证）",
                },
            },
            "required": ["url"],
        },
    },
]

_PROBE_BODY_CAP = 200_000       # 读取响应体的字节上限
_PROBE_TEXT_SAMPLE = 2500       # HTML/文本回给 LLM 的样本长度
_PROBE_JSON_SAMPLE_ROWS = 3     # JSON 数组样例行数


def _json_shape(value: Any, depth: int = 0) -> Any:
    """把 JSON 压缩成"结构骨架"——保留键名与类型，砍掉海量数据。"""
    if depth >= 4:
        return "…"
    if isinstance(value, dict):
        return {k: _json_shape(v, depth + 1) for k, v in list(value.items())[:24]}
    if isinstance(value, list):
        head = _json_shape(value[0], depth + 1) if value else None
        return {"__type": f"array[{len(value)}]", "item": head}
    if isinstance(value, str):
        return value if len(value) <= 80 else value[:80] + "…"
    return value


def _normalize_nodes(nodes: list[dict]) -> list[dict]:
    """补全 LLM 常省略的字段：id / position / typeVersion / parameters。"""
    if not isinstance(nodes, list) or not nodes:
        raise StewardError("nodes 必须是非空数组。")
    normalized = []
    seen_names: set[str] = set()
    for i, raw in enumerate(nodes):
        if not isinstance(raw, dict):
            raise StewardError(f"nodes[{i}] 不是对象。")
        node = dict(raw)
        name = str(node.get("name") or "").strip()
        ntype = str(node.get("type") or "").strip()
        if not name or not ntype:
            raise StewardError(f"nodes[{i}] 缺少 name 或 type。")
        if name in seen_names:
            raise StewardError(f"节点 name 重复：「{name}」。n8n 要求节点名唯一（connections 以 name 为键）。")
        seen_names.add(name)
        node.setdefault("id", str(uuid.uuid4()))
        node.setdefault("typeVersion", 1)
        node.setdefault("parameters", {})
        if not node.get("position"):
            node["position"] = [260 * i, 300]
        normalized.append(node)
    return normalized


def _validate_connections(nodes: list[dict], connections: dict) -> None:
    if not isinstance(connections, dict):
        raise StewardError("connections 必须是对象（键为源节点 name）。")
    names = {n["name"] for n in nodes}
    for src, outs in connections.items():
        if src not in names:
            raise StewardError(f"connections 引用了不存在的源节点「{src}」。现有节点：{sorted(names)}")
        for branch in (outs or {}).values():
            for lane in branch or []:
                for target in lane or []:
                    tgt = (target or {}).get("node")
                    if tgt not in names:
                        raise StewardError(f"connections 引用了不存在的目标节点「{tgt}」。现有节点：{sorted(names)}")


def _execution_brief(e: dict) -> dict:
    return {
        "id": e.get("id"),
        "status": e.get("status"),
        "startedAt": e.get("startedAt"),
        "stoppedAt": e.get("stoppedAt"),
        "mode": e.get("mode"),
    }


class ToolRunner:
    """一次对话回合的工具执行器 — 记录触达的流水线，供前端刷新审批面板。"""

    def __init__(self, db: Session, user_id: str | None, conversation_id: str | None):
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.touched_pipeline_ids: list[str] = []
        self._client: N8nClient | None = None

    @property
    def client(self) -> N8nClient:
        if self._client is None:
            self._client = service.get_n8n_client(self.db)
        return self._client

    def _touch(self, record_id: str) -> None:
        if record_id not in self.touched_pipeline_ids:
            self.touched_pipeline_ids.append(record_id)

    def run(self, name: str, args: dict) -> dict:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return {"error": f"未知工具: {name}"}
        try:
            return handler(**(args or {}))
        except StewardError as e:
            return {"error": str(e)}
        except N8nApiError as e:
            return {"error": f"n8n API 错误 (HTTP {e.status_code}): {e.message}"}
        except TypeError as e:
            return {"error": f"参数不合法: {e}"}

    # ── 只读 ──────────────────────────────────────────────────────

    def tool_steward_overview(self) -> dict:
        status = service.n8n_config_status(self.db)
        overview: dict[str, Any] = {"n8n": status}
        if status["configured"] and status["enabled"]:
            try:
                self.client.test_connection()
                overview["n8n"]["reachable"] = True
            except Exception as e:  # noqa: BLE001
                overview["n8n"]["reachable"] = False
                overview["n8n"]["error"] = str(e)[:200]
        records = (self.db.query(N8nPipeline)
                   .filter(N8nPipeline.status != STATUS_ARCHIVED)
                   .order_by(N8nPipeline.updated_at.desc()).all())
        by_status: dict[str, int] = {}
        for r in records:
            by_status[r.status] = by_status.get(r.status, 0) + 1
        overview["pipelines"] = {
            "total": len(records),
            "byStatus": by_status,
            "recent": [{"id": r.id, "name": r.name, "status": r.status} for r in records[:10]],
        }
        return overview

    def tool_list_pipelines(self, keyword: str | None = None) -> dict:
        records = (self.db.query(N8nPipeline)
                   .filter(N8nPipeline.status != STATUS_ARCHIVED)
                   .order_by(N8nPipeline.updated_at.desc()).limit(50).all())
        if keyword:
            kw = keyword.lower()
            records = [r for r in records if kw in (r.name or "").lower()]
        managed_ids = {r.n8n_workflow_id for r in records}
        managed = [service.record_out(r) for r in records]

        unmanaged = []
        try:
            for wf in self.client.list_workflows(limit=50):
                if str(wf.get("id")) in managed_ids:
                    continue
                if keyword and keyword.lower() not in str(wf.get("name", "")).lower():
                    continue
                unmanaged.append({"workflowId": wf.get("id"), "name": wf.get("name"),
                                  "active": wf.get("active")})
        except StewardError as e:
            return {"managed": managed, "unmanagedError": str(e)}
        return {"managed": managed, "unmanaged": unmanaged[:20]}

    def tool_get_workflow(self, record_id: str) -> dict:
        rec = service.require_record(self.db, record_id)
        workflow = self.client.get_workflow(rec.n8n_workflow_id)
        # 顺手刷新快照，保持平台视图与 n8n 真身一致
        rec.workflow_snapshot = N8nClient.sanitize_workflow(workflow)
        self.db.commit()
        return {
            "record": service.record_out(rec, active=bool(workflow.get("active"))),
            "workflow": {
                "name": workflow.get("name"),
                "nodes": workflow.get("nodes"),
                "connections": workflow.get("connections"),
                "settings": workflow.get("settings"),
            },
        }

    def tool_list_node_types(self, category: str | None = None, keyword: str | None = None) -> dict:
        nodes = find_nodes(category=category, keyword=keyword)
        return {"nodes": nodes, "hint": "不在目录里的 n8n 节点也可以使用，但请优先用目录内节点以保证类型/版本正确。"}

    def tool_test_run(self, record_id: str, payload: dict | None = None) -> dict:
        from app.data_channel.steward.runner import test_run_workflow

        rec = service.require_record(self.db, record_id)
        result = test_run_workflow(self.db, rec, payload=payload)
        self._touch(rec.id)
        return result

    def tool_probe_url(self, url: str, headers: dict | None = None) -> dict:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise StewardError("只支持 http/https 地址。")
        # 云元数据端点一律拒绝（自托管平台允许探测局域网数据源，但不碰实例凭据面）
        if "169.254.169.254" in url or "metadata.google.internal" in url:
            raise StewardError("该地址不允许探测。")

        req_headers = {"User-Agent": "OntoPrompt-DataSteward/1.0", "Accept": "*/*"}
        for k, v in (headers or {}).items():
            if isinstance(k, str) and isinstance(v, str) and k.lower() != "host":
                req_headers[k] = v
        try:
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.get(url, headers=req_headers)
        except httpx.HTTPError as e:
            raise StewardError(f"探测失败：{e}") from e

        body = resp.content[:_PROBE_BODY_CAP]
        content_type = resp.headers.get("content-type", "")
        out: dict[str, Any] = {
            "status": resp.status_code,
            "finalUrl": str(resp.url),
            "contentType": content_type,
            "bytes": len(resp.content),
            "truncated": len(resp.content) > _PROBE_BODY_CAP,
        }

        text = body.decode(resp.encoding or "utf-8", errors="replace")
        parsed = None
        if "json" in content_type or text[:1] in ("{", "["):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
        if parsed is not None:
            out["kind"] = "json"
            out["shape"] = _json_shape(parsed)
            rows = parsed if isinstance(parsed, list) else None
            if rows is None and isinstance(parsed, dict):
                # 常见包裹形态：取第一个数组字段作为"行"
                rows = next((v for v in parsed.values() if isinstance(v, list)), None)
            if isinstance(rows, list) and rows:
                out["sampleRows"] = rows[:_PROBE_JSON_SAMPLE_ROWS]
            out["hint"] = "JSON 数据源：HTTP Request 节点直接拉取，再按 shape 用 Set/Code 整形成行。"
        else:
            out["kind"] = "html" if "html" in content_type or "<html" in text[:500].lower() else "text"
            m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
            if m:
                out["title"] = m.group(1).strip()[:200]
            # 粗剥 script/style 后取正文样本，给 LLM 判断页面形态
            stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
            stripped = re.sub(r"<[^>]+>", " ", stripped)
            stripped = re.sub(r"\s+", " ", stripped).strip()
            out["textSample"] = stripped[:_PROBE_TEXT_SAMPLE]
            if out["kind"] == "html":
                out["hint"] = ("HTML 页面：优先找它的 JSON API（浏览器开发者工具/常见 /api 路径），"
                               "实在没有再用 HTML 节点(extractHtmlContent) 按 CSS 选择器解析。")
        return out

    # ── 写入（治理规则内嵌） ──────────────────────────────────────

    def tool_create_workflow(self, name: str, nodes: list, connections: dict,
                             description: str | None = None,
                             settings: dict | None = None) -> dict:
        name = (name or "").strip()
        if not name:
            raise StewardError("name 不能为空。")
        dup = (self.db.query(N8nPipeline)
               .filter(N8nPipeline.name == name, N8nPipeline.status != STATUS_ARCHIVED).first())
        if dup:
            raise StewardError(f"已存在同名受管流水线「{name}」（记录 {dup.id}）。请换名称，或用 update_workflow 修改它。")

        norm_nodes = _normalize_nodes(nodes)
        _validate_connections(norm_nodes, connections or {})
        created = self.client.create_workflow({
            "name": name, "nodes": norm_nodes,
            "connections": connections or {}, "settings": settings or {},
        })
        rec = N8nPipeline(
            name=name,
            description=(description or "").strip(),
            n8n_workflow_id=str(created.get("id")),
            workflow_snapshot=N8nClient.sanitize_workflow(created),
            conversation_id=self.conversation_id,
            created_by=self.user_id,
        )
        self.db.add(rec)
        self.db.flush()
        service.ensure_shadow_pipeline(self.db, rec)  # 创建即出现在流水线列表（draft）
        self.db.commit()
        self._touch(rec.id)
        return {
            "record": service.record_out(rec, active=False),
            "notice": "已创建为草稿（n8n 侧未激活），流水线列表中已可见。完善后请用 check_workflow 体检，经用户同意再 submit_for_approval。",
        }

    def tool_update_workflow(self, record_id: str, name: str | None = None,
                             description: str | None = None,
                             nodes: list | None = None, connections: dict | None = None,
                             settings: dict | None = None) -> dict:
        rec = service.require_record(self.db, record_id)
        payload: dict[str, Any] = {}
        if name is not None and name.strip():
            payload["name"] = name.strip()
        if nodes is not None:
            payload["nodes"] = _normalize_nodes(nodes)
        if settings is not None:
            payload["settings"] = settings
        if connections is not None:
            base_nodes = payload.get("nodes")
            if base_nodes is None:
                base_nodes = (rec.workflow_snapshot or {}).get("nodes") or []
            _validate_connections(base_nodes, connections)
            payload["connections"] = connections
        elif payload.get("nodes") is not None:
            # 只换 nodes 不换 connections：校验旧连接仍指向存在的节点
            old_conns = (rec.workflow_snapshot or {}).get("connections") or {}
            _validate_connections(payload["nodes"], old_conns)

        demoted = False
        if payload:
            updated = self.client.update_workflow(rec.n8n_workflow_id, payload)
            demoted = service.demote_on_edit(self.db, rec, self.client)
            rec.workflow_snapshot = N8nClient.sanitize_workflow(updated)
            if payload.get("name"):
                rec.name = payload["name"]
        if description is not None:
            rec.description = description.strip()
        # 名称/定义同步到影子行（状态由 demote_on_edit 负责，此处不动）
        service.ensure_shadow_pipeline(self.db, rec)
        self.db.commit()
        self._touch(rec.id)
        out = {"record": service.record_out(rec)}
        if demoted:
            out["notice"] = "该流水线原本已批准/待审批：修改后已自动停用并退回草稿，需要重新提交审批。"
        return out

    def tool_adopt_workflow(self, workflow_id: str, description: str | None = None) -> dict:
        existing = (self.db.query(N8nPipeline)
                    .filter(N8nPipeline.n8n_workflow_id == str(workflow_id),
                            N8nPipeline.status != STATUS_ARCHIVED).first())
        if existing:
            raise StewardError(f"该工作流已被纳管（记录 {existing.id}，状态 {existing.status}）。")
        workflow = self.client.get_workflow(str(workflow_id))
        rec = N8nPipeline(
            name=str(workflow.get("name") or f"workflow-{workflow_id}"),
            description=(description or "").strip(),
            n8n_workflow_id=str(workflow_id),
            workflow_snapshot=N8nClient.sanitize_workflow(workflow),
            conversation_id=self.conversation_id,
            created_by=self.user_id,
        )
        self.db.add(rec)
        self.db.flush()
        service.ensure_shadow_pipeline(self.db, rec)  # 纳管即出现在流水线列表（draft）
        self.db.commit()
        self._touch(rec.id)
        out = {"record": service.record_out(rec, active=bool(workflow.get("active")))}
        if workflow.get("active"):
            out["notice"] = "注意：该工作流当前在 n8n 侧处于激活状态，但平台记录是草稿（未经审批）。建议尽快提交审批，或提醒用户在 n8n 中停用。"
        return out

    def tool_check_workflow(self, record_id: str) -> dict:
        rec = service.require_record(self.db, record_id)
        workflow = self.client.get_workflow(rec.n8n_workflow_id)
        rec.workflow_snapshot = N8nClient.sanitize_workflow(workflow)
        self.db.commit()

        issues: list[dict] = []
        nodes = workflow.get("nodes") or []
        connections = workflow.get("connections") or {}
        names = {n.get("name") for n in nodes}

        summary = service.summarize_workflow(workflow)
        if not nodes:
            issues.append({"level": "error", "message": "工作流没有任何节点。"})
        if not summary["has_trigger"]:
            issues.append({"level": "error", "message": "缺少触发器节点（Webhook 或 Schedule Trigger），无法提交审批。"})

        try:
            _validate_connections([{"name": n.get("name")} for n in nodes], connections)
        except StewardError as e:
            issues.append({"level": "error", "message": str(e)})

        # 非触发节点应有入边（孤儿节点通常是漏连线）
        targets: set[str] = set()
        for outs in connections.values():
            for branch in (outs or {}).values():
                for lane in branch or []:
                    for t in lane or []:
                        targets.add((t or {}).get("node"))
        for n in nodes:
            ntype = str(n.get("type", ""))
            if ntype.startswith(service.TRIGGER_TYPE_PREFIXES):
                continue
            if n.get("name") not in targets:
                issues.append({"level": "warning",
                               "message": f"节点「{n.get('name')}」没有任何入边，可能漏了连线。"})

        webhook_path = summary["webhook_path"]
        if webhook_path:
            wh = next(n for n in nodes if str(n.get("type")) == "n8n-nodes-base.webhook")
            params = wh.get("parameters") or {}
            if str(params.get("httpMethod", "GET")).upper() != "POST":
                issues.append({"level": "warning",
                               "message": "平台调度以 POST 触发 Webhook，建议 httpMethod 设为 POST。"})
            if params.get("responseMode") not in ("lastNode", "responseNode"):
                issues.append({"level": "warning",
                               "message": "Webhook responseMode 建议设为 lastNode：平台触发后可直接取回末节点数据入湖。"})
        else:
            issues.append({"level": "warning",
                           "message": "没有 Webhook 触发器：平台将无法主动调度这条流水线（只能由 n8n 内部定时自跑），运行产物也无法自动入湖。"})

        for n in nodes:
            if n.get("credentials"):
                cred_names = ", ".join(str((v or {}).get("name") or k) for k, v in n["credentials"].items())
                issues.append({"level": "info",
                               "message": f"节点「{n.get('name')}」引用凭据 [{cred_names}]——请确认已在 n8n 界面配置好，API 无法代建凭据。"})

        ok = not any(i["level"] == "error" for i in issues)
        self._touch(rec.id)
        return {"ok": ok, "issues": issues, "summary": summary}

    def tool_submit_for_approval(self, record_id: str) -> dict:
        rec = service.require_record(self.db, record_id)
        service.submit_for_approval(self.db, rec)
        self._touch(rec.id)
        return {"record": service.record_out(rec),
                "notice": "已提交审批。请用户在右侧「流水线审批」面板查看并批准/拒绝——批准后才会激活并进入平台流水线列表。"}

    # ── 执行观测 ──────────────────────────────────────────────────

    def tool_list_executions(self, record_id: str, status: str | None = None,
                             limit: int | None = None) -> dict:
        rec = service.require_record(self.db, record_id)
        executions = self.client.list_executions(
            workflow_id=rec.n8n_workflow_id, status=status, limit=limit or 10)
        return {"pipeline": rec.name,
                "executions": [_execution_brief(e) for e in executions]}

    def tool_get_execution(self, execution_id: str) -> dict:
        e = self.client.get_execution(str(execution_id), include_data=True)
        out: dict[str, Any] = _execution_brief(e)
        data = (e.get("data") or {})
        result_data = data.get("resultData") or {}
        out["lastNode"] = result_data.get("lastNodeExecuted")
        err = result_data.get("error") or {}
        if err:
            out["error"] = {
                "message": str(err.get("message", ""))[:400],
                "node": (err.get("node") or {}).get("name") if isinstance(err.get("node"), dict) else err.get("node"),
                "description": str(err.get("description", ""))[:400],
            }
        run_data = result_data.get("runData") or {}
        node_stats = {}
        for node_name, runs in run_data.items():
            try:
                items = ((runs[-1].get("data") or {}).get("main") or [[]])[0] or []
                node_stats[node_name] = len(items)
            except Exception:  # noqa: BLE001
                node_stats[node_name] = None
        out["nodeItemCounts"] = node_stats
        last = out.get("lastNode")
        if last and last in run_data:
            try:
                items = ((run_data[last][-1].get("data") or {}).get("main") or [[]])[0] or []
                out["lastNodeSample"] = [it.get("json") for it in items[:_EXEC_SAMPLE_ROWS]]
            except Exception:  # noqa: BLE001
                pass
        return out
