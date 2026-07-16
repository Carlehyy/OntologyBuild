# nano-ontoprompt

**[English Documentation](./README.md)**

一个轻量级、借鉴 Palantir Foundry 设计的领域本体构建平台。接入数据源,经过可视化转换管道处理,将清洗后的数据集映射为实体类型,最终生成可探索的知识图谱——包含实体、关系、逻辑规则与可执行动作。

支持两条构建路径:

- **Pipeline Mapping**(v2)— 完整数据集成链路:`数据接入 → 原始存储 → 转换 → Curated 数据集 → 本体映射`
- **简易 LLM 提取**(v1)— 上传文档,选择提示词和模型,一键提取知识图谱

---

## 什么是本体(Ontology)?

本体是特定领域知识的形式化表示——一套共享的概念词汇及概念间的关系。它是把原始数据变成机器可读、可查询知识的结构化骨架。

在 nano-ontoprompt 中,每个本体由以下构件组成:

| 构件 | 含义 | 示例 |
|---|---|---|
| **实体(Object Type)** | 从 Curated 数据集映射出的核心概念,每行数据一个节点 | `Supplier`、`PurchaseOrder` |
| **关系(Link Type)** | 实体间的边,由外键检测与跨数据集值重叠推断 | `PurchaseOrder -[HAS_SUPPLIER]-> Supplier` |
| **逻辑规则(Logic)** | 规则层:从 schema 约束、质量报告、状态字段和图关系中发现的映射/校验/状态/推断/自动化规则 | `amount > 0`、`库存状态` 状态机 |
| **动作(Action)** | 可执行行为层:基于实体类型与关系生成的 CRUD、状态流转、链接维护动作,含提交校验与审计快照 | `Approve Record`、`Link Order to Supplier` |

**典型场景:** 供应链知识建模、医疗概念提取、金融合规规则、法律文档结构化——任何需要把异构数据转化为结构化知识的领域。

---

## 功能特性

### 数据管道(v2)
- **可视化管道构建器** — 画布上编排连接器/存储器/转换器/输出节点,逐节点状态与数据预览
- **三条转换路径** — A:结构化(CSV/Excel,schema 推断 + 清洗);B:半结构化(JSON 拍平 / XML 解析);C:非结构化(文档 → Markdown → LLM 或规则结构化提取)
- **连接器** — 文件上传、MySQL/PostgreSQL、MongoDB、REST API(支持增量同步)
- **Curated 数据集** — 质量评分、人工审核(仅管理员可审批)、版本管理

### 本体(v2)
- **自动映射引擎** — 数据集→实体类型、列→属性、外键→关系类型,自动推断基数
- **跨数据集关系推断** — 精确外键匹配、值格式容错(`SUP-001` ↔ `SUP001`)、备用键匹配(如文档中提到的公司名直接连到 Supplier 实体)、可选 LLM 辅助语义链接(`ENABLE_LLM_FK_DETECTION=1`)
- **Logic & Actions 发现机制** — 规则与动作从映射、schema 约束、状态字段和关系中自动发现,经 草稿 → 审核 → 发布 流程上线
- **知识图谱** — Cytoscape.js 交互式网状视图,可一键隐藏孤立节点;Neo4j 可用时由其驱动,否则回退 SQLite
- **搜索** — 关键词搜索(ChromaDB 不可用时回退 SQL)与语义搜索(ChromaDB)

### 质量审查(ReAct Agent)
- **LLM 驱动多步审查** — AI Agent 系统检查本体质量:孤立实体、断链引用、缺失关系、低覆盖实体类型
- **工具调用架构** — 8 个内置检查工具(摘要、覆盖率、引用校验、模式推断)可链式调用
- **审查报告** — 按严重级别分类的问题及修复建议,持久化为审计任务

