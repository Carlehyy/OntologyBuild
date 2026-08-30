# OntologyBuild 后端

后端使用 FastAPI、SQLAlchemy、Alembic 和 Celery，要求 Python 3.12。应用入口
是 `app/main.py`，生产数据库历史以 `alembic/versions/` 为准。

依赖事实源是 `pyproject.toml` 与 `uv.lock`。`requirements.txt` 为历史兼容产物，
日常开发和 CI 统一使用 `uv sync --frozen`。

## 当前模块边界

优先从业务域查找实现：完整业务域表（含支撑基础设施与兼容层例外台账）以根
[AGENTS.md](../AGENTS.md) 第 1 节为唯一权威；`app/` 内逐目录导览见
[`app/README.md`](./app/README.md)。

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

本地完整开发还需要流水线 executor（经 NATS 消费派发任务）：

```bash
uv run python -m app.data_channel.pipeline_tasks.nats_executor
```

完整启动顺序与依赖见 [本地开发](../docs/development/setup.md)。

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
