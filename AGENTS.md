# OntologyBuild 开发与交付准则

本文件是仓库级强制规范，适用于人工开发者和编码 Agent。目录内如存在
更具体的 `AGENTS.md`，只能补充本文件，不能降低这里的质量与安全要求。

## 1. 项目与结构原则

OntologyBuild 是“本体即服务”平台。前端导航是代码发现入口，后端以稳定
业务能力作为模块边界，不按每个页面或 Tab 机械拆包。

当前后端优先业务域如下：

| 能力 | Canonical package |
|---|---|
| 平台概览 | `backend/app/platform/` |
| 超级助手 | `backend/app/super_assistant/` |
| 业务探索 | `backend/app/exploration/` |
| 本体、本体助手、治理与哨兵 | `backend/app/ontologies/` |
| 世界模型（演化层：推演模型/推演服务/调用记录） | `backend/app/world_model/` |
| 事件登记 | `backend/app/events/` |
| 数据通道 | `backend/app/data_channel/` |
| 接口代理 | `backend/app/api_hub/` |
| Plugin 社区适配 | `backend/app/community/`（Skill 社区当前仅前端占位） |
| 模型配置 | `backend/app/model_configs/` |
| 系统设置 | `backend/app/settings/` |
| 鉴权与收件箱 | `backend/app/auth/`、`backend/app/inbox/` |

`backend/app/routers/`、`models/`、`schemas/`、`services/` 以迁移期兼容层为主：

- 不得假设整个目录都可删除；
- 除例外台账内的修复外，禁止在兼容层新增业务实现；
- 新代码直接导入 canonical package；
- 删除兼容入口前必须证明内部零引用、测试零 patch、Celery 旧任务兼容、
  Alembic 历史可运行。

### 兼容层例外台账

下列文件仍包含真实实现、模型注册或组合逻辑，不能按纯 facade 处理：

```text
app/routers/settings.py
app/models/__init__.py
app/services/local_config_sync.py
app/services/storage_service.py
```

下列入口维持私有符号、惰性循环依赖或 monkeypatch 所依赖的模块对象身份，
不能改成普通 `import *` facade（例如 `storage_service.py` 通过 `sys.modules`
维持 canonical 模块身份）：

```text
app/routers/v2/graph.py
app/services/llm_service.py
app/services/sentinel/__init__.py
app/services/connection/sql_connector.py
app/services/v2/graph/graph_analytics.py
app/services/v2/graph/neo4j_service.py
```

注：`app/services/v2/pipeline/steps/md_to_structured.py` 曾在此台账中，
已随 canvas/route A-B-C 流水线下线（迁移 0061）连同画布转换 steps 一并
退役删除，不属于仍可依赖的兼容入口。

`app/services/model_config_selector.py` 是纯转发 facade，但部分调用方在函数
执行时从该路径导入以保留 monkeypatch/扩展点，兼容测试迁移前不得批量替换。
`app.services.llm_service._call_llm` 仍被模型配置、Agent、业务探索和数据管家
等存量调用方作为 patch seam 使用，不能移除。

退役兼容入口的顺序必须是：调用方改用 canonical import → 加边界检查 → 保留
显式 facade 跑完整回归 → 确认运行时、迁移和外部脚本零依赖 → 最后删除 facade。
`backend/tests/architecture/` 下的边界守卫测试编码了已完成迁移并经兼容测试
证明的事实；尚未完成迁移的 facade 不会被笼统判定为可删除。函数内依赖纳入
统计时，当前唯一依赖环例外是 `sentinels.cdc`、`sentinels.engine`、
`sentinels.dynamic_service` 三节点四边，架构测试拒绝新增节点或边。

前端目标由 `frontend/src/app/`、`frontend/src/features/`、
`frontend/src/shared/` 三层组成，但当前仍以 `pages/api/components` 等路径
为权威。某业务域迁入 `frontend/src/features/<capability>/` 必须经维护者批准
并先建立目标骨架；此前继续维护现有路径，不得制造第三套并行实现。

前端页面 UI 开发须遵循仓库根目录 `DESIGN.md`（平台设计语言唯一事实来源）：
界面颜色取自 `frontend/src/styles/tokens.css` 的语义令牌，ECharts 图表取值统一
导入 `frontend/src/lib/echartsTheme.ts`；禁止在页域新建平行的主题常量或色板。

## 2. 不可破坏的兼容契约

纯目录整理不得擅自改变以下内容：

- HTTP 路径、方法、状态码、响应结构和 OpenAPI operation；
- `navigation.ts`、后端权限和数据库中持久化的 menu key；
- 数据库表名、约束名、Alembic revision/down_revision；
- Celery task name、队列名和定时任务标识；
- 环境变量名、Compose service/volume 名和健康检查入口；
- HashRouter 深链、公开分享/下载 URL、localStorage key；
- SSE、WebSocket、API Hub `/api-hub` 与 `/proxy` 协议。

需要改变契约时，必须作为独立功能变更处理：写需求、兼容方案、迁移、
回滚和契约测试，不能藏在目录移动里。

## 3. 开发工作流

每项变更必须依次执行：

1. 按本文件第 1 节的业务域表确认改动所属能力域。
2. 从非自动部署分支创建功能分支；一个 PR 聚焦一个业务域或一种治理动作，
   禁止直接向 `nano-ontoprompt` 推送结构调整。
