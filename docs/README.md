# OntologyBuild 文档

新同事建议按下列顺序阅读：

1. [项目概览](./product/overview.md)：平台解决什么问题；
2. [核心数据流](./architecture/data-flow.md)：数据生产、定义发布和发布后刷新；
3. [核心运行契约](./product/requirements/0002-core-data-ontology-runtime-contract.md)：
   状态门、权限、幂等、发布围栏和回滚；
4. [导航与业务能力](./product/navigation-business-map.md)：界面入口属于哪个业务域；
5. [统一模块地图](./architecture/module-map.md)：从功能定位源码、API 和测试；
6. [系统架构](./architecture/overview.md)：进程、存储和外部依赖；
7. [本地开发](./development/setup.md)与[测试指南](./development/testing.md)：
   如何启动并证明改动安全；
8. [配置说明](./operations/configuration.md)与[部署说明](./operations/deployment.md)：
   必需运行依赖、无降级边界和自动部署；
9. [迭代记录](./iterations/README.md)：最近发生了什么。

仓库级强制开发规则见 [AGENTS.md](../AGENTS.md)。

## 目录树

```text
docs/
├── product/          产品目标、需求、术语与业务流程
├── architecture/     模块地图、数据流和架构决策
├── development/      开发环境、编码、测试与责任
├── operations/       配置、部署、回滚、备份与排障
├── iterations/       每次实际变更与验证证据
├── reference/        领域和引擎深度参考
└── archive/          不再作为当前依据的历史资料
```

每个子目录都有自己的 `README.md`。需求、ADR 和迭代记录分别使用各自模板，
不要把“期望行为”“技术取舍”和“实际实施证据”混写在一个文档里。

## 完整目录

### 产品

- [产品目录](./product/README.md)
- [产品概览](./product/overview.md)
- [导航与业务能力](./product/navigation-business-map.md)
- [需求目录与模板](./product/requirements/README.md)
- [当前导航、路由与 RBAC 契约](./product/requirements/0001-current-platform-contract.md)
- [核心数据、本体发布与运行时契约](./product/requirements/0002-core-data-ontology-runtime-contract.md)

### 架构

- [架构目录](./architecture/README.md)
- [系统架构](./architecture/overview.md)
- [统一模块地图](./architecture/module-map.md)
- [核心数据流](./architecture/data-flow.md)
- [后端模块边界](./architecture/backend-modules.md)
- [前端路由与权限](./architecture/frontend-routing.md)
- [ADR 目录与模板](./architecture/adr/README.md)
- [退役遗留文档本体抽取 ADR](./architecture/adr/0003-retire-legacy-document-ontology-extraction.md)

### 开发

- [开发目录](./development/README.md)
- [本地开发](./development/setup.md)
- [测试与验收](./development/testing.md)
- [编码规范](./development/coding-standards.md)
- [模块责任与评审](./development/module-ownership.md)
- [前端视觉约束](./development/frontend-design-system.md)

### 运维

- [运维目录](./operations/README.md)
- [配置与秘密](./operations/configuration.md)
- [自动部署](./operations/deployment.md)
- [回滚](./operations/rollback.md)
- [备份与恢复](./operations/backup-restore.md)
- [排障](./operations/troubleshooting.md)

### 迭代与参考

- [迭代规则与模板](./iterations/README.md)
- [当前平台概览与超级助手导航临时隐藏迭代](./iterations/2026/2026-08-07-temporarily-hide-overview-and-super-assistant-navigation.md)
- [当前运行密钥无损解耦迭代](./iterations/2026/2026-08-03-preserve-runtime-secret-migration.md)
- [当前遗留文档本体抽取退役迭代](./iterations/2026/2026-08-02-retire-legacy-document-ontology-extraction.md)
- [当前稳定版依赖迭代](./iterations/2026/2026-08-02-required-runtime-dependencies.md)
- [当前仓库治理迭代](./iterations/2026/2026-07-30-repository-governance.md)
- [参考目录](./reference/README.md)
- [Ontology 架构参考](./reference/ontology.md)
- [Sentinel Engine 参考](./reference/sentinel-engine.md)
- [归档目录](./archive/README.md)

## 按角色阅读

- 新同事：项目概览 → 核心数据流/运行契约 → 导航/模块地图 → 本地开发；
- 后端开发：核心运行契约 → 系统架构 → 后端模块边界 → 测试指南；
- 前端开发：产品概览 → 导航/模块地图 → 前端路由与权限 → 前端 README；
- 运维：配置 → 部署 → 回滚 → 备份恢复 → 排障；
- 评审者：AGENTS → 需求/ADR → 迭代记录 → 实际测试证据。

## 当前事实源

| 事实 | 权威路径 |
|---|---|
| 项目目标与启动方式 | `README.md` |
| 导航与 menu key | `frontend/src/config/navigation.ts` |
| React 路由 | `frontend/src/App.tsx` |
| 后端路由装配与生命周期 | `backend/app/main.py` |
| 服务端 menu key / RBAC | `backend/app/auth/permissions.py` |
| 数据库历史 | `backend/alembic/versions/` |
| Python 版本与后端依赖 | `backend/pyproject.toml`、`backend/uv.lock` |
| 前端命令与依赖 | `frontend/package.json`、`frontend/package-lock.json` |
| 浏览器测试分组 | `frontend/playwright.*.config.ts` |
| 核心状态门与发布契约 | `backend/app/data_channel/`、`backend/app/ontologies/` 及对应测试 |
| 推荐本地核心完整栈 | `docker-compose.local.yml` |
| 生产编排 | `docker-compose.prod.yml` |
| 自动部署 | `.github/workflows/deploy-nano-ontoprompt.yml` |
| 服务器部署行为 | `scripts/deploy-prod.sh` |
| 本地配置中心 | `config/README.md` |

正常启动的阻塞型依赖是 PostgreSQL、Redis、Celery worker、Neo4j、MinIO 和
n8n；Chromium CDP 地址配置必需，但连通失败只让深度 readiness 失败，不终止
API。LLM 在平台启动后从模型配置页按需配置。当前搜索、存储和兼容例外的准确
边界见[系统架构](./architecture/overview.md)与
[配置说明](./operations/configuration.md)。

文档描述必须来自源码、可执行配置、测试或已接受的 ADR。若这些事实互相矛盾，
应记录矛盾和验证结果，不能用推测填空。

## 文档事实验证规则

业务、架构和运维说明不能只依据 UI 文案或旧文档改写。每次更新至少执行：

1. 从当前装配入口、模型/状态机、application service 和外部适配器还原实现；
2. 用路由/RBAC、数据库迁移、任务注册、Compose/Actions 中适用的第二事实源
   交叉核对；
3. 链接能够直接断言该行为的测试；只有源码证据时明确写成“实现证据”，不能
   宣称已经自动化覆盖；
4. 对未接通、可选依赖、兼容入口和目标结构使用明确限定，不补写源码中不存在
   的流程；
5. 运行 Markdown 链接/索引检查和受影响测试，并把命令、结果、跳过项写入
   `docs/iterations/`。

文档与源码冲突时，先修正事实或新增需求/ADR，禁止为了让叙述顺畅而猜测。
