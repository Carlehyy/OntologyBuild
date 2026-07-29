# OntologyBuild

> 一个**本体即服务(Ontology-as-a-Service)**的平台基座 —— 把企业数据建模成由对象、关系、动作和规则组成的活本体,并让它在数据变化时**自动运转**。

OntologyBuild 让你在可视化图谱编辑器里编排本体结构(抽象实体 / 对象实体 / 实体关系 / 执行动作 / 激活函数,参考 Palantir Foundry 的范式),再通过**哨兵引擎(Sentinel Engine)**监听数据状态变化、判断条件、自动触发动作 —— 从"静态的数据模型"进化为"反应式的运行时"。

风险治理、合规审计、运营监控等都只是跑在这个基座之上的**场景**,而非内核本身。

---

## 1. 项目介绍

传统的数据建模产出的是一张**静态 schema**:它描述了"数据长什么样",却不会自己做任何事。OntologyBuild 解决的根本问题是 —— **让本体跑起来**。

它由两层构成:

- **建模层(图谱编辑器)**:可视化地定义对象类型、实体关系、动作、函数,并支持从外部数据源采集、映射、投影成真实的对象实例与关系实例。
- **运行层(哨兵引擎)**:监听对象实例的属性状态变化,在数据变化或定期任务时评估跨对象条件,命中后自动执行绑定的动作。采用**边沿触发**语义(参考 Foundry Automate),条件持续满足时不重复触发。

技术栈:

- **后端** — FastAPI(Python 3.12)、SQLAlchemy、Alembic、Celery
- **前端** — React + Vite + TypeScript + Tailwind
- **存储/中间件** — PostgreSQL(主)、可选 Neo4j(图)、Redis(队列)、MinIO(对象存储)、ChromaDB(向量)
- **部署** — Docker Compose,GitHub Actions 自动构建与发布

> 缺省情况下,Neo4j / MinIO / ChromaDB / Redis 均为可选 —— 不配置时,平台自动回退到 SQLite 图、本地文件存储与同步式流水线。

---

## 2. 快速开始

### 方式一 · Docker Compose(完整栈,推荐)

```bash
git clone --branch nano-ontoprompt https://github.com/Carlehyy/OntologyBuild.git
cd OntologyBuild
cp .env.example .env
docker compose -f docker-compose.v2.yml up --build
```

启动后包含 PostgreSQL、Redis、Neo4j、MinIO、ChromaDB、后端与前端。
轻量 v1 栈可改用 `docker-compose.yml`。

### 方式二 · 本地源码完整模式(推荐)

项目根目录的 `config/` 是独立的本地配置中心。它集中管理源码运行所需的端口、PostgreSQL、
Redis/Celery、Neo4j、MinIO、Chroma、Chromium CDP、n8n 与默认 LLM：

```text
Windows 11       双击 config/start.bat
Ubuntu / macOS   执行 ./config/start.sh
```

网页只监听 `127.0.0.1:8765`，并使用每次启动随机生成的本地访问令牌。所有必需服务连通性测试通过后，工具会原子生成
`config/generated/local/.env`；该目录已被 Git 忽略，不会影响 GitHub Actions 或生产部署。
后端、API Hub、Alembic 与 Vite 均从这一处读取本地配置，同时保留系统环境变量最高优先级。

生成后分别启动后端、Celery worker 和前端：

```bash
# 终端 1
cd backend
uv run python -m app.dev_server

# 终端 2
cd backend
uv run celery -A app.tasks.celery_app:celery_app worker --loglevel=info

# 终端 3
cd frontend
npm install
npm run dev
```

配置中心面向完整功能模式，固定关闭本地对象存储回退并启用 Celery。已保存的 n8n 与默认 LLM
配置会在本地开发后端启动时加密、幂等地同步到平台数据库；生产环境不会执行这项同步。
详细边界和安全说明见 [config/README.md](./config/README.md)。

### 方式三 · 本地手动启动(最小依赖,允许降级)

