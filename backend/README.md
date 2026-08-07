# OntologyBuild 后端

后端使用 FastAPI、SQLAlchemy、Alembic 和 Celery，要求 Python 3.12。应用入口
是 `app/main.py`，生产数据库历史以 `alembic/versions/` 为准。

依赖事实源是 `pyproject.toml` 与 `uv.lock`。`requirements.txt` 为历史兼容产物，
日常开发和 CI 统一使用 `uv sync --frozen`。

## 当前模块边界

优先从业务域查找实现：

```text
app/
├── bootstrap/         FastAPI 健康检查、生命周期与启动 seed
├── platform/          平台概览
├── super_assistant/   超级助手、Skill、MCP
├── exploration/       业务探索
├── ontologies/        本体、映射、图、Agent、Sentinel
├── events/            事件登记
├── data_channel/      连接、数据集、流水线、数据管家
├── api_hub/           接口定义、发布与代理
├── community/         开放社区
├── model_configs/     模型配置
├── settings/          系统设置
├── auth/              身份与菜单授权
├── inbox/             收件箱契约
├── shared/            迁移期共享基础能力
└── tasks/             Celery 任务入口
```

`app/routers`、`models`、`schemas`、`services` 以兼容转发为主，但仍有少量
真实实现；不要批量删除或假设它们都是 facade。例外和迁移协议见
[AGENTS.md 兼容层例外台账](../AGENTS.md)。

## 人工脚本

`scripts/` 只保存需要操作者显式执行的迁移、维护、演示和真实链路验收工具。
每个保留入口的依赖、写入范围和清理要求见
[`scripts/README.md`](./scripts/README.md)。这些脚本不属于普通单元测试，也
不得仅凭“能运行”直接对生产环境执行。

## 开发

```bash
uv sync --frozen --group dev
# 该入口先执行 alembic upgrade head；迁移失败时不会启动 API
uv run python -m app.dev_server
```

另开终端启动 worker：

```bash
uv run celery -A app.tasks.celery_app:celery_app worker --loglevel=info
```

## 验证

```bash
uv run pytest -q --disable-warnings --ignore tests/v2/perf
uv run pytest -q --disable-warnings tests/v2/perf

# 仅用于隔离迁移 fixture；真实非测试启动只支持 PostgreSQL。
DB_FILE="$(mktemp -u /tmp/ontologybuild-XXXXXX.db)"
ENVIRONMENT=test DATABASE_URL="sqlite:///${DB_FILE}" \
  uv run alembic upgrade head
ENVIRONMENT=test uv run alembic heads
rm -f "${DB_FILE}"
```

目录、路由、ORM 或任务变化还需执行契约和真实栈验证，详见
[测试指南](../docs/development/testing.md)。
