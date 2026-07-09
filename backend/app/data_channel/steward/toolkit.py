"""
数据管家受限工具集 — LLM 与 n8n 交互的全部动词

职权收敛到两件事（除此之外没有任何写权限）：
  1. 新建流水线：create_pipeline 只收名称+描述，在 n8n 建 Webhook→输出 骨架并
     登记为未发布流水线（等价于流水线列表「新建 n8n 流水线」），不激活。
  2. 辅助编排：update_workflow 只能改「未发布 且 未启用」的流水线定义。

其余工具都是只读支撑（查看/体检/探测数据源/节点目录），不改变流水线的生命
周期与激活状态。治理边界（与本体 agent 同一哲学：agent 干活，人签字）：
  - 创建的 workflow 一律不激活；激活只发生在用户于流水线编辑向导「发布」时
    ——发布是生命周期的唯一入口，工具集里没有这个动词。
  - 已发布或已启用的流水线：编排一律拒绝（须先在编辑向导撤回发布 / 在 n8n 停用）。
  - 纳管、试跑、执行观测、归档/删除都不再是管家职权（归档在流水线列表操作）。
  - 凭据(credentials)无法经 API 创建 — 工具只能引用用户在 n8n 界面配好的凭据。
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


TOOL_DEFS: list[dict] = [
    {
        "name": "steward_overview",
        "description": "查看数据管家全景：n8n 连接健康状况、受管流水线的状态统计。开场或用户问'现在有哪些流水线/什么状态'时先用它。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_pipelines",
        "description": "列出受管的 n8n 数据流水线记录（含发布状态：未发布/已发布）。要编排哪条流水线时先用它拿到 record_id。",
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
        "name": "create_pipeline",
        "description": (
            "新建一条 n8n 数据流水线：只需名称与描述。"
            "后台自动在 n8n 建好 Webhook→输出 的骨架工作流并登记为未发布流水线"
            "（等价于用户在流水线列表点「新建流水线 → n8n → 填名称/描述」）——不激活、不调度。"
            "创建后用 get_workflow 看骨架，再用 update_workflow 逐步补全取数与整形节点。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "流水线名称（同时作为 n8n 工作流名）"},
                "description": {"type": "string", "description": "这条数据流水线的用途说明（可选）"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "update_workflow",
        "description": "编排/修改受管流水线的 workflow 定义（整体替换 nodes/connections，先 get_workflow 取当前值改后回传）。只能修改「未发布 且 未启用」的流水线；已发布或 n8n 侧已启用的会被拒绝，需引导用户先在流水线列表的编辑向导中「撤回发布」或在 n8n 停用。",
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
        "name": "check_workflow",
        "description": "只读体检一条受管流水线是否符合平台约定：触发器、连接完整性、Webhook 约定、凭据引用。编排完/让用户去发布前自检一遍，修复 error 级问题（不改动工作流、不激活）。",
        "parameters": {
            "type": "object",
            "properties": {"record_id": {"type": "string"}},
            "required": ["record_id"],
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


class ToolRunner:
    """一次对话回合的工具执行器 — 记录触达的流水线，供前端刷新受管流水线面板。"""

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
        recent = []
        for r in records:
            pub = service.shadow_status(self.db, r)
            by_status[pub] = by_status.get(pub, 0) + 1
            if len(recent) < 10:
                recent.append({"id": r.id, "name": r.name, "pipelineStatus": pub})
        overview["pipelines"] = {
            "total": len(records),
            "byStatus": by_status,   # published=已发布可调度 / draft=未发布
            "recent": recent,
        }
        return overview

    def tool_list_pipelines(self, keyword: str | None = None) -> dict:
        # 只列受管流水线；不再枚举 n8n 上未纳管的工作流（纳管已不在管家职权内）
        records = (self.db.query(N8nPipeline)
                   .filter(N8nPipeline.status != STATUS_ARCHIVED)
                   .order_by(N8nPipeline.updated_at.desc()).limit(50).all())
        if keyword:
            kw = keyword.lower()
            records = [r for r in records if kw in (r.name or "").lower()]
        return {"managed": [service.record_out(self.db, r) for r in records]}

    def tool_get_workflow(self, record_id: str) -> dict:
        rec = service.require_record(self.db, record_id)
        workflow = self.client.get_workflow(rec.n8n_workflow_id)
        # 顺手刷新快照，保持平台视图与 n8n 真身一致
        rec.workflow_snapshot = N8nClient.sanitize_workflow(workflow)
        self.db.commit()
        return {
            "record": service.record_out(self.db, rec, active=bool(workflow.get("active"))),
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

    def tool_create_pipeline(self, name: str, description: str | None = None) -> dict:
        # 与流水线列表「新建 n8n 流水线」完全同源：建 Webhook→输出 骨架、未激活、
        # 登记为未发布流水线。任意节点的搭建交给随后的 update_workflow。
        rec = service.bootstrap_blank_workflow(
            self.db, name, description or "", user_id=self.user_id)
        rec.conversation_id = self.conversation_id
        self.db.commit()
        self._touch(rec.id)
        return {
            "record": service.record_out(self.db, rec, active=False),
            "notice": ("已新建为未发布流水线（含 Webhook→输出 骨架，n8n 侧未激活），流水线列表已可见。"
                       "接下来用 get_workflow 看骨架、update_workflow 补全取数与整形节点；"
                       "编排、体检通过后，提醒用户到流水线列表的编辑向导中「发布并启用」。"),
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

        if payload:
            # 编排守卫：写 n8n 之前拦——只放行「未发布 且 未启用」的流水线
            service.require_orchestrable(self.db, rec, self.client)
            updated = self.client.update_workflow(rec.n8n_workflow_id, payload)
            rec.workflow_snapshot = N8nClient.sanitize_workflow(updated)
            if payload.get("name"):
                rec.name = payload["name"]
        if description is not None:
            rec.description = description.strip()
        # 名称/定义同步到影子行（不触碰发布状态）
        service.ensure_shadow_pipeline(self.db, rec)
        self.db.commit()
        self._touch(rec.id)
        return {"record": service.record_out(self.db, rec)}

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
            issues.append({"level": "error", "message": "缺少触发器节点（Webhook 或 Schedule Trigger），无法发布。"})

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
