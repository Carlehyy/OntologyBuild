# OntologyBuild

OntologyBuild 是一个“本体即服务（Ontology-as-a-Service）”平台：把企业数据
接入、清洗并映射为对象、关系、动作与规则，再由 Sentinel Engine 监听状态
变化、评估条件并执行受治理的动作。

核心运行链路不是一条可以跳过状态门的直线：

```text
数据生产（版本化数据集 → 流水线发布 → 成品审核）
  → 定义发布（Draft → Trial → Impact → Promote）
  → 数据刷新（Approved Version → Mapping → Formal → 查询投影 → Sentinel → Action）
```

事件登记是独立业务能力；当前没有可核验的 RegisteredEvent → Formal/Sentinel
自动接线。平台还包含业务探索、超级助手、数据管家、API Hub、已接通的 Plugin
社区与维护中的 Skill 社区、模型配置和系统治理。完整说明见
[产品概览](./docs/product/overview.md)、
[核心数据流](./docs/architecture/data-flow.md)和
[核心运行契约](./docs/product/requirements/0002-core-data-ontology-runtime-contract.md)。

## 三分钟定位

| 想了解 | 从这里开始 |
|---|---|
| 产品解决什么问题 | [产品概览](./docs/product/overview.md) |
| 核心数据怎样生产、发布和刷新 | [核心数据流](./docs/architecture/data-flow.md) |
| 状态门、权限、幂等和回滚是什么 | [核心运行契约](./docs/product/requirements/0002-core-data-ontology-runtime-contract.md) |
| 导航分别对应哪些稳定业务能力 | [导航与业务能力](./docs/product/navigation-business-map.md) |
| 一个导航功能的前端、后端、API 和测试在哪里 | [统一模块地图](./docs/architecture/module-map.md) |
| 本地如何启动 | [开发环境](./docs/development/setup.md) |
| 改动后必须跑哪些测试 | [AGENTS.md](./AGENTS.md)（兼容入口：[AGENT.md](./AGENT.md)）与 [测试指南](./docs/development/testing.md) |
| 配置和秘密放在哪里 | [配置说明](./docs/operations/configuration.md) |
| GitHub Actions 如何部署、如何回滚 | [部署](./docs/operations/deployment.md) 与 [回滚](./docs/operations/rollback.md) |
| 最近迭代了什么 | [迭代记录](./docs/iterations/README.md) 与 [CHANGELOG](./CHANGELOG.md) |

所有文档入口和事实源见 [docs/README.md](./docs/README.md)。开始开发前请先阅读
[AGENTS.md](./AGENTS.md)；参与方式见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## 按目录下钻

| 目录 | 本目录入口 |
|---|---|
| 后端 | [backend/README.md](./backend/README.md) |
| 后端应用 | [backend/app/README.md](./backend/app/README.md) |
| 数据通道源码 | [backend/app/data_channel/README.md](./backend/app/data_channel/README.md) |
| 本体源码 | [backend/app/ontologies/README.md](./backend/app/ontologies/README.md) |
| 后端测试 | [backend/tests/README.md](./backend/tests/README.md) |
| 前端 | [frontend/README.md](./frontend/README.md) |
| 前端源码 | [frontend/src/README.md](./frontend/src/README.md) |
| 前端页面 | [frontend/src/pages/README.md](./frontend/src/pages/README.md) |
| 本体图谱前端 | [frontend/src/palantir-graph/README.md](./frontend/src/palantir-graph/README.md) |
| 前端测试 | [frontend/src/test/README.md](./frontend/src/test/README.md) |
| 本地配置中心 | [config/README.md](./config/README.md) |
| 项目文档 | [docs/README.md](./docs/README.md) |
| 仓库脚本 | [scripts/README.md](./scripts/README.md) |
| 共享 fixture | [test_data/README.md](./test_data/README.md) |
| Docker 资源 | [docker/README.md](./docker/README.md) |
| GitHub Actions | [.github/workflows/README.md](./.github/workflows/README.md) |
| 本地 Agent 启动示例 | [.claude/README.md](./.claude/README.md) |

## 技术栈

- 后端：FastAPI、Python 3.12、SQLAlchemy、Alembic、Celery；
- 前端：React、TypeScript、Vite、Tailwind CSS；
- 数据与中间件：PostgreSQL、Redis、Neo4j、MinIO；
- 工作流与浏览器：n8n、Chromium CDP；
- 交付：Docker Compose、GitHub Actions。

正常启动采用同一套 fail-closed 依赖契约：PostgreSQL、Redis、Celery worker、
Neo4j、MinIO 和 n8n 必须完成配置且真实就绪；Chromium CDP 必须在
启动配置中明确提供，暂时不可达不会伪装成进程崩溃，但会使深度 readiness
失败。后端不会在依赖
失败时静默切换到平台 SQLite、API 进程任务、内存图或本地对象存储；已有显式
同步接口保持其公开语义。API Hub 自有
SQLite、测试环境 SQLite，以及历史 `local://` 对象的只读迁移兼容不属于运行时
降级。

