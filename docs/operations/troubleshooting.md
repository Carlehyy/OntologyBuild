# 排障入口

## 启动失败

- 检查 Python 3.12、Node 22 和锁文件是否匹配；
- 检查 `config/generated/local/.env` 是否由当前配置中心生成；
- 逐项检查 PostgreSQL、Redis、Celery worker、Neo4j、MinIO 和 n8n；这些阻塞型
  依赖在正常本地与生产启动时都必须 fail closed；
- 检查 Chromium CDP 地址和服务；地址配置必需，服务不可达不终止 API，但深度
  readiness 必须 fail closed；
- 运行 `/health/live` 与 `/health/ready`，并执行 Celery ping；不要只看前端页面；
- 不要用平台 SQLite、API 线程任务、NetworkX/SQL 图或本地对象目录绕过故障。

API Hub 自有 SQLite、测试环境 SQLite 和历史 `local://` 只读迁移路径有独立
用途；看到它们不代表主平台存在降级模式。LLM 未配置时，先确认基础平台已
ready，再由管理员在模型配置页添加提供商。

## 部署失败

- verify 失败：先修测试/迁移/构建，不允许跳过 deploy 依赖；
- manifest 生成失败：检查 `production` Environment 的逐项配置；
- worker 失败：检查 Redis 鉴权、task registry 和 Celery ping；
- 图接口 503：检查 Neo4j 连接与本体投影状态，不应转查 SQL/NetworkX 结果；
- 文件写入失败：检查 MinIO，不要改用本地路径写新对象；
- 语义搜索 501：这是已移除语义搜索的预期契约；使用 PostgreSQL 关键词搜索；
- n8n/CDP 失败：检查外部端点、凭据、网络策略和真实 readiness；
- 静态资源失败：检查 Nginx、Vite base、懒加载 chunk 和内容类型；
- 迁移失败：保留日志与数据库副本，禁止直接 stamp/改 Alembic 历史。

## 目录移动后失败

优先检查：

- Python import 和 monkeypatch target；
- `Path(__file__).parents[n]`；
- fixture 相对路径；
- Vite/TypeScript alias；
- Playwright `testDir`；
- Compose context、volume、env_file；
- Actions `working-directory` 与 cache path；
- README、部署脚本和配置中心中写死的目录。
