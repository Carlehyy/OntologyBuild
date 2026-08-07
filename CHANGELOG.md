# Changelog

本项目按迭代记录维护详细工程证据，本文件仅保留面向发布的结果摘要。

## Unreleased

### Security

- 明确现有生产依赖清单是仓库所有者批准的临时兼容例外；本轮不改变自动部署
  的配置来源，后续秘密迁移作为独立运维变更处理。
- 将个人启动配置替换为无密钥、无绝对路径的示例。
- 旧生产环境会先把原 `SECRET_KEY` 派生的 Fernet key 固化为显式
  `ENCRYPTION_KEY`，再轮换 JWT 签名密钥；不重写 PostgreSQL 或 API Hub 密文，
  避免直接换密钥导致存量配置不可读。

### Documentation

- 建立版本化开发准则、需求/ADR/迭代索引和按目录下钻的 README 体系。
- 增加从导航、React 路由、后端权限和 RBAC 测试交叉核验的平台契约基线。
- 依据当前源码重建核心数据生产、本体版本发布、发布后刷新、Sentinel/Action
  与 Event Registry 的业务流程及实现索引。
- 统一稳定版运行依赖说明：正常启动必须真实就绪 PostgreSQL、Redis/Celery
  worker、Neo4j、MinIO 和 n8n；Chromium CDP 必须配置并纳入深度 readiness，
  其暂时不可达不终止 API 进程；同时明确测试/API Hub/历史对象兼容例外。

### Changed

- 本体管理卡片新增“对话”入口，可通过带 `ontology_id` 的 Hash 深链进入本体
  助手并自动选择当前发布版本；没有当前发布版本时入口保持禁用。
- 平台启动与深度 readiness 改为必需依赖 fail-closed；LLM 改为平台启动后由
  管理员在模型配置页按需配置。
- 非测试 API 在任何后台 worker 启动前探测必需依赖、初始化 Neo4j 索引并修复
  持久化投影围栏；Chromium CDP 仅允许进程存活用于诊断，深度 readiness 仍
  fail closed。
- 本体 SQL/Formal→Neo4j 写入统一为带稳定 ID、数量/端点校验和跨进程锁的全量
  重建；发布、回滚、Mapping、Action、兼容写入口和项目删除共用耐久围栏。
- 本体关键词及统一 keyword 搜索继续使用 PostgreSQL；语义搜索与统一
  semantic 模式明确返回 `501 semantic_search_unsupported`。
- 平台概览改由应用直接装配 canonical router，并把测试归入
  `backend/tests/platform/`；旧 import facade 继续保留。
- FastAPI 健康检查、生命周期和数据库 seed 从 `main.py` 收口到
  `app/bootstrap/`；API Hub、datasets、pipelines、Formal、Mapping、
  Sentinel、versions、Agent Runtime、Super Assistant、Exploration、
  Event Registry、模型配置和 web search 按稳定职责拆入 canonical service，
  HTTP router 保留鉴权、协议适配和兼容注入。
- Pipeline A/B/C 纯执行与同步触发分别进入 `route_executor.py` 和
  `trigger_service.py`，旧 `engine.py` 保持兼容 facade；Settings 保留的 QwenPaw
  Agent 配置和 n8n 工作流配置分别由两个同域 service 承接。
- Data Steward、Super Assistant、Agent Workbench 与 Settings 在现有页面
  业务域内拆成“页面编排 + 同域组件”，没有改变路由、menu key 或公开 API。
- 复盘修正 Data Steward 附件清空时机、Tailwind 全局 token、动态 Sentinel
  发布上下文读取和报告模板查询，使其恢复为整理前的交互与执行语义。
- 后端测试按业务域归档，`tests/v2/` 继续作为明确的 API/runtime v2 契约族。
- API Hub 权限边界测试复用统一平台数据库 fixture，消除 clean runner 上的
  测试顺序和默认 SQLite 状态依赖。
- 资产湖离线 E2E 从同一 UTC 原值计算浏览器本地时间断言，不再把东八区显示
  结果写死为所有 runner 的期望。
- 前端 49 个 Playwright spec 明确分为 mocked、stack、external 三组。
- 手工浏览器脚本的资源定位和运行证据统一指向仓库根与 `.artifacts/`。
- Mapping 显式业务 `id/name` 不再被旧实体信封误过滤；Neo4j 派生投影在保留
  稳定图身份的同时安全承载冲突字段、嵌套 JSON 和超 Int64 整数，并以完整
  属性替换消除增量写入后的旧字段残留。

### Infrastructure

- 建立 PR 级文档、仓库卫生、后端、配置中心和前端验证门禁，并在自动部署前
  重复执行。
- 前端源码门禁增加递归 feature/page-domain 边界检查；后端增加 bootstrap、
  dataset、pipeline、formal、versions 及复杂聚合路由的自动依赖方向守卫。
- 部署目录在任何远端删除/解包前经过统一安全校验；镜像摘要严格模式从最终
  运行配置读取，并允许显式进程环境覆盖。
- 部署上传包改由受测试的运行时白名单生成，排除文档、测试、fixture、E2E
  源码和过程产物。
- 部署源码包先上传再替换；远端 `.env` 在清理源码时始终原地保留，不再通过
  固定 `/tmp` 文件搬运，避免中断后遗失或恢复陈旧密钥。
- 首次生成 `.env` 需要显式确认全部持久存储为空；部署目录、`.env` 软链接和
  非 Git 非空目录均失败关闭，不会被源码更新路径递归覆盖。
- CI 在真实 PostgreSQL schema 与临时 API Hub SQLite 上验证全部 13 个持久密文
  位置，并上传不含密钥、密文或连接串的 0600 证据报告。
- 自动部署继续使用现有生产依赖清单与 Repository SSH Secrets，不要求本轮
  额外迁移 GitHub Environment；新增配置来源回归守卫。
- 前端、后端、Celery 和浏览器生产镜像使用清理后的构建上下文。

### Removed

- 退役系统设置中的规则设置、提示词模板和旧开放接口，以及它们专用的 v1
  本体文件/execute、v2 extraction、22 个 OpenAPI operation 和五张数据库表；
  API Hub、MinIO、Plugin 社区、超级助手 MCP 与手工/业务探索建模保持不变。
- 移除 ChromaDB 依赖、向量查询投影和语义检索运行链路。
- 移除平台 PostgreSQL→SQLite、Celery→API 线程、Neo4j→NetworkX/SQL 图、
  MinIO→本地对象存储的静默运行时降级。
- 移除已确认无运行时消费者的截图、HTML/JSON 测试结果等过程产物。
- 前端依赖管理统一为 npm，移除未被构建与 CI 使用的 Bun/pnpm 锁文件。