ChromaDB 已从平台移除。关键词搜索继续由 PostgreSQL 提供；语义搜索及统一搜索
的 semantic 模式明确返回 `501 semantic_search_unsupported`。LLM 不是启动依赖，
平台启动后由管理员在“模型配置”页面按需添加提供商和模型。

## 快速开始

### 完整本地栈

```bash
git clone --branch nano-ontoprompt https://github.com/Carlehyy/OntologyBuild.git
cd OntologyBuild
cp .env.example .env
# 填写并验证外部 n8n 地址与凭据后再启动
docker compose -f docker-compose.local.yml up --build
```

前端默认位于 `http://localhost:5173`，后端存活检查位于
`http://localhost:8000/health/live`。示例账号只用于本地开发，默认是
`admin / admin123`。`/health/live` 只表示 API 进程存活；完整平台可用性还必须
通过 `/health/ready` 和 Celery worker 检查。

两份 Compose 文件用途不同，均为当前有效入口：

| 文件 | 用途 | 说明 |
|---|---|---|
| `docker-compose.local.yml` | 推荐的本地核心栈 | PostgreSQL、Redis、Neo4j、MinIO、Chromium CDP、API、Worker、前端；n8n 作为启动前必须就绪的外部依赖接入 |
| `docker-compose.prod.yml` | 生产编排 | 同一必需依赖契约、生产镜像和受控依赖清单；按部署文档/工作流执行 |

不要用本地编排推断生产依赖已经验证。生产步骤见[部署说明](./docs/operations/deployment.md)。

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

配置中心生成的 `config/generated/local/.env` 不进入 Git。平台启动后再从
“启动后复检”确认后端深度 readiness、前端与 Celery worker，再从“模型配置”
页面按需配置 LLM。完整依赖、端口和例外边界见
[开发环境](./docs/development/setup.md) 与
[config/README.md](./config/README.md)。

## 仓库结构

```text
OntologyBuild/
├── backend/                 FastAPI、领域模块、Alembic 和后端测试
├── frontend/                React 应用、纯逻辑单元测试与 Playwright 旅程
├── config/                  独立的本地配置中心
├── docs/
│   ├── product/             需求、业务流程与导航地图
│   ├── architecture/        模块边界、数据流和 ADR
│   ├── development/         开发、测试和编码规范
│   ├── operations/          配置、部署、回滚和排障
│   ├── iterations/          每次迭代的实施与验证证据
│   ├── reference/           深度技术参考
│   └── archive/             不再作为依据的历史文档
├── scripts/                 CI、部署和受控数据脚本
├── docker/                  容器初始化与运行资源
├── test_data/               受版本控制、已分类的测试 fixture
├── .claude/                 无秘密、可复制的本地 Agent 启动示例
├── .github/workflows/       PR 验证与自动部署
├── .coze                    Coze 运行器兼容配置
├── .env.example             本地/容器环境变量模板
├── production.dependencies.env
│                            当前自动部署使用的生产依赖清单（受控临时例外）
├── production.dependencies.example.env
│                            后续迁移与人工校验使用的无秘密模板
├── AGENTS.md                强制开发和交付准则
├── AGENT.md                 指向唯一准则的单数兼容入口
├── CONTRIBUTING.md          贡献流程与验证入口
├── SECURITY.md              安全边界与报告方式
├── CHANGELOG.md             面向版本的变更摘要
├── docker-compose.local.yml  推荐的本地核心完整栈
└── docker-compose.prod.yml   生产编排
```

后端已处于从传统横向目录向业务域迁移的中间状态。新同事应从
[统一模块地图](./docs/architecture/module-map.md) 找到当前权威实现，不要仅凭
目录名称猜测。前端平台概览已迁入 `src/features/overview/`，其他业务仍以
`pages/`、`api/`、`components/` 等现有路径为主；不得把目标结构误写成全仓
已经完成的事实。

## 标准验证

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

推送到 `nano-ontoprompt` 会触发验证与自动部署。当前工作流继续读取仓库中
已跟踪的 `production.dependencies.env`，并使用既有 SSH Secrets 完成部署；
这是一项经仓库所有者确认的临时兼容决策，避免本轮目录治理改变自动部署
前置条件。日常开发不得回显或擅自修改其中的值。

后续迁移到逐项 GitHub Environment Secrets/Variables 时，应作为独立运维
变更完成配置、预检和回滚验证；仅删除当前文件不等于清理 Git 历史。详见
[SECURITY.md](./SECURITY.md)。
