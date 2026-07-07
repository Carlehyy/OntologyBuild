"""
n8n 常用节点目录 — 数据管家的确定性节点知识

n8n 公共 REST API 不暴露节点目录（/node-types 是内部 API），这里维护一份
面向"数据流水线"场景的精选清单，注入系统提示 + list_node_types 工具，
把 LLM 拼 workflow JSON 时的节点类型/typeVersion 幻觉压到最低。

typeVersion 取 2024 年后 n8n 1.x 已长期稳定的版本 — 新实例全部兼容。
"""
from __future__ import annotations

CATEGORIES = ("trigger", "core", "database", "file", "http", "notify")

# type / typeVersion / 用途 / 关键参数（parameters 里的字段）
NODE_CATALOG: list[dict] = [
    # ── 触发器 ──
    {"type": "n8n-nodes-base.webhook", "typeVersion": 2, "category": "trigger",
     "name": "Webhook", "usage": "HTTP 触发（平台调度数据流水线的标准入口）",
     "key_params": {"httpMethod": "POST", "path": "ob-<流水线短名>", "responseMode": "lastNode"}},
    {"type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "category": "trigger",
     "name": "Schedule Trigger", "usage": "定时触发（在 n8n 内部自主调度）",
     "key_params": {"rule": {"interval": [{"field": "hours", "hoursInterval": 6}]}}},
    {"type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "category": "trigger",
     "name": "Manual Trigger", "usage": "仅手动测试用，正式流水线不要以它为唯一触发器",
     "key_params": {}},

    # ── 数据获取 ──
    {"type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "category": "http",
     "name": "HTTP Request", "usage": "调用任意 REST API 拉取/回传数据",
     "key_params": {"method": "GET", "url": "https://…", "sendQuery": True,
                    "queryParameters": {"parameters": [{"name": "page", "value": "1"}]}}},
    {"type": "n8n-nodes-base.rssFeedRead", "typeVersion": 1.1, "category": "http",
     "name": "RSS Read", "usage": "读取 RSS/Atom 源", "key_params": {"url": "https://…/feed"}},
    {"type": "n8n-nodes-base.html", "typeVersion": 1.2, "category": "http",
     "name": "HTML", "usage": "从 HTML 提取内容（CSS 选择器 → 字段），配合 HTTP Request 抓网页用",
     "key_params": {"operation": "extractHtmlContent",
                    "extractionValues": {"values": [{"key": "标题", "cssSelector": "h1"}]}}},

    # ── 数据库（需要用户先在 n8n 界面配置凭据） ──
    {"type": "n8n-nodes-base.postgres", "typeVersion": 2.5, "category": "database",
     "name": "Postgres", "usage": "查询/写入 PostgreSQL（需凭据）",
     "key_params": {"operation": "executeQuery", "query": "SELECT …"}},
    {"type": "n8n-nodes-base.mySql", "typeVersion": 2.4, "category": "database",
     "name": "MySQL", "usage": "查询/写入 MySQL（需凭据）",
     "key_params": {"operation": "executeQuery", "query": "SELECT …"}},
    {"type": "n8n-nodes-base.mongoDb", "typeVersion": 1.1, "category": "database",
     "name": "MongoDB", "usage": "查询 MongoDB（需凭据）",
     "key_params": {"operation": "find", "collection": "…"}},

    # ── 变换与流控 ──
    {"type": "n8n-nodes-base.set", "typeVersion": 3.4, "category": "core",
     "name": "Edit Fields (Set)", "usage": "选择/重命名/新增字段，整形输出列",
     "key_params": {"mode": "manual", "assignments": {"assignments": [
         {"id": "a1", "name": "字段名", "type": "string", "value": "={{ $json.raw }}"}]}}},
    {"type": "n8n-nodes-base.code", "typeVersion": 2, "category": "core",
     "name": "Code", "usage": "JavaScript 自定义变换（返回 [{json:{…}}] 数组）",
     "key_params": {"mode": "runOnceForAllItems", "jsCode": "return items.map(i => ({json: i.json}))"}},
    {"type": "n8n-nodes-base.if", "typeVersion": 2.2, "category": "core",
     "name": "IF", "usage": "按条件分流 true/false 两路",
     "key_params": {"conditions": {"combinator": "and", "conditions": []}}},
    {"type": "n8n-nodes-base.switch", "typeVersion": 3.2, "category": "core",
     "name": "Switch", "usage": "多路分流", "key_params": {}},
    {"type": "n8n-nodes-base.merge", "typeVersion": 3, "category": "core",
     "name": "Merge", "usage": "合并两路输入（append/combine）", "key_params": {"mode": "append"}},
    {"type": "n8n-nodes-base.filter", "typeVersion": 2.2, "category": "core",
     "name": "Filter", "usage": "过滤行", "key_params": {}},
    {"type": "n8n-nodes-base.sort", "typeVersion": 1, "category": "core",
     "name": "Sort", "usage": "排序", "key_params": {"sortFieldsUi": {"sortField": [{"fieldName": "…"}]}}},
    {"type": "n8n-nodes-base.removeDuplicates", "typeVersion": 2, "category": "core",
     "name": "Remove Duplicates", "usage": "按字段去重", "key_params": {}},
    {"type": "n8n-nodes-base.splitInBatches", "typeVersion": 3, "category": "core",
     "name": "Loop Over Items", "usage": "分批循环处理（如逐页拉取）", "key_params": {"batchSize": 100}},
    {"type": "n8n-nodes-base.aggregate", "typeVersion": 1, "category": "core",
     "name": "Aggregate", "usage": "多行聚合为一行/数组", "key_params": {}},
    {"type": "n8n-nodes-base.limit", "typeVersion": 1, "category": "core",
     "name": "Limit", "usage": "截断行数", "key_params": {"maxItems": 1000}},

    # ── 文件 ──
    {"type": "n8n-nodes-base.extractFromFile", "typeVersion": 1, "category": "file",
     "name": "Extract From File", "usage": "从二进制文件解析 CSV/JSON/XLSX 为行",
     "key_params": {"operation": "csv"}},
    {"type": "n8n-nodes-base.readWriteFile", "typeVersion": 1, "category": "file",
     "name": "Read/Write File", "usage": "读写 n8n 宿主机文件（自托管可用）", "key_params": {}},
    {"type": "n8n-nodes-base.ftp", "typeVersion": 1, "category": "file",
     "name": "FTP", "usage": "FTP/SFTP 下载上传（需凭据）", "key_params": {}},

    # ── 回应与通知 ──
    {"type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "category": "notify",
     "name": "Respond to Webhook", "usage": "Webhook responseMode=responseNode 时自定义响应体",
     "key_params": {"respondWith": "allIncomingItems"}},
    {"type": "n8n-nodes-base.emailSend", "typeVersion": 2.1, "category": "notify",
     "name": "Send Email", "usage": "SMTP 发邮件通知（需凭据）", "key_params": {}},
    {"type": "n8n-nodes-base.noOp", "typeVersion": 1, "category": "core",
     "name": "No Operation", "usage": "占位/汇合节点", "key_params": {}},
]


def catalog_digest() -> str:
    """注入系统提示的单行式速查表。"""
    lines = []
    for n in NODE_CATALOG:
        lines.append(f"- {n['type']} (v{n['typeVersion']}) — {n['name']}: {n['usage']}")
    return "\n".join(lines)


def find_nodes(category: str | None = None, keyword: str | None = None) -> list[dict]:
    result = NODE_CATALOG
    if category:
        result = [n for n in result if n["category"] == category]
    if keyword:
        kw = keyword.lower()
        result = [n for n in result
                  if kw in n["type"].lower() or kw in n["name"].lower() or kw in n["usage"].lower()]
    return result
