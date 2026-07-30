# 排障入口

## 启动失败

- 检查 Python 3.12、Node 22 和锁文件是否匹配；
- 检查 `config/generated/local/.env` 是否由当前配置中心生成；
- 区分允许降级的本地模式和必须 fail-closed 的生产模式；
- 运行 `/health/live` 与 `/api/health`，不要只看前端页面。

## 部署失败

- verify 失败：先修测试/迁移/构建，不允许跳过 deploy 依赖；
- manifest 生成失败：检查 `production` Environment 的逐项配置；
- worker 失败：检查 Redis 鉴权、task registry 和 Celery ping；
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
