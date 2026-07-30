# 本地开发

## 前置环境

- Python 3.12.x；
- uv 0.11.14 或与锁文件兼容版本；
- Node.js 22；
- npm；
- 完整模式需要 PostgreSQL、Redis、Neo4j、MinIO、ChromaDB、浏览器运行时
  和可选 n8n。

## 推荐：本地配置中心

```bash
./config/start.sh
```

Windows 使用 `config/start.bat`。配置中心生成
`config/generated/local/.env`，该文件不进入 Git。

随后分别启动：

```bash
uv run --directory backend python -m app.dev_server
uv run --directory backend celery -A app.tasks.celery_app:celery_app worker --loglevel=info
npm --prefix frontend ci
npm --prefix frontend run dev
```

## 最小降级模式

```bash
uv sync --directory backend --frozen --group dev
uv run --directory backend uvicorn app.main:app --reload --port 8000

npm --prefix frontend ci
npm --prefix frontend run dev
```

最小模式不证明生产依赖可用。生产配置和部署见
[配置说明](../operations/configuration.md)与
[部署说明](../operations/deployment.md)。