3. 检查受影响入口、调用方、测试、配置、迁移和部署脚本。
4. 将“原样移动”“导入路径调整”“逻辑重构”“格式化”分开提交。
5. 先运行受影响测试，再运行本文件第 4 节要求的完整门禁。
6. 在 PR 中记录执行命令、结果、未执行项及原因。

禁止以“只是移动文件”为理由跳过测试。Python import、monkeypatch 路径、
相对 fixture 路径、Vite alias、Docker context 和 Compose 相对路径都会因
移动而失效。

## 4. 测试门禁

### 所有源码变更

```bash
git diff --check
node scripts/ci/check-markdown-links.mjs
bash scripts/ci/check-repository-hygiene.sh

cd backend
uv sync --frozen --group dev
uv run pytest -q --disable-warnings --ignore tests/v2/perf

cd ../config
uv sync --frozen --group dev
uv run pytest -q

cd ../frontend
npm ci
npm run test:unit
npm run check:feature-boundaries
npm run test:e2e:classification
npm run lint
npm run build
npm run test:e2e:mocked
```

### 后端路由、模型、任务或目录迁移

除完整后端回归外，必须验证：

- OpenAPI、路由和 RBAC 契约无意外 diff；
- `alembic heads` 只有一个 head；
- 全新数据库 `alembic upgrade head`；
- 受支持的现存数据库副本升级；
- Celery 旧任务名仍可被 worker 消费；
- API 启停不会重复启动或泄漏后台 worker。

### 前端路由、导航、权限或功能迁移

除 feature boundary、分类门禁、lint、build、mocked E2E 外，必须验证：

- 生产模块从 `src/main.tsx` 可达且没有循环依赖；
- 所有导航项和直达 URL；
- admin/editor/viewer/custom 菜单授权；
- Hash 深链、刷新、登录 `returnTo`；
- 受影响业务域的真实后端 E2E；
- 生产 Docker 构建、Nginx API/WS 代理和静态资源加载。

### 必须执行真实环境 E2E 的变更

涉及以下任一项时，mock 测试不充分：

- PostgreSQL、Redis/Celery worker、Neo4j、MinIO、n8n、Chromium CDP；
- Alembic、对象存储、文件上传下载或公开分享；
- SSE、WebSocket、浏览器接管、n8n、API Hub proxy；
- 本体映射/发布、Sentinel 执行动作、生产部署与回滚；
- 剪贴板写入、浏览器下载、系统通知等依赖浏览器/系统副作用的交互。

真实验收必须在隔离的 staging/canary 环境执行，证据作为 CI artifact，
不得提交截图、trace、数据库或运行结果到 Git。

#### 副作用类交互的验收标准（防"假成功"）

依赖浏览器/系统副作用的交互（剪贴板、下载、通知等）在普通 HTTP 部署下
没有 Clipboard API，document.execCommand('copy') 在非聚焦页面等场景会
返回成功但实际未写入剪贴板，且页面侧无法验证真实结果。因此：

- 验收必须作用在**最终结果**上：真实粘贴内容（生产环境可用 pbpaste
  等系统命令核对）或下载文件的内容校验，不得以中间信号（提示出现、
  API/execCommand 返回值）代替；
- 禁止依据无法验证的中间信号向用户宣称"已复制/已下载"；提示文案必须
  如实（如"已尝试写入剪贴板"+ 失败时的手动路径指引）；
- 此类功能必须提供不依赖单一剪贴板路径的兜底：如全文弹窗（自动全选、
  手动 Cmd+C / Ctrl+C）或文件下载入口；
- 自动化测试需断言真实结果：Playwright 可读取剪贴板（需
  clipboard-read/write 权限）与下载内容；可见性断言（toBeVisible）
  不检测遮挡，凡层叠场景须用 elementFromPoint 命中检测断言层级契约。

## 5. 安全与仓库卫生

- 除仓库所有者已明确批准的现有 `deploy/production.dependencies.env` 临时兼容例外
  外，禁止提交真实密码、token、API key、Cookie、证书或生产连接串。
- 禁止提交个人绝对路径、个人 launch 配置和历史 worktree 路径。
- 日常功能或重构不得修改、复制、回显 `deploy/production.dependencies.env`；后续
  迁移和历史处理必须作为独立运维变更执行，不能混入普通功能提交。
- fixture 必须确定、最小且脱敏；真实业务数据不得进入测试目录。
- 截图、trace、HTML report、coverage 和结果 JSON 写入 `.artifacts/`。
- 发现上述临时例外之外的新秘密进入 Git 后，立即停止传播并通知维护者轮换；
  仅删除当前文件不等于完成处置，历史清理必须单独协调。

## 6. 文档责任

仓库只维护少数长期有效的文档，功能与行为说明以代码和测试为准：

- `README.md`：三分钟项目入口和标准启动方式；
- `docs/operations/`：如何配置、部署、监控、备份和回滚；
- `docs/development/`：如何搭建本地环境并验证改动；
- 根目录 `DESIGN.md`：前端设计语言基线（色彩、排版、组件与图表规范）及外部样例拷贝规则。

变更史以 Git 提交历史为准，不单独维护 Changelog。

不为单个功能新增需求、架构、ADR 或迭代类文档；启动方式、部署流程或配置
分区发生变化时，代码与上述文档必须在同一个 PR 更新。
