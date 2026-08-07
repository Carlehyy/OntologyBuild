# OntologyBuild

OntologyBuild 是一个“本体即服务（Ontology-as-a-Service）”平台：把企业数据
接入、清洗并映射为对象、关系、动作与规则，再由 Sentinel Engine 监听状态
变化、评估条件并执行受治理的动作。

## 平台能力

- 本体管理与治理：对象、关系、动作与规则建模，版本按
  Draft → Trial → Impact → Promote 门控发布；
- 数据通道与数据管家：版本化数据集、流水线发布与成品审核；
- 业务探索、超级助手与本体助手：对话式业务建模与受治理的本体问答；
- 事件登记：独立的事件登记与查询能力；
- API Hub：接口代理与对外发布（Plugin 社区已接通，Skill 社区维护中）；
- Sentinel：监听状态变化、评估条件并执行受治理动作；
- 模型配置与系统治理：LLM 不是启动依赖，启动后由管理员按需接入提供商。

## 快速开始

### 完整本地栈（推荐）

```bash
git clone --branch nano-ontoprompt https://github.com/Carlehyy/OntologyBuild.git
cd OntologyBuild
cp .env.example .env
# 填写并验证外部 n8n 地址与凭据后再启动
docker compose -f docker-compose.local.yml up --build
```

启动后访问 `http://localhost:5173`，本地示例账号 `admin / admin123`（仅用于
本地开发）。后端存活检查位于 `http://localhost:8000/health/live`；完整平台
可用性还必须通过 `/health/ready` 和 Celery worker 检查。

`docker-compose.prod.yml` 是生产编排入口，按
[部署说明](./docs/operations/deployment.md) 与 GitHub Actions 工作流执行；
不要用本地编排推断生产依赖已经验证。

### 源码开发

前置要求：Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js 22、npm，以及
已经启动的 PostgreSQL、Redis、Neo4j、MinIO 和 n8n。Chromium CDP 地址必须
配置；它暂时不可达时仍可启动 API 用于诊断，但深度 readiness 保持失败。先运行
本地配置中心并通过所有阻塞型外部依赖连通性检查：

```bash
./config/start.sh
```

Windows 使用 `config/start.bat`。配置通过后，分别启动：

```bash
uv sync --directory backend --frozen --group dev
# app.dev_server 会先执行 alembic upgrade head，再启动热重载服务
uv run --directory backend python -m app.dev_server

uv run --directory backend \
  celery -A app.tasks.celery_app:celery_app worker --loglevel=info

npm --prefix frontend ci
npm --prefix frontend run dev
```

配置中心生成的 `config/generated/local/.env` 不进入 Git。启动后从“模型配置”
页面按需配置 LLM。完整依赖、端口和例外边界见
[开发环境](./docs/development/setup.md) 与
[config/README.md](./config/README.md)。

## 核心运行机制

数据不是一条可以跳过状态门的直线：

```text
数据生产（版本化数据集 → 流水线发布 → 成品审核）
  → 定义发布（Draft → Trial → Impact → Promote）
  → 数据刷新（Approved Version → Mapping → Formal → 查询投影 → Sentinel → Action）
```

平台采用 fail-closed 依赖契约：PostgreSQL、Redis、Celery worker、Neo4j、
MinIO 和 n8n 必须真实就绪，后端不会在依赖失败时静默切换到 SQLite、内存图或
本地对象存储。语义搜索当前明确返回 `501 semantic_search_unsupported`，关键词
搜索由 PostgreSQL 提供。事件登记是独立业务能力，当前没有可核验的
RegisteredEvent → Formal/Sentinel 自动接线。状态门与发布契约的实现以
`backend/app/data_channel/`、`backend/app/ontologies/` 及对应测试为准。

## 技术栈

- 后端：FastAPI、Python 3.12、SQLAlchemy、Alembic、Celery；
- 前端：React、TypeScript、Vite、Tailwind CSS；
- 数据与中间件：PostgreSQL、Redis、Neo4j、MinIO；
- 工作流与浏览器：n8n、Chromium CDP；
- 交付：Docker Compose、GitHub Actions。

