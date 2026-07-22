"""
把「已开放」的接口，通过一个**统一的 MCP 服务**对外暴露，供其它电脑上的 Agent 调用。

设计（参考 Skill 的渐进式披露）：
不再为每个接口生成一个独立工具，而是只暴露**两个稳定工具**，无论开放多少个接口，
Agent 看到的工具数量都不变：

  1) list_open_interfaces  —— 发现层。返回当前所有已开放接口的清单（id、名称、用途
     说明、方法、URL、可用参数）。Agent 先调它，了解“有哪些接口、怎么调、要什么参数”。
  2) call_open_interface   —— 执行层。按 id 调用某个已开放接口并返回响应；可用 query /
     body 覆盖默认参数。

这样做的好处：
- 工具面恒定且极小（2 个），上下文友好；接口增减只改变 list 的返回内容，不改变工具签名。
- “开放”用的就是接口的 open_enabled 标记，网页里勾选即时生效（每次调用都实时查库）。
- 执行仍走平台已有的 executor，自动带 W3 登录态、自动透明重登。
- 传输用 Streamable HTTP（无状态 + JSON 响应），挂在主服务的 /mcp 路径上。
"""
import json
import re
from urllib.parse import urlsplit, urlunsplit

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from . import config, db, executor

server = Server(config.MCP_SERVER_NAME)


def tool_name(name: str, iid: int) -> str:
    """把接口名转成可读引用 <slug>_<id>，仅用于展示/日志；调用以数字 id 为准。"""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip()).strip("_").lower()
    if not slug:
        slug = "iface"
    return f"{slug[:48]}_{iid}"


def _published() -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM interfaces WHERE mcp_enabled = 1 ORDER BY group_name, sort_order, id"
        ).fetchall()
    return [dict(r) for r in rows]


def published_tools() -> list[dict]:
    """给前端 /api/mcp/info 用：已发布为独立 MCP 工具的接口及其可读引用。"""
    return [{"id": r["id"], "name": r["name"], "tool_name": tool_name(r["name"], r["id"])}
            for r in _published()]


def _open_ifaces() -> list[dict]:
    """已加入「开放接口清单」的接口（供统一 MCP 服务 list_open_interfaces / call_open_interface 使用）。"""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM interfaces WHERE open_enabled = 1 ORDER BY group_name, sort_order, id"
        ).fetchall()
    return [dict(r) for r in rows]


def _row_to_iface(row: dict) -> dict:
    return {
        "id": row["id"], "name": row["name"], "method": row["method"], "url": row["url"],
        "query_params": json.loads(row["query_params"]), "headers": json.loads(row["headers"]),
        "body_type": row["body_type"], "body_content": row["body_content"],
        "use_w3": bool(row["use_w3"]),
    }


def _catalog_entry(row: dict) -> dict:
    """单个已开放接口在「清单」里对 Agent 暴露的视图。"""
    qp = json.loads(row["query_params"])
    parsed_url = urlsplit(row["url"])
    return {
        "id": row["id"],
        "name": row["name"],
        "description": (row.get("description") or "").strip(),
        "group": row["group_name"],
        "method": row["method"],
        # 清单只用于发现，不披露 URL 查询值（其中可能包含访问令牌）。
        "url": urlunsplit(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                "",
                "",
            )
        ),
        # 只暴露“接口当前已配置了哪些 query 键”，作为可覆盖参数的提示
        "query_params": [p["key"] for p in qp if p.get("key")],
        "body_type": row["body_type"],
        "needs_w3": bool(row["use_w3"]),
    }


# ── 两个统一工具 ───────────────────────────────────────────
_LIST_DESC = (
    "列出当前已加入开放接口清单的全部接口及其调用方式（id、名称、用途说明、HTTP 方法、URL、"
    "可覆盖的 query 参数、Body 类型）。在调用任何具体接口之前，应先调用本工具，"
    "据此选择目标接口的 id 并了解它需要的参数。支持可选的 keyword 关键字过滤。"
)

