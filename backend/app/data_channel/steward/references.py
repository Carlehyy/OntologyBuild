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
返回契约：末节点若是 Code，必须吐“一行一个 item、json 里字段为标量或平台 FileRef”的结构。不要把 n8n binary 放进末节点。"""


FILE_REF_REF = """平台受管附件（n8n → 平台文件网关 → 平台存储）：
- 每次平台触发 Webhook 时都会在 `$node["Webhook"].json.body.file_gateway` 注入：
  `upload_url`、短时 `token`、`invocation_id`、`max_bytes`。不要把 token 写死进 workflow。
- 下载源附件的 HTTP Request 把 Response Format 设为 File，二进制字段建议统一叫 `data`。
- 下载节点经常把文件名退化成 `data`/`data.txt`。上传前应增加 Code 节点，把
  `binary.data.fileName` 设置为业务接口返回的原始文件名（缺失时再使用安全兜底名）；
  不要用二进制字段名冒充文件名。
- 再用 HTTP Request POST 到 `={{ $node["Webhook"].json.body.file_gateway.upload_url }}`；
  Header `Authorization` = `={{ 'Bearer ' + $node["Webhook"].json.body.file_gateway.token }}`；
  Body 选 multipart/form-data，`file` 参数取 Binary File 字段 `data`，另传稳定的
  `idempotency_key`（推荐“业务记录主键:附件字段/序号”，同一次重试必须相同）。
- 上传响应是 `{file_ref:{...}}`。末节点只输出 `file_ref` 对象和普通 JSON 列；平台会校验
  id、流水线、本次执行、大小、哈希并重写为可信元数据。禁止输出 storage_uri、MinIO 凭据、
  预签名 URL、base64 或 n8n binary。
- FileRef 形状：`{"$type":"file_ref","id":"…","name":"报告.pdf","size":123,
  "content_type":"application/pdf","sha256":"…",
  "download_url":"/api/v2/file-assets/…/download",
  "authenticated_url":"https://平台/#/file-assets/…/download",
  "share_url":"https://平台/api/public/file-assets/随机令牌/download"}`。
  download_url 是受保护 API；authenticated_url 可在浏览器中先登录再下载；share_url 是长期
  匿名平台网关地址，不自动过期，但平台管理员可以立即吊销或重新生成。不要把底层存储地址当分享地址。
- 上传中的临时附件仍按保留期清理；只有成功出现在末节点最终输出中的 FileRef 才会解除临时过期，
  并随流水线产物/数据集版本生命周期管理。上传成功但未被引用的孤儿文件仍会删除。
- 文件名只用于展示；平台会去路径、去控制字符并作为元数据保存。底层使用 MinIO 时 object key
  始终由平台生成；未配置 MinIO 时可由平台文件存储降级实现承载，FileRef 契约不变。"""


# ── 可复用骨架（nodes/connections 可直接抄，改 url/query/字段即可） ──
# 平台约定：Webhook 触发（POST, responseMode=lastNode），末节点输出扁平行数据。

PATTERNS: list[dict] = [
    {
        "name": "rest_api_with_attachment",
        "title": "JSON API + 附件 → FileRef → 入湖",
        "when": "接口行数据含 attachment_url 等附件地址：JSON 保持表格输出，附件经平台网关存入私有 MinIO。",
        "nodes": [
            {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
             "parameters": {"httpMethod": "POST", "path": "ob-<短名>", "responseMode": "lastNode"}},
            {"name": "拉取数据", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
             "parameters": {"method": "GET", "url": "https://api.example.com/items"}},
            {"name": "下载附件", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
             "parameters": {"method": "GET", "url": "={{ $json.attachment_url }}",
                            "options": {"response": {"response": {
                                "responseFormat": "file", "outputPropertyName": "data"}}}}},
            {"name": "保留文件名", "type": "n8n-nodes-base.code", "typeVersion": 2,
             "parameters": {"mode": "runOnceForAllItems", "jsCode":
                 "const item = $input.first();\n"
                 "if (!item.binary?.data) throw new Error('附件下载结果缺少 binary.data');\n"
                 "const sourceName = $('拉取数据').first().json.attachment_name;\n"
                 "item.binary.data.fileName = String(sourceName || item.binary.data.fileName || 'attachment.bin');\n"
                 "return [item];"}},
            {"name": "上传附件", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
             "parameters": {
                 "method": "POST",
                 "url": "={{ $node[\"Webhook\"].json.body.file_gateway.upload_url }}",
                 "sendHeaders": True,
                 "headerParameters": {"parameters": [{
                     "name": "Authorization",
                     "value": "={{ 'Bearer ' + $node[\"Webhook\"].json.body.file_gateway.token }}"}]},
                 "sendBody": True,
                 "contentType": "multipart-form-data",
                 "bodyParameters": {"parameters": [
                     {"parameterType": "formBinaryData", "name": "file", "inputDataFieldName": "data"},
                     {"name": "idempotency_key",
                      "value": "={{ $node[\"拉取数据\"].json.id + ':attachment' }}"},
                 ]},
             }},
            {"name": "整形输出", "type": "n8n-nodes-base.set", "typeVersion": 3.4,
             "parameters": {"mode": "manual", "assignments": {"assignments": [
                 {"id": "f1", "name": "id", "type": "string",
                  "value": "={{ $node[\"拉取数据\"].json.id }}"},
                 {"id": "f2", "name": "标题", "type": "string",
                  "value": "={{ $node[\"拉取数据\"].json.title }}"},
                 {"id": "f3", "name": "附件", "type": "object", "value": "={{ $json.file_ref }}"},
             ]}}},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "拉取数据", "type": "main", "index": 0}]]},
            "拉取数据": {"main": [[{"node": "下载附件", "type": "main", "index": 0}]]},
            "下载附件": {"main": [[{"node": "保留文件名", "type": "main", "index": 0}]]},
            "保留文件名": {"main": [[{"node": "上传附件", "type": "main", "index": 0}]]},
            "上传附件": {"main": [[{"node": "整形输出", "type": "main", "index": 0}]]},
        },
        "note": "编排前先查 n8n_reference('files')。多附件要逐个上传，并让每个 file_ref 都进入末节点 JSON。",
    },
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

_TOPICS = {"expressions", "code", "files", "patterns"}


def reference(topic: str) -> dict:
    """按 topic 返回参考内容；patterns 直接给可抄的 nodes/connections 骨架。"""
    topic = (topic or "").strip().lower()
    if topic in ("expression", "expressions", "expr"):
        return {"topic": "expressions", "text": EXPRESSION_REF}
    if topic in ("code", "code_node", "js", "javascript"):
        return {"topic": "code", "text": CODE_REF}
    if topic in ("file", "files", "attachment", "attachments", "file_ref"):
        return {"topic": "files", "text": FILE_REF_REF}
    if topic in ("pattern", "patterns", "template", "templates"):
        return {"topic": "patterns",
                "patterns": [{"name": p["name"], "title": p["title"], "when": p["when"],
                              "nodes": p["nodes"], "connections": p["connections"],
                              **({"note": p["note"]} if p.get("note") else {})}
                             for p in PATTERNS]}
    return {"error": f"未知 topic「{topic}」。可选：{', '.join(sorted(_TOPICS))}。"}