## 开发指南

开始开发前请先阅读 [AGENTS.md](./AGENTS.md)（仓库级强制准则，含分支与 PR
规则）。所有文档入口和事实源见 [docs/README.md](./docs/README.md)。

### 三分钟定位

| 想了解 | 从这里开始 |
|---|---|
| 本地如何启动 | [开发环境](./docs/development/setup.md) |
| 改动后必须跑哪些测试 | [AGENTS.md](./AGENTS.md) 与 [测试指南](./docs/development/testing.md) |
| 配置和秘密放在哪里 | [配置说明](./docs/operations/configuration.md) |
| GitHub Actions 如何部署、如何回滚 | [部署](./docs/operations/deployment.md) 与 [回滚](./docs/operations/rollback.md) |
| 功能的前端、后端和测试在哪里 | 代码即文档：从 `frontend/src/config/navigation.ts` 的导航项出发，对照 [AGENTS.md](./AGENTS.md) 第 1 节的业务域表定位后端包 |
| 最近迭代了什么 | Git 提交历史（`git log` 或 GitHub 提交页） |

### 仓库结构

```text
OntologyBuild/
├── backend/                 FastAPI、领域模块、Alembic 和后端测试
├── frontend/                React 应用、纯逻辑单元测试与 Playwright 旅程
├── config/                  独立的本地配置中心
├── docs/
│   ├── development/         本地环境搭建与测试门禁
│   └── operations/          配置、部署、回滚、备份和排障
├── scripts/                 CI、部署和受控数据脚本
├── docker/                  容器初始化与运行资源
├── test_data/               受版本控制、已分类的测试 fixture
├── .github/workflows/       PR 验证与自动部署
├── .env.example             本地/容器环境变量模板
├── production.dependencies.env
│                            当前自动部署使用的生产依赖清单（受控临时例外）
├── production.dependencies.example.env
│                            后续迁移与人工校验使用的无秘密模板
├── AGENTS.md                强制开发和交付准则
├── docker-compose.local.yml  推荐的本地核心完整栈
└── docker-compose.prod.yml   生产编排
```

后端已处于从传统横向目录向业务域迁移的中间状态。新同事应按
[AGENTS.md](./AGENTS.md) 第 1 节的业务域表和兼容层例外台账找到当前权威实现，
不要仅凭目录名称猜测。前端平台概览已迁入 `src/features/overview/`，其他业务
仍以 `pages/`、`api/`、`components/` 等现有路径为主；不得把目标结构误写成
全仓已经完成的事实。

### 标准验证

```bash
node scripts/ci/check-markdown-links.mjs
bash scripts/ci/check-repository-hygiene.sh

uv sync --directory backend --frozen --group dev
uv run --directory backend pytest -q --disable-warnings --ignore tests/v2/perf

uv sync --directory config --frozen --group dev
uv run --directory config pytest -q

npm --prefix frontend ci
npm --prefix frontend run test:unit
npm --prefix frontend run check:feature-boundaries
npm --prefix frontend run test:e2e:classification
npm --prefix frontend run lint
npm --prefix frontend run build
npm --prefix frontend run test:e2e:mocked
```

真实后端、浏览器接管、对象存储、n8n、LLM 和生产部署变更还需要隔离环境
验收；不能用离线 mock 结果替代。详细矩阵见
[测试指南](./docs/development/testing.md)。

## 部署与安全

推送到 `nano-ontoprompt` 分支会触发验证与自动部署，流程与回滚见
[部署说明](./docs/operations/deployment.md) 和
[回滚](./docs/operations/rollback.md)。

当前部署事实源是仓库中已跟踪的 `production.dependencies.env`（含真实生产
凭据，属受控临时例外）：日常开发不得修改、复制或回显其中的值。后续迁移到
GitHub Environment Secrets/Variables 必须作为独立运维变更执行；仅删除当前
文件不等于清理 Git 历史。凭据与卫生红线见 [AGENTS.md](./AGENTS.md) 第 5 节。