_CALL_DESC = (
    "调用一个已开放的接口，并返回其 HTTP 响应（状态码、耗时、响应体）。"
    "interface_id 取自 list_open_interfaces 返回的 id。可用 query 追加/覆盖查询参数、"
    "用 body 覆盖请求体；不传则按接口已保存的配置发送。调用会自动带上平台维护的登录态。"
)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_open_interfaces",
            description=_LIST_DESC,
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "可选。按接口名称 / 分组 / URL 模糊过滤。",
                    },
                },
            },
        ),
        types.Tool(
            name="call_open_interface",
            description=_CALL_DESC,
            inputSchema={
                "type": "object",
                "properties": {
                    "interface_id": {
                        "type": "integer",
                        "description": "目标接口的 id（来自 list_open_interfaces）。",
                    },
                    "query": {
                        "type": "object",
                        "description": "可选。追加或覆盖查询参数（键值对，值为字符串）。",
                        "additionalProperties": {"type": "string"},
                    },
                    "body": {
                        "type": "string",
                        "description": "可选。覆盖请求体内容（按接口已配置的 Body 类型发送）。",
                    },
                },
                "required": ["interface_id"],
            },
        ),
    ]


def _handle_list(keyword) -> list[types.TextContent]:
    kw = (keyword or "").strip().lower()
    items = []
    for r in _open_ifaces():
        if kw:
            hay = f"{r['name']} {r['group_name']} {r['url']} {r['method']}".lower()
            if kw not in hay:
                continue
        items.append(_catalog_entry(r))

    payload = {
        "count": len(items),
        "interfaces": items,
        "usage": "用 call_open_interface 并传 interface_id 调用其中某个接口。",
    }
    if not items:
        payload["usage"] = "当前没有已开放的接口。请在平台「开放接口」里勾选要开放的接口。"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return [types.TextContent(type="text", text=text)]


def _apply_overrides(iface: dict, args: dict) -> dict:
    q = args.get("query")
    if isinstance(q, dict) and q:
        merged = {p["key"]: dict(p) for p in iface["query_params"] if p.get("key")}
        for k, v in q.items():
            merged[k] = {"key": k, "value": str(v)}
        iface["query_params"] = list(merged.values())

    b = args.get("body")
    if isinstance(b, str) and b != "":
        iface["body_content"] = b
    return iface


def _truncate_for_mcp(text: str) -> str:
    limit = config.MCP_MAX_BODY_CHARS
    if limit and len(text) > limit:
        return text[:limit] + f"\n\n…（响应过大，已截断至 {limit} 字符。如需完整内容，请在平台界面查看该接口的调用历史）"
    return text


async def _handle_call(args: dict) -> list[types.TextContent]:
    iid = args.get("interface_id")
    try:
        iid = int(iid)
    except (TypeError, ValueError):
        return [types.TextContent(type="text",
                text="参数 interface_id 必须是整数（取自 list_open_interfaces 返回的 id）。")]

    # 安全关键点：调用时实时校验“仍处于开放状态”，不信任 Agent 之前看到的清单。
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM interfaces WHERE id = ? AND open_enabled = 1", (iid,)
        ).fetchone()
    if row is None:
        return [types.TextContent(type="text",
                text=f"接口 {iid} 不存在或未开放。请重新调用 list_open_interfaces 获取最新的开放清单。")]

    iface = _apply_overrides(_row_to_iface(dict(row)), args)

    # executor 是同步阻塞的，放到线程里跑，避免卡住事件循环
    result = await anyio.to_thread.run_sync(
        lambda: executor.run_interface(
            iface, executor.RequestOverrides(source="mcp_open")
        )
    )

    if result.get("status_code") is None and result.get("error"):
        text = f"请求失败：{result['error']}"
    else:
        head = f"HTTP {result.get('status_code')} · {result.get('elapsed_ms')}ms"
        if result.get("relogin"):
            head += " · 已自动重新登录"
        if result.get("error"):
            head += f" · 注意：{result['error']}"
        body = _truncate_for_mcp(result.get("response_body") or "(空响应体)")
        text = head + "\n\n" + body
    return [types.TextContent(type="text", text=text)]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    args = arguments or {}
    if name == "list_open_interfaces":
        return _handle_list(args.get("keyword"))
    if name == "call_open_interface":
        return await _handle_call(args)
    return [types.TextContent(type="text", text=f"未知工具：{name}")]


