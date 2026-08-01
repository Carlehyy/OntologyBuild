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
| 事件登记 | `backend/app/events/` |
| 数据通道 | `backend/app/data_channel/` |
| 接口代理 | `backend/app/api_hub/` |
| Plugin 社区适配 | `backend/app/community/`（Skill 社区当前仅前端占位） |
| 模型配置 | `backend/app/model_configs/` |
| 系统设置 | `backend/app/settings/` |
| 鉴权与收件箱 | `backend/app/auth/`、`backend/app/inbox/` |

`backend/app/routers/`、`models/`、`schemas/`、`services/` 以迁移期兼容层为主，
但仍有少量当前实现，例外台账见
`docs/architecture/backend-modules.md`：

- 不得假设整个目录都可删除；
- 除例外的修复外，禁止在兼容层新增业务实现；
- 新代码直接导入 canonical package；
- 删除兼容入口前必须证明内部零引用、测试零 patch、Celery 旧任务兼容、
  Alembic 历史可运行。

前端目标由 `frontend/src/app/`、`frontend/src/features/`、
`frontend/src/shared/` 三层组成，但当前仍以 `pages/api/components` 等路径
为权威。只有某业务域的迁移 ADR 获批并建立目标骨架后，该业务域的新功能才
进入 `frontend/src/features/<capability>/`；此前继续维护现有路径，不得制造
第三套并行实现。

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

1. 从 `docs/README.md` 和 `docs/product/navigation-business-map.md` 确认业务域。
2. 检查受影响入口、调用方、测试、配置、迁移、部署脚本和文档。
3. 在 `docs/iterations/` 新建或更新迭代记录。
4. 将“原样移动”“导入路径调整”“逻辑重构”“格式化”分开提交。
5. 先运行受影响测试，再运行本表要求的完整门禁。
6. 在 PR 中记录执行命令、结果、未执行项及原因。

禁止以“只是移动文件”为理由跳过测试。Python import、monkeypatch 路径、
相对 fixture 路径、Vite alias、Docker context 和 Compose 相对路径都会因
移动而失效。

## 4. 测试门禁

### 所有源码变更

```bash
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
- 本体映射/发布、Sentinel 执行动作、生产部署与回滚。

真实验收必须在隔离的 staging/canary 环境执行，证据作为 CI artifact，
不得提交截图、trace、数据库或运行结果到 Git。

## 5. 安全与仓库卫生

- 除仓库所有者已明确批准的现有 `production.dependencies.env` 临时兼容例外
  外，禁止提交真实密码、token、API key、Cookie、证书或生产连接串。
- 禁止提交个人绝对路径、个人 launch 配置和历史 worktree 路径。
- 日常功能或重构不得修改、复制、回显 `production.dependencies.env`；后续
  迁移和历史处理必须作为独立运维变更执行，不能混入普通功能提交。
- fixture 必须确定、最小且脱敏；真实业务数据不得进入测试目录。
- 截图、trace、HTML report、coverage 和结果 JSON 写入 `.artifacts/`。
- 发现上述临时例外之外的新秘密进入 Git 后，立即停止传播并通知维护者轮换；
  仅删除当前文件不等于完成处置，历史清理必须单独协调。

## 6. 文档责任

- `README.md`：三分钟项目入口和标准启动方式。
- `docs/product/requirements/`：要实现什么。
- `docs/architecture/` 与 ADR：为什么这样设计。
- `docs/development/`：如何开发和验证。
- `docs/operations/`：如何配置、部署、监控和回滚。
- `docs/iterations/`：每次实际改了什么及其测试/上线证据。
- `CHANGELOG.md`：面向发布的结果摘要。

功能、配置、部署或目录发生变化时，代码与对应文档必须在同一个 PR 更新。
