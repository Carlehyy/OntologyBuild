"""数据管家的编排参考库 — 纯只读上下文，压低 LLM 拼 n8n 定义时的高频错误。

三块：
  - EXPRESSION_REF: n8n 表达式 {{ }} 语法与常见坑
  - CODE_REF: Code 节点（JS/Python）写法与返回契约
  - PATTERNS: 验证过的数据流水线骨架（可直接抄 nodes/connections 再改）

由 n8n_reference(topic) 工具按需返回；系统提示只放一小段 primer 指路。
"""
from __future__ import annotations

EXPRESSION_REF = """n8n 表达式语法（字段值里写 `={{ ... }}` 才会被求值；不带 = 前缀就是字面量）：
- `={{ $json.foo }}`          取当前 item 的字段 foo
- `={{ $json["带 空格"] }}`   字段名有空格/中文用方括号
- `={{ $node["HTTP Request"].json.data }}`  取指定上游节点的输出
- `={{ $now.toISO() }}` / `={{ $today }}`   当前时间 / 今天（Luxon DateTime）
- `={{ $json.items.map(i => i.id).join(",") }}`  表达式里可写 JS 片段
- `={{ $runIndex }}` / `={{ $itemIndex }}`  循环/分批时的索引
常见坑：
- 忘了 `=` 前缀 → 整串被当字面量字符串，最典型的“取不到值”。
- 在 Set 节点里给字段赋动态值，value 必须是 `={{ $json.x }}` 形式。
- 跨节点引用要用节点“显示名”（name），改名后表达式会断。"""

CODE_REF = """Code 节点（n8n-nodes-base.code, typeVersion 2）：
- JS 模式 mode=runOnceForAllItems（默认，推荐）：
  拿到 `items`（数组），必须 `return` 一个 `[{json: {...}}, ...]` 数组。
  例：`return items.map(i => ({ json: { id: i.json.id, name: i.json.title } }));`
- JS 模式 mode=runOnceForEachItem：对每个 item 跑一次，用 `$input.item.json`，
  `return { json: {...} };`（返回单个对象）。
- 取输入：`$input.all()` 全部 items；`$input.first().json`；`$json` 当前 item。
- Python 模式（beta, language=python）：用 `_input.all()`、`_json`，只能用标准库，
  返回 `[{'json': {...}}]`。能用 JS 就别用 Python。
返回契约：末节点若是 Code，必须吐“一行一个 item、json 里字段扁平”的结构，平台才能入湖。"""


# ── 可复用骨架（nodes/connections 可直接抄，改 url/query/字段即可） ──
# 平台约定：Webhook 触发（POST, responseMode=lastNode），末节点输出扁平行数据。

