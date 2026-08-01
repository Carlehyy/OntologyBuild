# 本地开发

## 前置环境

- Python 3.12.x；
- uv 0.11.14 或与锁文件兼容版本；
- Node.js 22；
- npm；
- PostgreSQL、Redis、Celery worker、Neo4j、MinIO 和 n8n，以及已明确配置地址的
  Chromium CDP。

不存在可用于正常开发的“最小降级模式”。PostgreSQL、Redis/Celery worker、
Neo4j、MinIO 和 n8n 必须提供真实配置并通过连通性检查；Chromium CDP 地址必须
配置，其服务连通检查是提示性的，不可达时 API 可启动用于诊断但深度 readiness
失败。平台不会改用 SQLite、API 线程任务、NetworkX/SQL 图或本地对象存储。
API Hub 自有 SQLite、测试环境 SQLite 和历史 `local://` 只读迁移兼容有各自
边界，不代表开发运行时可以省略依赖。

## 推荐：本地配置中心

```bash
./config/start.sh
```

Windows 使用 `config/start.bat`。配置中心生成
`config/generated/local/.env`，该文件不进入 Git。生成前必须通过 PostgreSQL、
Redis、Neo4j、MinIO 和 n8n 探针；Chromium CDP 地址同样必须配置，但其启动前
探针是提示性检查，暂时不可达不会阻止生成配置。Celery worker 在配置生成后
按下列命令启动，CDP 未恢复前深度 readiness 保持失败。

随后分别启动：

```bash
# dev_server 先执行 alembic upgrade head；迁移失败时 API 不会启动
uv run --directory backend python -m app.dev_server
uv run --directory backend celery -A app.tasks.celery_app:celery_app worker --loglevel=info
npm --prefix frontend ci
npm --prefix frontend run dev
```

随后执行配置中心的“启动后复检”，确认后端深度 readiness、前端以及至少一个
Celery worker PONG。复检未通过时平台不算启动完成。

n8n 地址、API Key 和超时由配置中心生成的启动环境统一托管。“系统设置 →
工作流配置”只显示脱敏状态并测试当前启动配置，不保存覆盖值；修改 n8n 后需
重启 API 与 worker。测试代码只有在 `ENVIRONMENT=test` 下才可注入隔离配置。

API、worker 和前端都启动后，再由管理员登录“模型配置”页面，按需配置 LLM
提供商、模型和凭据。LLM 未配置不阻断基础平台启动；相关接口会明确报告未配置，
或在已声明的文本抽取场景使用可识别的确定性规则模式，不会伪装成 LLM 结果。

## 搜索契约

- `GET /api/v2/ontologies/{ontology_id}/search/keyword` 使用 PostgreSQL；
- `POST /api/v2/ontologies/{ontology_id}/search` 的 `mode=keyword` 使用
  PostgreSQL；
- 语义搜索端点和 `mode=semantic` 返回
  `501 semantic_search_unsupported`；
- 不需要也不应配置 ChromaDB。

## 测试环境例外

`ENVIRONMENT=test` 可以使用隔离 SQLite、mock 服务、临时目录和数据库 n8n
配置注入，以保证测试确定性。测试例外不得进入正常启动配置，也不能作为真实
依赖验收证据。生产配置和部署见
[配置说明](../operations/configuration.md)与
[部署说明](../operations/deployment.md)。