### 数据管家（会话浏览器 + n8n）
- **会话文件隔离** — 每个数据管家会话拥有独立目录；上传件、网页下载件和解析文本不能跨会话访问，可一键打包，浏览器 Cookie/登录态不会进入压缩包
- **Office/PDF 资料读写** — Word、PowerPoint、Excel、PDF、Markdown 等沿用平台文档转换链；Agent 可在当前会话内创建、追加、另存新版本或按用户明确指令删除，但不能接收或访问会话外路径
- **人工登录接管** — 数据管家页的“实时浏览器”弹窗展示同一 CDP 会话画面；账号密码由用户直接输入，Agent 不读取、不代填密码框
- **三类浏览器来源** — 每个会话可独立选择平台 Docker 浏览器、管理员配置的加密远程 CDP，或用户电脑上的浏览器助手；切换来源不会影响其他会话
- **接口与分页发现** — 记录同会话浏览器的 XHR/fetch/文件响应，脱敏展示认证头，并识别 page/offset/cursor 等分页模式及返回结构
- **授权下载与接口代理** — 在浏览器登录态下重放已捕获的 GET 文件请求；内网接口可登记到接口代理，由 n8n 通过受令牌保护的 `/api-hub/proxy/{id}` 间接调用
- **普通 HTTP 接口发布** — 已登记接口可发布为稳定的 `/proxy/{公开路径}`；调用方使用独立密钥，平台按配置开放 Query、业务 Header 和 Body，并视情况自动注入 W3 登录态
- **受治理的 n8n 编排** — 数据管家仍只新建和编排未发布、未启用的工作流；试跑、字段封版、发布与启用继续由流水线编辑向导把关

### 平台
- **LLM 提取** — 支持 OpenAI、Anthropic 及任何 OpenAI 兼容模型;多道防线杜绝模糊关系类型
- **LiteLLM 代理** — 可选 LiteLLM 集成,统一管理多 LLM 提供商的 API Key 与用量
- **提示词管理** — 领域提示词版本化管理,一键生成模板
- **数据管理** — 结构化数据浏览器,含 Curated 数据集详情面板、行级编辑与审核流程
- **导出** — JSON、YAML、CSV、Turtle (RDF)、HTML
- **优雅降级** — Neo4j / MinIO / ChromaDB / Redis 全部可选;缺失时自动回退 SQLite + 本地文件存储 + 同步执行
- **多语言界面** — 中英文切换
- **用户管理** — JWT 认证,admin/editor 角色;Curated 审批仅限管理员
- **开放接口(MCP)** — 管理员可在 *系统设置 → 开放接口* 勾选后端接口,通过 `/mcp` 以 Streamable HTTP 暴露给外部 Agent;MCP 调用沿用 OntoPrompt Bearer JWT,不绕过原有鉴权

---

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 18、TypeScript、Vite、Tailwind CSS、Cytoscape.js |
| 后端 | FastAPI、SQLAlchemy、Alembic |
| 元数据库 | SQLite(开发)/ PostgreSQL(生产) |
| 对象存储 | MinIO(可选,本地文件回退) |
| 图数据库 | Neo4j(可选,SQLite 回退) |
| 向量库 | ChromaDB(可选) |
| 任务队列 | Celery + Redis(可选,同步执行回退) |
| LLM 客户端 | OpenAI SDK、Anthropic SDK |
| LLM 代理 | LiteLLM(可选) |
| MCP 暴露 | Model Context Protocol (mcp), Streamable HTTP |

---

## 架构指南

深入理解 Ontology-as-a-Service 架构设计 — 包括 Object/Link/Function/Governance 设计模式、多租户隔离、临床筛查工作流、生产部署 Checklist — 详见 **[ONTOLOGY.md](./ONTOLOGY.md)**（2727 行）。

---

## 快速开始

### 方式一 — Docker Compose(完整 v2 栈)

```bash
git clone https://github.com/jingw2/nano-ontoprompt.git
cd nano-ontoprompt
cp .env.example .env          # 生产环境务必修改密钥
docker compose -f docker-compose.v2.yml up --build
```

将启动 PostgreSQL、Redis、Neo4j、MinIO、ChromaDB、会话浏览器、后端与前端。轻量 v1 栈可改用 `docker-compose.yml`。

