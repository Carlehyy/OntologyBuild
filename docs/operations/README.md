# 运维文档

```text
operations/
├── README.md
├── configuration.md    配置源、生产依赖与部署 Secrets
├── deployment.md       GitHub Actions 和服务器部署
├── rollback.md         应用与数据库回滚
├── backup-restore.md   备份与恢复
└── troubleshooting.md  常见故障定位
```

运维事实以 `.github/workflows/`、`docker-compose*.yml`、
`scripts/deploy-prod.sh`、配置模型和健康检查为准。任何部署脚本变化必须在
同一 PR 更新本目录并运行部署专项测试。

正常启动必须同时具备 PostgreSQL、Redis、Celery worker、Neo4j、MinIO、n8n
和 Chromium CDP。依赖清单、失败关闭行为、LLM 与 SQLite 例外见
[配置说明](./configuration.md)。

建议顺序：[配置](./configuration.md) → [部署](./deployment.md) →
[回滚](./rollback.md) → [备份恢复](./backup-restore.md)。