# Streamable HTTP 传输。无状态 + JSON 响应，便于跨机访问与排查。
# Host / Origin 必须显式进入允许清单，避免浏览器侧 DNS rebinding。
def _build_session_manager(app):
    return StreamableHTTPSessionManager(
        app=app,
        json_response=True,
        stateless=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(config.MCP_ALLOWED_HOSTS),
            allowed_origins=list(config.MCP_ALLOWED_ORIGINS),
        ),
    )


session_manager = _build_session_manager(server)


async def handle_mcp(scope, receive, send):
    await session_manager.handle_request(scope, receive, send)


# ═══════════════════════════════════════════════════════════
#  系统管理 MCP —— 供其它 Agent 管理接口与分组
#  独立端点 /mcp/system，独立令牌 SYSTEM_MCP_TOKEN。
# ═══════════════════════════════════════════════════════════

from .routers.interfaces import (
    InterfaceIn,
    create_interface as _create_interface,
    delete_group as _delete_group,
    delete_interface as _delete_interface,
    update_interface as _update_interface,
    _row_to_dict as _iface_row_to_dict,
)

sys_server = Server(config.MCP_SERVER_NAME + "-system")


def _sys_published_groups() -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT group_name, COUNT(*) AS cnt FROM interfaces "
            "GROUP BY group_name ORDER BY group_name"
        ).fetchall()
    return [{"name": r["group_name"] or "默认分组", "count": r["cnt"]} for r in rows]