PATTERNS: list[dict] = [
    {
        "name": "rest_api_to_lake",
        "title": "REST API → 整形 → 入湖",
        "when": "最常用：从一个 JSON REST 接口拉数据，整理成表格行，供平台入湖。",
        "nodes": [
            {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
             "parameters": {"httpMethod": "POST", "path": "ob-<短名>", "responseMode": "lastNode"}},
            {"name": "拉取", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
             "parameters": {"method": "GET", "url": "https://api.example.com/items"}},
            {"name": "整形", "type": "n8n-nodes-base.set", "typeVersion": 3.4,
             "parameters": {"mode": "manual", "assignments": {"assignments": [
                 {"id": "f1", "name": "id", "type": "string", "value": "={{ $json.id }}"},
                 {"id": "f2", "name": "标题", "type": "string", "value": "={{ $json.title }}"}]}}},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "拉取", "type": "main", "index": 0}]]},
            "拉取": {"main": [[{"node": "整形", "type": "main", "index": 0}]]},
        },
    },
    {
        "name": "paginated_api",
        "title": "分页 REST API → 汇总 → 整形",
        "when": "接口分页返回时：用 HTTP Request 内置分页（response 里有 next 游标/页码）。",
        "nodes": [
            {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
             "parameters": {"httpMethod": "POST", "path": "ob-<短名>", "responseMode": "lastNode"}},
            {"name": "分页拉取", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
             "parameters": {"method": "GET", "url": "https://api.example.com/items",
                            "sendQuery": True, "queryParameters": {"parameters": [
                                {"name": "page", "value": "={{ $pageCount + 1 }}"}]},
                            "options": {"pagination": {"pagination": {
                                "paginationMode": "responseContainsNextURL",
                                "nextURL": "={{ $response.body.next }}"}}}}},
            {"name": "整形", "type": "n8n-nodes-base.set", "typeVersion": 3.4,
             "parameters": {"mode": "manual", "assignments": {"assignments": [
                 {"id": "f1", "name": "id", "type": "string", "value": "={{ $json.id }}"}]}}},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "分页拉取", "type": "main", "index": 0}]]},
            "分页拉取": {"main": [[{"node": "整形", "type": "main", "index": 0}]]},
        },
    },
    {
        "name": "html_scrape",
        "title": "网页 HTML → CSS 提取 → 整形",
        "when": "数据源只有网页、没有 JSON API：抓 HTML 再按 CSS 选择器提取。",
        "nodes": [
            {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
             "parameters": {"httpMethod": "POST", "path": "ob-<短名>", "responseMode": "lastNode"}},
            {"name": "抓页面", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
             "parameters": {"method": "GET", "url": "https://example.com/list"}},
            {"name": "提取", "type": "n8n-nodes-base.html", "typeVersion": 1.2,
             "parameters": {"operation": "extractHtmlContent", "extractionValues": {"values": [
                 {"key": "标题", "cssSelector": "h2.title"},
                 {"key": "链接", "cssSelector": "a", "returnValue": "attribute", "attribute": "href"}]}}},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "抓页面", "type": "main", "index": 0}]]},
            "抓页面": {"main": [[{"node": "提取", "type": "main", "index": 0}]]},
        },
    },
    {
        "name": "db_query",
        "title": "数据库查询 → 行输出",
        "when": "从 Postgres/MySQL 取数（需用户先在 n8n 界面配好数据库凭据）。",
        "nodes": [
            {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
             "parameters": {"httpMethod": "POST", "path": "ob-<短名>", "responseMode": "lastNode"}},
            {"name": "查询", "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "parameters": {"operation": "executeQuery",
                            "query": "SELECT id, name, created_at FROM orders WHERE created_at > now() - interval '1 day'"}},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "查询", "type": "main", "index": 0}]]},
        },
        "note": "查询节点需引用一个数据库凭据（credentials）——先用 check_credentials 看实例有没有，没有就让用户去 n8n 配。",
    },
    {
        "name": "code_transform",
        "title": "拉取 → Code 自定义整形",
        "when": "整形逻辑复杂（嵌套展开、字段计算、合并），Set 不够用时上 Code 节点。",
        "nodes": [
            {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
             "parameters": {"httpMethod": "POST", "path": "ob-<短名>", "responseMode": "lastNode"}},
            {"name": "拉取", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
             "parameters": {"method": "GET", "url": "https://api.example.com/data"}},
            {"name": "整形", "type": "n8n-nodes-base.code", "typeVersion": 2,
             "parameters": {"mode": "runOnceForAllItems",
                            "jsCode": "return items.flatMap(i => (i.json.records || []).map(r => ({ json: { id: r.id, value: r.v } })));"}},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "拉取", "type": "main", "index": 0}]]},
            "拉取": {"main": [[{"node": "整形", "type": "main", "index": 0}]]},
        },
    },
]

_TOPICS = {"expressions", "code", "patterns"}


def reference(topic: str) -> dict:
    """按 topic 返回参考内容；patterns 直接给可抄的 nodes/connections 骨架。"""
    topic = (topic or "").strip().lower()
    if topic in ("expression", "expressions", "expr"):
        return {"topic": "expressions", "text": EXPRESSION_REF}
    if topic in ("code", "code_node", "js", "javascript"):
        return {"topic": "code", "text": CODE_REF}
    if topic in ("pattern", "patterns", "template", "templates"):
        return {"topic": "patterns",
                "patterns": [{"name": p["name"], "title": p["title"], "when": p["when"],
                              "nodes": p["nodes"], "connections": p["connections"],
                              **({"note": p["note"]} if p.get("note") else {})}
                             for p in PATTERNS]}
    return {"error": f"未知 topic「{topic}」。可选：{', '.join(sorted(_TOPICS))}。"}
