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
     "name": "Schedule Trigger", "usage": "外部自管工作流可用；平台受管流水线禁止使用，统一由数据任务池调度",
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


# 高价值节点的深挖详情 — describe_node 返回：完整参数说明 + 可抄的 worked example + 坑。
# 只维护数据流水线最常用的一批；不在此表的节点回退 key_params 模板。
NODE_DETAIL: dict[str, dict] = {
    "n8n-nodes-base.webhook": {
        "params": {
            "httpMethod": "平台调度固定用 POST",
            "path": "webhook 路径，平台约定前缀 ob-；同实例内唯一",
            "responseMode": "lastNode=执行完把末节点数据作为响应（平台入湖用这个）；responseNode=由 Respond to Webhook 节点自定义",
        },
        "example": {"httpMethod": "POST", "path": "ob-orders", "responseMode": "lastNode"},
        "notes": "平台运行流水线 = POST 这个 webhook 取末节点 items。未发布/未激活时生产 webhook 不注册，别指望能直接打通。",
    },
    "n8n-nodes-base.scheduleTrigger": {
        "params": {"rule.interval": "定时规则数组，field=seconds/minutes/hours/days/weeks/months + 对应 Interval"},
        "example": {"rule": {"interval": [{"field": "hours", "hoursInterval": 6}]}},
        "notes": "n8n 内部自跑触发器。自跑产物只有经平台触发的运行才自动入湖；要自跑回传需加 HTTP Request 回调平台 API + Token。",
    },
    "n8n-nodes-base.httpRequest": {
        "params": {
            "method": "GET / POST / PUT / DELETE",
            "url": "完整 URL，可用表达式 ={{ }}",
            "authentication": "none | genericCredentialType(+genericAuthType: httpHeaderAuth/httpBasicAuth/httpQueryAuth/oAuth2Api) | predefinedCredentialType",
            "sendQuery + queryParameters": "查询串：{parameters:[{name,value}]}",
            "sendHeaders + headerParameters": "自定义请求头（同上结构）",
            "sendBody + contentType + jsonBody/bodyParameters": "POST 体：contentType=json 时 specifyBody=json 配 jsonBody",
            "options.pagination": "内置分页，见 n8n_reference('patterns') 的 paginated_api",
        },
        "example": {"method": "GET", "url": "https://api.example.com/items",
                    "sendQuery": True,
                    "queryParameters": {"parameters": [{"name": "limit", "value": "100"}]}},
        "notes": "需认证时优先让用户在 n8n 配好凭据，再 authentication=genericCredentialType 引用 credentials；别把密钥明文写进 header。",
    },
    "n8n-nodes-base.set": {
        "params": {
            "mode": "manual（推荐，逐字段赋值）",
            "assignments.assignments": "赋值数组：每项 {id, name, type(string/number/boolean/array/object), value}",
            "value": "动态值必须写成 ={{ $json.x }}；字面量直接写",
            "options.include": "是否保留其它未列字段（默认只留声明的）",
        },
        "example": {"mode": "manual", "assignments": {"assignments": [
            {"id": "f1", "name": "id", "type": "string", "value": "={{ $json.id }}"},
            {"id": "f2", "name": "价格", "type": "number", "value": "={{ $json.price }}"}]}},
        "notes": "typeVersion 3.x 用 assignments 结构（不是老的 values）。这是把输出整形成“扁平列”的首选节点。",
    },
    "n8n-nodes-base.code": {
        "params": {
            "mode": "runOnceForAllItems（默认，拿 items 数组）/ runOnceForEachItem",
            "language": "javaScript（默认）/ python(beta)",
            "jsCode / pythonCode": "代码体，见 n8n_reference('code') 的返回契约",
        },
        "example": {"mode": "runOnceForAllItems",
                    "jsCode": "return items.map(i => ({ json: { id: i.json.id, name: i.json.title } }));"},
        "notes": "必须 return [{json:{…}}] 数组；末节点用 Code 时输出要“一行一 item、字段扁平”。",
    },
    "n8n-nodes-base.if": {
        "params": {
            "conditions.combinator": "and / or",
            "conditions.conditions": "条件数组：{leftValue: ={{ $json.x }}, rightValue, operator:{type, operation}}",
        },
        "example": {"conditions": {"combinator": "and", "conditions": [
            {"leftValue": "={{ $json.status }}", "rightValue": "active",
             "operator": {"type": "string", "operation": "equals"}}]}},
        "notes": "typeVersion 2.x 的条件结构带 operator.type（string/number/boolean/dateTime）。输出两路：true / false。",
    },
    "n8n-nodes-base.filter": {
        "params": {"conditions": "同 IF 的 conditions 结构，命中条件的行通过"},
        "example": {"conditions": {"combinator": "and", "conditions": [
            {"leftValue": "={{ $json.amount }}", "rightValue": 0,
             "operator": {"type": "number", "operation": "gt"}}]}},
        "notes": "只留满足条件的行；比 IF 简单（单路输出）。",
    },
    "n8n-nodes-base.html": {
        "params": {
            "operation": "extractHtmlContent",
            "extractionValues.values": "提取数组：{key, cssSelector, returnValue(text/html/attribute), attribute?, returnArray?}",
        },
        "example": {"operation": "extractHtmlContent", "extractionValues": {"values": [
            {"key": "标题", "cssSelector": "h2.title"},
            {"key": "链接", "cssSelector": "a", "returnValue": "attribute", "attribute": "href"}]}},
        "notes": "配合 HTTP Request 抓到的 HTML 用；先 probe_url 看页面结构再定选择器。",
    },
    "n8n-nodes-base.postgres": {
        "params": {"operation": "executeQuery / insert / update", "query": "SQL，支持 $1 占位或表达式"},
        "example": {"operation": "executeQuery", "query": "SELECT id, name FROM orders WHERE created_at > now() - interval '1 day'"},
        "notes": "必须引用一个 Postgres 凭据。用 check_credentials 看实例有没有；没有就让用户去 n8n 配，API 建不了凭据。",
    },
    "n8n-nodes-base.extractFromFile": {
        "params": {"operation": "csv / fromJson / xlsx / xml", "binaryPropertyName": "上游二进制字段名（默认 data）"},
        "example": {"operation": "csv", "options": {}},
        "notes": "把上游 HTTP/读取到的二进制文件解析成行；常接在 HTTP Request(下载文件) 之后。",
    },
    "n8n-nodes-base.splitInBatches": {
        "params": {"batchSize": "每批条数", "options": {}},
        "example": {"batchSize": 100},
        "notes": "配合回路做分批循环（如逐页/逐条调用）；循环出口回连到自身。分页优先用 HTTP Request 内置 pagination。",
    },
    "n8n-nodes-base.respondToWebhook": {
        "params": {"respondWith": "allIncomingItems / json / text / noData", "responseBody": "respondWith=json 时的体"},
        "example": {"respondWith": "allIncomingItems"},
        "notes": "仅当 Webhook responseMode=responseNode 时才需要它自定义响应；平台常规入湖用 lastNode 就够，不必加。",
    },
}


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