def _sys_interfaces_in_group(group: str | None) -> list[dict]:
    with db.get_conn() as conn:
        if group:
            if group == "默认分组":
                group = ""
            rows = conn.execute(
                "SELECT * FROM interfaces WHERE group_name = ? ORDER BY sort_order, id",
                (group,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM interfaces ORDER BY group_name, sort_order, id"
            ).fetchall()
    return [_iface_row_to_dict(r) for r in rows]


@sys_server.list_tools()
async def sys_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_groups",
            description="列出所有分组及其接口数量。用于了解当前有哪些分组。",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="list_interfaces",
            description="列出接口清单。可传 group 筛选指定分组（不传则列出全部）。返回 id、名称、分组、方法、URL、描述、Body 类型等。",
            inputSchema={
                "type": "object",
                "properties": {
                    "group": {"type": "string", "description": "可选。按分组名筛选。"},
                },
            },
        ),
        types.Tool(
            name="get_interface",
            description="获取单个接口的完整信息（含 Headers、Query Params、Body 等）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "接口 id。"},
                },
                "required": ["id"],
            },
        ),
        types.Tool(
            name="create_interface",
            description="新建接口。name 和 url 必填，其余可选（method 默认 GET，use_w3 默认 false）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "接口名称。"},
                    "url": {"type": "string", "description": "请求 URL。"},
                    "method": {"type": "string", "description": "HTTP 方法，默认 GET。"},
                    "group": {"type": "string", "description": "分组名（不传则归入默认分组）。"},
                    "description": {"type": "string", "description": "用途说明。"},
                    "query_params": {"type": "object", "description": "查询参数，键值对如 {\"page\":\"1\"}。", "additionalProperties": {"type": "string"}},
                    "headers": {"type": "object", "description": "自定义请求头，键值对如 {\"X-Token\":\"abc\"}。", "additionalProperties": {"type": "string"}},
                    "body_type": {"type": "string", "description": "Body 类型：none/json/form/raw，默认 none。"},
                    "body_content": {"type": "string", "description": "Body 内容。"},
                    "use_w3": {"type": "boolean", "description": "是否启用 W3 登录，默认 false。"},
                    "parameters": {
                        "type": "array",
                        "description": "可选的动态参数契约（name/location/value_type/required/dynamic/sensitive）。",
                        "items": {"type": "object"},
                    },
                },
                "required": ["name", "url"],
            },
        ),
        types.Tool(
            name="update_interface",
            description="更新已有接口。只需传要修改的字段，未传的字段保持不变。",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "接口 id。"},
                    "name": {"type": "string"},
                    "url": {"type": "string"},
                    "method": {"type": "string"},
                    "group": {"type": "string"},
                    "description": {"type": "string"},
                    "query_params": {"type": "object", "additionalProperties": {"type": "string"}},
                    "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                    "body_type": {"type": "string"},
                    "body_content": {"type": "string"},
                    "use_w3": {"type": "boolean"},
                    "parameters": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["id"],
            },
        ),
        types.Tool(
            name="delete_interface",
            description="删除指定接口（含其调用历史）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "接口 id。"},
                },
                "required": ["id"],
            },
        ),
        types.Tool(
            name="delete_group",
            description="删除指定分组（分组下所有接口移入默认分组）。默认分组不可删除。",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "分组名。"},
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="call_interface",
            description="调用指定接口并返回 HTTP 响应（状态码、耗时、响应体）。不要求接口已开放，可直接按 id 调用任意已保存的接口。",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "接口 id。"},
                    "query": {
                        "type": "object",
                        "description": "可选。追加或覆盖查询参数（键值对，值为字符串）。",
                        "additionalProperties": {"type": "string"},
                    },
                    "body": {
                        "type": "string",
                        "description": "可选。覆盖请求体内容。",
                    },
                },
                "required": ["id"],
            },
        ),
    ]