**前置要求:** Python 3.12、Node.js 20.19+ 或 22.13+。推荐使用
[uv](https://docs.astral.sh/uv/) 创建 Python 环境,它会在本机缺少 Python 3.12 时自动准备兼容版本。

> `backend/pyproject.toml` 与 `backend/uv.lock` 当前锁定 Python 3.12.x。不要使用系统自带的
> Python 3.9/3.10。前端使用 Vite 8 与 ESLint 10,Node.js 18 已不再兼容。

```bash
# 后端
cd backend
uv venv --python 3.12 .venv
source .venv/bin/activate                            # Windows: .venv\Scripts\activate
uv pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000           # 开发态自动建表

# 前端(另开一个终端)
cd frontend
npm ci
npm run dev
```

打开 [http://localhost:5173](http://localhost:5173),默认账号 `admin / admin123`。

后端存活检查:

```bash
curl http://localhost:8000/health/live
# 预期返回: {"status":"ok"}
```

最小开发模式无需复制根目录的 `.env.example`,也无需预先执行 `alembic upgrade head`。后端默认使用
`/tmp/ontoprompt.db` 并在启动时自动建表。根目录的 `.env.example` 主要供 Docker Compose 使用,
其中 `db`、`redis`、`minio` 等服务名不能直接用于本机进程。新配置优先使用上述 `config/`
配置中心；旧的 `backend/.env` 仍保持兼容。

启动日志中出现 `Neo4j unavailable` / `Neo4j 不可用，跳过索引初始化` 属于预期降级,不会阻止
核心平台使用。Neo4j、MinIO、ChromaDB 与 Redis 未启动时,系统分别回退到 SQLite 图谱、本地文件存储、
SQL 搜索与同步任务执行。需要数据管家的实时浏览器时,仍须额外准备开放 CDP 的 Chromium 并配置
`STEWARD_BROWSER_CDP_URL`;需要 LLM 能力时,还须配置对应提供商的 API Key。

对象存储降级会返回 `local://` URI，与 MinIO 的 `s3://` URI 明确区分；MinIO 恢复不会改变
既有对象的读取位置。生产环境必须把 `STORAGE_LOCAL_DIR` 配置到 API 与 worker 共享的持久卷
（生产 Compose 默认 `/uploads/object-storage`），详见 [DEPLOY.md](./DEPLOY.md)。
附件链接的浏览器入口不复用 n8n 网关地址：生产环境应分别配置
`PIPELINE_FILE_PUBLIC_APP_BASE_URL`（登录下载 Hash 深链）和
`PIPELINE_FILE_PUBLIC_API_BASE_URL`（匿名分享 API）为浏览器公网可达的 HTTP(S) origin。
推荐 HTTPS，且不要包含路径、账号、查询参数或片段。

> 无 Docker 的源码部署 + 公网隧道预览,见 [DEPLOY.md](./DEPLOY.md)。

---

## 3. 核心功能

### 本体建模(Foundry 风格的正规本体)
对象类型、实体关系、执行动作、激活函数、接口五类一等公民;强类型属性、主键、链接基数;支持版本与发布(草稿 / 已发布)。

### 数据采集与投影
连接外部数据源 → 采集 → 数据集 → 映射 → 投影成真实的**对象实例与关系实例**。源数据携带的引用列自动派生为关系(链接实例),多源经 `external_id` 去重合并为一张图;状态遥测按 id 逐节点更新属性(数字孪生式)。

### 普通 HTTP 接口发布
接口管理中登记的真实接口可发布为稳定的 `/proxy/<slug>` 地址。每个调用方使用可独立撤销、可设置有效期和接口权限的密钥；Query、业务 Header 与 Body 仅按发布配置转发，W3 登录态仍由平台统一维护，并可按接口一键复制完整 cURL 调用示例。

### 哨兵引擎(反应式运行时)
平台的核心运行机制 —— **监听对象状态变化 → 跨对象条件判断 → 自动执行动作**:

- **三种触发入口**:手动触发、数据变化驱动(CDC)、定期扫描(可设间隔)
- **跨对象条件**:可同时监听多个对象类型(用别名引用),沿实体关系配对,做跨对象比较;条件以"句子式"逐行编排,支持 AND / OR
- **边沿触发**:条件"刚满足"时触发一次,持续满足不重复触发;可选"条件消除时触发"用于收尾
- **多动作**:命中后依次执行一组动作;动作内部自定义副作用(建对象 / 改属性 / 建关系 / 通知 / Webhook)
- **静默模式**:仅评估并记录命中而不执行动作,供上线前观察
- 详见 [SENTINEL_ENGINE.md](./SENTINEL_ENGINE.md)

### 质量审计(ReAct Agent)
对接入数据做质量打分、问题发现与可执行的修复建议,并持久化为审计任务。

### 平台能力
JWT 认证与角色(admin / editor);本体导出(JSON / YAML / CSV / Turtle RDF / HTML);开放接口(MCP)—— 管理员可在「设置 → 开放接口」选择后端 API 并通过 `/mcp` 以 Streamable HTTP 暴露,MCP 调用复用调用方的 JWT、不绕过既有鉴权。

---

## 4. 项目结构

```
OntologyBuild/
├── backend/                      # FastAPI 后端
│   └── app/
│       ├── main.py               # 应用入口、路由挂载、启动钩子(建表 / CDC / 扫描 worker)
│       ├── config.py             # 配置
│       ├── database.py           # SQLAlchemy 引擎与会话
│       ├── deps.py               # 依赖注入(鉴权 / DB 会话)
│       ├── models/               # ORM 模型(对象/关系/动作/函数/哨兵 等)
│       ├── schemas/              # Pydantic 模式(CamelModel)
│       ├── routers/              # REST API 路由
│       ├── services/             # 业务逻辑
│       │   ├── formal/           #   正规本体:动作引擎 / 函数引擎 / 表达式沙箱
│       │   ├── sentinel/         #   哨兵引擎:评估器 / 三入口 / CDC / 扫描 worker
│       │   └── v2/               #   数据管道:采集 / 映射 / 投影 / 图同步
│       ├── tasks/                # Celery 任务
│       └── engine/               # 校验等
├── frontend/                     # React + Vite 前端
│   └── src/
│       ├── pages/                # 页面
│       ├── palantir-graph/       # 图谱编辑器(画布 / 面板 / 哨兵引擎面板 / 状态)
│       ├── api/                  # 接口封装
│       └── components/           # 通用组件
├── docker/                       # 容器初始化资源(如 Postgres init SQL)
├── scripts/                      # 部署与数据脚本(deploy-prod.sh 等)
├── test_data/                    # 多领域测试数据
├── docker-compose.yml            # v1 轻量栈
├── docker-compose.v2.yml         # 完整栈
├── docker-compose.prod.yml       # 生产栈(CI 构建/部署使用)
├── README.md                     # 本文件
├── SENTINEL_ENGINE.md            # 哨兵引擎核心逻辑说明书
├── ONTOLOGY.md                   # 本体架构设计指南
└── DEPLOY.md                     # 无 Docker 源码部署指南
```