打开 [http://localhost:5173](http://localhost:5173),默认账号 `admin / admin123`。

### 方式二 — 手动启动(最小化,无需外部服务)

**前置要求:** Python 3.11+、Node.js 18+

```bash
# 后端
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head                                  # 开发模式也可依赖启动时自动建表
uvicorn app.main:app --reload --port 8000

# 前端(另开终端)
cd frontend
npm install
npm run dev
```

Neo4j / MinIO / ChromaDB / Redis 均为可选——缺失时系统自动使用 SQLite 图谱回退、本地文件存储与同步管道执行。

手动启动时如需数据管家的实时浏览器，还要准备一个开放 CDP 的 Chromium，并设置
`STEWARD_BROWSER_CDP_URL`（默认 `http://localhost:9222`）。生产环境应同时配置
`API_HUB_SYSTEM_MCP_TOKEN`，在 n8n 中以 Header Auth 凭据保存该令牌；不要把令牌直接写入工作流 JSON。

会话浏览器默认最多同时保留 8 个 BrowserContext、每个用户最多 3 个，空闲 15 分钟后自动保存
登录态并回收；再次访问该会话时会从会话隔离目录恢复登录态并重新分配 Context。可通过
`STEWARD_BROWSER_MAX_SESSIONS`、`STEWARD_BROWSER_MAX_SESSIONS_PER_USER`、
`STEWARD_BROWSER_IDLE_TIMEOUT_SECONDS` 调整。实时浏览器窗口打开期间，该会话进入人工接管状态，
Agent 的页面读取、点击、输入及下载都会暂停；同一会话内的所有变更操作会串行执行。当前调度器是
进程内状态，生产后端须保持单 worker；如需多 worker/多副本，应先增加基于 Redis 的会话到 worker
粘性路由和分布式租约。

如果目标站点的 WAF 拒绝云服务器出口，可在“实时浏览器 → 浏览器来源”中添加“我的电脑”。平台会
生成一次性配对令牌和 Node.js 22+ 助手脚本；助手只在本机回环地址开放 Chrome/Edge CDP，并主动向
平台建立 WebSocket 隧道，不需要公开 9222 端口。生产环境必须先为平台配置 HTTPS/WSS，否则助手会
拒绝连接。远程 CDP 拥有完整浏览器控制权，仅管理员可配置，其地址和认证请求头会加密入库。

需要对真实 LLM 与外部 n8n 做破坏性端到端验收时，可运行以下脚本。密钥只从终端隐藏输入
（或临时环境变量 `LLM_API_KEY` / `N8N_API_KEY`）读取；脚本使用隔离数据库，创建、激活、
执行并最终删除唯一命名的临时工作流：

```bash
cd backend
python scripts/steward_live_e2e.py \
  --n8n-api-url https://n8n.example.com/api/v1 \
  --llm-api-base https://api.deepseek.com \
  --models deepseek-v4-pro deepseek-v4-flash
```

单独验收“n8n → 接口代理 → AI HOT cursor 分页”链路可运行：

```bash
cd backend
python scripts/steward_proxy_live_e2e.py \
  --n8n-api-url https://n8n.example.com/api/v1
```

脚本通过终端隐藏输入 n8n API Key，使用短期随机代理令牌和临时公网隧道；结束时会停用并删除
临时 n8n 工作流、代理数据库与隧道。运行环境需要 Node.js/npm，以便临时执行 localtunnel。

生产环境的 n8n 地址策略要求 HTTPS；仅开发验收允许访问公网 HTTP n8n 地址。

普通 HTTP 发布的真实公网验收不需要 n8n 或私有凭据，可运行：

```bash
cd backend
python scripts/api_hub_http_proxy_live_e2e.py
```

脚本使用临时数据库和短期随机调用密钥，启动真实本地代理服务，通过 `/proxy/{slug}`
连续调用 AI HOT 公共接口的两页 cursor 数据，并校验调用审计和密钥脱敏。

---

## 使用流程(Pipeline Mapping 路径)

1. **配置提供商** — *模型 → 添加提供商*:填写提供商、API Key、Base URL,并标记用途(提取 / VLM / FK 检测)。
2. **创建管道** — *数据管道 → 新建*:在画布上编排 连接器/存储器/转换器/输出 节点,挂载数据文件,选择转换路径,点击**运行**。
3. **审核数据** — *数据管道 → Curated*:查看质量评分与预览,管理员审批通过。
4. **创建本体** — *本体 → 新建*,构建方式选 **Pipeline Mapping**:选择已审批的 Curated 数据集,逐个映射为实体类型并指定主键。
5. **构建** — 系统自动推断跨数据集关系,并发现 Logic 规则与 Actions 草稿。
6. **探索** — *知识图谱* 标签页查看网状结构;*实体 / 逻辑规则 / 动作* 标签页查看详情、完成审核并发布。
7. **导出** — 在 *基本信息* 标签页导出 JSON、YAML、CSV、Turtle (RDF) 或 HTML。

**简易 LLM 提取**路径:新建本体时选 `simple_llm` 模式,在 *文件* 标签页上传文档,选择提示词与模型后运行提取。

---

## 项目结构

```
nano-ontoprompt/
├── backend/
│   ├── alembic/
│   ├── app/
│   │   ├── main.py
│   │   ├── shared/              # 公共基础设施 (配置、数据库、依赖、加解密)
│   │   ├── platform/            # 平台概览
│   │   ├── auth/                # 认证与用户
│   │   ├── data_channel/        # 数据通道
│   │   │   ├── pipelines/       #   数据流水线 (引擎、编译、处理步骤)
│   │   │   ├── connections/     #   数据连接器 (文件/SQL/Mongo/REST)
│   │   │   ├── datasets/        #   数据集
│   │   │   ├── curated/         #   Curated 数据 (质量、审核)
│   │   │   ├── sync_tasks/      #   数据任务池 (调度、同步引擎)
│   │   │   └── transforms/      #   数据变换 (提取、测试数据)
│   │   ├── ontologies/          # 本体管理
│   │   │   ├── projects/        #   本体项目
│   │   │   ├── entities/        #   对象类型 (实体)
│   │   │   ├── relations/       #   实体关系
│   │   │   ├── logic/           #   逻辑规则
│   │   │   ├── actions/         #   可执行动作
│   │   │   ├── formal_modeling/ #   正规本体建模 (Palantir 风格)
│   │   │   ├── sentinels/       #   哨兵引擎
│   │   │   ├── graph/           #   知识图谱 (Neo4j/Cypher)
│   │   │   ├── mappings/        #   本体映射
│   │   │   ├── audit/           #   质量审查
│   │   │   ├── export/          #   导出
│   │   │   ├── files/           #   文件管理
│   │   │   ├── extraction/      #   LLM 提取
│   │   │   ├── inference/       #   推理结果
│   │   │   ├── versions/        #   版本记录
│   │   │   └── attribute_schemas/ # 属性模式
│   │   ├── rag/                 # 智能问答
│   │   ├── model_configs/       # 模型配置
│   │   ├── settings/            # 系统设置
│   │   │   ├── rules/           #   规则设置
│   │   │   ├── users/           #   用户管理
│   │   │   ├── prompts/         #   提示词模板
│   │   │   ├── agents/          #   智能体配置
│   │   │   ├── workflows/       #   工作流配置
│   │   │   └── open_interfaces/ #   开放接口 (MCP)
│   │   └── tasks/               # Celery 异步任务
│   ├── scripts/
│   │   ├── maintenance/         # 运维维护脚本
│   │   ├── migrations/          # 一次性数据迁移脚本
│   │   ├── demos/               # 演示脚本
│   │   └── dev/                 # 开发/调试脚本
│   └── tests/
├── frontend/
│   └── src/
│       ├── pages/pipelines/
│       ├── pages/ontologies/
│       ├── pages/data-management/
│       ├── pages/settings/
│       └── api/
├── scripts/
│   └── data/
├── docker-compose.yml
├── docker-compose.v2.yml
├── litellm_config.yaml
├── ONTOLOGY.md
└── test_data/
```

---

## 环境变量

完整列表见 `.env.example`,核心配置:

```env
ENVIRONMENT=development        # 设为 production 时, 默认密钥未修改将拒绝启动
DATABASE_URL=sqlite:///./ontoprompt.db
SECRET_KEY=change-me
ENCRYPTION_KEY=                # Fernet 密钥, 用于加密存储的 API Key
FIRST_ADMIN_USER=admin
FIRST_ADMIN_PASSWORD=admin123

# 可选服务 (缺失时优雅降级)
REDIS_URL=redis://localhost:6379/0
NEO4J_URI=bolt://localhost:7687
MINIO_ENDPOINT=localhost:9000
CHROMA_HOST=localhost

# 上传限制
MAX_UPLOAD_MB=200
ALLOWED_UPLOAD_EXTENSIONS=csv,xlsx,xls,json,xml,pdf,docx,doc,pptx,ppt,md,txt

# 可选: LLM 辅助语义外键检测 (需先配置模型)
ENABLE_LLM_FK_DETECTION=0
```

---

## 故障排查

**前端容器报 `AggregateError [ECONNREFUSED]`,登录失败。**
拉取最新代码 — Vite 代理已通过 `VITE_API_PROXY_TARGET` 在 Docker 内指向 `http://backend:8000`。然后重建: `docker compose up -d --build frontend`。

**已有部署用 `admin / admin123` 登录失败。**
admin 用户用旧的默认密码 seed,需要重置:

```bash
# Docker
docker compose exec backend python scripts/maintenance/reset_admin_password.py

# 手动启动
cd backend && python scripts/maintenance/reset_admin_password.py
```

可选参数: `--user <username>` (默认 `admin`)、`--password <new_pwd>` (默认 `admin123`)。

**LLM 提取被 OOM-kill(macOS 或低内存环境)。**
并行 LLM 提取在内存有限的机器上可能耗尽资源。代码已默认改为串行提取(`max_workers=1`)。如仍遇到问题,可逐域提取,或减少每次提取上传的文件数。

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=jingw2/nano-ontoprompt&type=Date)](https://star-history.com/#jingw2/nano-ontoprompt&Date)

---

## 许可证

MIT