@sys_server.call_tool()
async def sys_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    args = arguments or {}

    if name == "list_groups":
        groups = _sys_published_groups()
        text = json.dumps({"groups": groups}, ensure_ascii=False, indent=2)
        return [types.TextContent(type="text", text=text)]

    if name == "list_interfaces":
        group = (args.get("group") or "").strip() or None
        ifaces = _sys_interfaces_in_group(group)
        summary = [{"id": it["id"], "name": it["name"], "group": it["group_name"] or "默认分组",
                     "method": it["method"], "url": it["url"], "description": it["description"]}
                   for it in ifaces]
        text = json.dumps({"count": len(summary), "interfaces": summary}, ensure_ascii=False, indent=2)
        return [types.TextContent(type="text", text=text)]

    if name == "get_interface":
        iid = int(args.get("id", 0))
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (iid,)).fetchone()
        if not row:
            return [types.TextContent(type="text", text=json.dumps({"error": "接口不存在"}, ensure_ascii=False))]
        text = json.dumps(_iface_row_to_dict(row), ensure_ascii=False, indent=2)
        return [types.TextContent(type="text", text=text)]

    if name == "create_interface":
        from .routers.interfaces import KV
        qp = args.get("query_params") or {}
        hd = args.get("headers") or {}
        body = InterfaceIn(
            name=args.get("name", "未命名接口"),
            url=args.get("url", ""),
            method=args.get("method", "GET").upper(),
            group_name=args.get("group", ""),
            description=args.get("description", ""),
            body_type=args.get("body_type", "none"),
            body_content=args.get("body_content", ""),
            use_w3=args.get("use_w3", False),
            query_params=[KV(key=str(k), value=str(v)) for k, v in qp.items()],
            headers=[KV(key=str(k), value=str(v)) for k, v in hd.items()],
            parameter_schema=args.get("parameters") or [],
        )
        text = json.dumps(_create_interface(body), ensure_ascii=False, indent=2)
        return [types.TextContent(type="text", text=text)]

    if name == "update_interface":
        iid = int(args.get("id", 0))
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (iid,)).fetchone()
            if not row:
                return [types.TextContent(type="text", text=json.dumps({"error": "接口不存在"}, ensure_ascii=False))]
            merged = _iface_row_to_dict(row)
        for field in ("name", "url", "method", "description", "body_type", "body_content"):
            if field in args:
                merged[field] = args[field]
        if "group" in args:
            merged["group_name"] = args["group"]
        if "use_w3" in args:
            merged["use_w3"] = bool(args["use_w3"])
        if "query_params" in args:
            merged["query_params"] = [
                {"key": str(key), "value": str(value)}
                for key, value in (args["query_params"] or {}).items()
            ]
        if "headers" in args:
            merged["headers"] = [
                {"key": str(key), "value": str(value)}
                for key, value in (args["headers"] or {}).items()
            ]
        if "parameters" in args:
            merged["parameter_schema"] = args.get("parameters") or []
        updated = _update_interface(iid, InterfaceIn(**merged))
        text = json.dumps(updated, ensure_ascii=False, indent=2)
        return [types.TextContent(type="text", text=text)]

    if name == "delete_interface":
        iid = int(args.get("id", 0))
        with db.get_conn() as conn:
            row = conn.execute("SELECT id FROM interfaces WHERE id = ?", (iid,)).fetchone()
        if not row:
            return [types.TextContent(type="text", text=json.dumps({"error": "接口不存在"}, ensure_ascii=False))]
        _delete_interface(iid)
        return [types.TextContent(type="text", text=json.dumps({"ok": True, "deleted_id": iid}, ensure_ascii=False))]

    if name == "delete_group":
        gname = (args.get("name") or "").strip()
        if not gname:
            return [types.TextContent(type="text", text=json.dumps({"error": "分组名不能为空"}, ensure_ascii=False))]
        if gname == "默认分组":
            return [types.TextContent(type="text", text=json.dumps({"error": "默认分组不可删除"}, ensure_ascii=False))]
        from .routers.interfaces import DeleteGroupBody
        result = _delete_group(DeleteGroupBody(group_name=gname))
        count = result["count"]
        return [types.TextContent(type="text", text=json.dumps({"ok": True, "deleted_group": gname, "moved_count": count}, ensure_ascii=False))]

    if name == "call_interface":
        iid = int(args.get("id", 0))
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (iid,)).fetchone()
        if not row:
            return [types.TextContent(type="text", text=json.dumps({"error": f"接口 {iid} 不存在"}, ensure_ascii=False))]
        iface = _apply_overrides(_row_to_iface(dict(row)), args)
        result = await anyio.to_thread.run_sync(
            lambda: executor.run_interface(
                iface, executor.RequestOverrides(source="mcp_system")
            )
        )
        if result.get("status_code") is None and result.get("error"):
            text = f"请求失败：{result['error']}"
        else:
            head = f"HTTP {result.get('status_code')} · {result.get('elapsed_ms')}ms"
            if result.get("relogin"):
                head += " · 已自动重新登录"
            if result.get("error"):
                head += f" · 注意：{result['error']}"
            body = _truncate_for_mcp(result.get("response_body") or "(空响应体)")
            text = head + "\n\n" + body
        return [types.TextContent(type="text", text=text)]

    return [types.TextContent(type="text", text=f"未知工具：{name}")]


sys_session_manager = _build_session_manager(sys_server)


def reset_session_managers():
    """Return fresh managers for every application lifespan."""
    global session_manager, sys_session_manager
    session_manager = _build_session_manager(server)
    sys_session_manager = _build_session_manager(sys_server)
    return session_manager, sys_session_manager


async def handle_mcp_system(scope, receive, send):
    await sys_session_manager.handle_request(scope, receive, send)