def _resolve_node(node_type: str) -> dict | None:
    """把用户/LLM 给的类型解析到目录条目：全名 / 短名(httpRequest) / 名称关键字都认。"""
    q = (node_type or "").strip().lower()
    if not q:
        return None
    for n in NODE_CATALOG:  # 1) 精确全名
        if n["type"].lower() == q:
            return n
    for n in NODE_CATALOG:  # 2) 短名（type 后缀）
        if n["type"].lower().rsplit(".", 1)[-1] == q:
            return n
    hits = [n for n in NODE_CATALOG  # 3) 名称/用途关键字
            if q in n["type"].lower() or q in n["name"].lower() or q in n["usage"].lower()]
    return hits[0] if len(hits) == 1 else (hits[0] if hits else None)


def describe_node(node_type: str) -> dict:
    """返回一个节点的完整编排知识：type/typeVersion/用途 + 参数说明 + worked example + 坑。"""
    n = _resolve_node(node_type)
    if n is None:
        return {"error": f"目录里没有匹配「{node_type}」的节点。",
                "hint": "不在目录的 n8n 节点也能用，但请自行确认 type 与 typeVersion 正确；"
                        "或用 list_node_types 看目录、n8n_reference('patterns') 找骨架。"}
    out = {"type": n["type"], "typeVersion": n["typeVersion"], "name": n["name"],
           "usage": n["usage"], "category": n["category"], "key_params": n.get("key_params", {})}
    detail = NODE_DETAIL.get(n["type"])
    if detail:
        out["detail"] = detail
    else:
        out["detail_note"] = "该节点暂无深挖详情，参照 key_params 模板拼参数即可。"
    return out
