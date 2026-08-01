# 后端模块边界

## Canonical 业务域

| Package | 责任 |
|---|---|
| `platform` | 平台概览与运行摘要 |
| `auth` | 身份、角色、菜单授权 |
| `model_configs` | 模型提供商与选择 |
| `super_assistant` | 通用助手、Skill 和 MCP 客户端 |
| `exploration` | 业务探索会话、文档与本体草稿 |
| `ontologies` | 本体项目、建模、实例、映射、版本、发布、推演、Agent、Sentinel |
| `data_channel` | 连接、数据集、流水线、成品审核、同步任务、数据管家、文件资产 |
| `api_hub` | 接口定义、凭据、发布、代理、调用历史 |
| `events` | 事件登记、附件、审计和第三方 ingest |
| `inbox` | 跨业务收件箱契约 |
| `community` | Plugin 社区 MCP 适配入口；Skill 社区当前无同域后端 |
| `settings` | 规则、用户、提示词、工作流、存储、领域和开放接口 |

## 迁移期兼容层

`app/routers`、`app/models`、`app/schemas`、`app/services` 中多数文件仅转发到
上述业务域。兼容层存在的原因包括：

- 历史源码和测试 import；
- monkeypatch 目标路径；
- Celery task 名称；
- Alembic 或启动时模型注册；
- 外部脚本依赖。

截至 2026-07-30，下列文件仍包含真实实现、模型注册或组合逻辑，不能按纯
facade 处理：

```text
app/routers/extraction.py
app/routers/settings.py
app/models/extraction_task.py
app/models/__init__.py
app/schemas/extraction.py
app/services/local_config_sync.py
app/services/storage_service.py
```

本表是迁移台账而不是永久许可。移动其中任一实现时，必须先搜索源码、测试、
Alembic、Celery 和外部脚本的 import/patch 路径，并在同一 PR 更新本表。

下列入口虽不承载完整业务实现，也不能按普通 `import *` facade 处理；它们维持
私有符号、惰性循环依赖或 monkeypatch 所依赖的模块对象身份：

```text
app/routers/v2/graph.py
app/services/audit_service.py
app/services/llm_service.py
app/services/sentinel/__init__.py
app/services/connection/sql_connector.py
app/services/v2/graph/graph_analytics.py
app/services/v2/graph/neo4j_service.py
app/services/v2/pipeline/steps/md_to_structured.py
```

例如 `app/services/storage_service.py` 通过 `sys.modules` 维持 canonical 模块
身份，不能改成普通 star re-export 后仅凭 import 成功就认定兼容。

`app/services/model_config_selector.py` 是一个纯转发 facade，但部分调用方会在
函数执行时从该路径导入，以保留既有 monkeypatch/扩展点。`model_configs`
自身的 models、schemas 和 selector 必须使用 canonical import；上述动态调用
点在兼容测试迁移前不得批量替换。提示词生成同理继续保留
`app.services.llm_service._call_llm` patch 路径。

迁移顺序必须是：

1. 调用方改用 canonical import；
2. 加边界检查，禁止新增旧 import；
3. 保留显式 facade 并运行完整回归；
4. 确认运行时、迁移和外部脚本零依赖；
5. 最后删除 facade。

## 已验证迁移波次

| 日期 | 业务域 | 收口内容 | 保留兼容 | 证据 |
|---|---|---|---|---|
| 2026-07-30 | `platform/overview` | `main.py` 直接装配 canonical router；4 个 ORM import 收口；测试移入 `tests/platform/` | `app/routers/overview.py` | 平台/RBAC 定向 12 passed；归一化 OpenAPI hash 未变化；Alembic head `0054_fact_lineage_indexes` |
| 2026-07-30 | `settings/prompts` | 模板从 router 提取到 `templates.py`；router/model/schema 与 seed 改用 canonical import；测试移入 `tests/settings/` | `app/routers/prompts.py`、`app/models/prompt.py`、`app/schemas/prompt.py`、`app.services.llm_service` patch 路径 | 定向 8 passed；8 个 OpenAPI operation/tag 固定；8 份内置模板 SHA-256 固定 |
| 2026-07-30 | `model_configs` | 配置生命周期/默认选择、连通性探测、调用统计查询和安全序列化分别进入 `config_service.py`、`connectivity_service.py`、`usage_query_service.py`、`presentation.py`；router 587→175 行 | 旧 model/schema/router/selector facade 与 11 个 router helper 对象 | 行为 26、边界联合 29 passed；11 个 OpenAPI operation/tag 与全应用指纹稳定 |
| 2026-07-30 | `settings/rules` | 可编辑规则、QwenPaw Agent 配置/连通性、n8n 工作流配置/连通性分别进入 `rules_service.py`、`agent_config_service.py`、`workflow_config_service.py`；9 个 handler 均为单一委派，router 412→184 行 | `app/routers/settings.py` 继续聚合 rules 与 object storage；canonical router 保留旧 model/schema/helper 对象，并在请求时注入 encryption、HTTP、n8n policy 和 settings 等 patch seam | settings/RBAC/生产配置/architecture 联合检查点 232 passed；7 paths/9 operations 与归一化全应用 OpenAPI 指纹稳定 |
| 2026-07-30 | `bootstrap` | 健康探测、启动/关闭顺序和数据库 seed 分别进入 `health.py`、`lifecycle.py`、`seeding.py`；`main.py` 保留 HTTP 装配 | `app.main._seed_db`、`_probe_http_service`、`urllib` 的旧 patch 身份 | 阶段检查点：architecture + infra + health/startup 98 passed；`main.py` 由 937 行降至 372 行 |
| 2026-07-30 | `auth` / `shared` foundation | auth 的模型、schema、service 使用 canonical import；共享依赖、外部探测和 `web_search.py` 成为明确基础能力 | `app/deps.py`、auth model/service 等旧导入与 patch 入口 | 阶段检查点：行为/兼容 61 passed，architecture 8 passed |
| 2026-07-30 | `api_hub` | 接口请求契约和 CRUD/发布校验分别进入 `interface_contracts.py`、`interface_service.py`；HTTP router 负责适配 | router 公开符号、monkeypatch 与 `/api-hub`、`/proxy` 协议 | 阶段检查点：API Hub 39 passed |
| 2026-07-30 | `data_channel/datasets` | 手工数据契约、查询、写入/导入、行编辑和消费者解析进入 `manual_contract.py`、`query_service.py`、`mutation_service.py`、`edit_service.py`、`consumers.py` | router 中公开 contract/helper 身份与运行时 patch 目标 | 阶段检查点：datasets 163 passed；文件/存储/分享/映射相关 79 passed、1 个 MinIO live skipped |
| 2026-07-30 | `data_channel/pipelines` | 请求契约、依赖引用、执行/dry-run、校验/发布门和管理事务进入同域模块；A/B/C 纯转换进入 `route_executor.py`，同步链式 run-record 编排进入 `trigger_service.py`，`engine.py` 仅兼容 facade | router contract/helper 与新旧 engine 函数对象身份 | architecture + pipeline + data-channel 537 passed；全应用 SCC 守卫固定 canonical import 与 facade 身份；OpenAPI 稳定 |
| 2026-07-30 | `data_channel/pipeline_tasks` | DTO、发布契约校验、候选/列表/统计、历史/审计、CRUD/调度刷新与手动触发进入六个同域 service；13 个 handler 仅保留 HTTP 委派 | router 中 DTO、15 个 helper 对象身份与 `_refresh_scheduler`、`_now_utc` 调用时 patch | pipeline/调度联合 223 passed；拆分前后 10 paths/13 operations 指纹一致 |
| 2026-07-30 | `data_channel/curated` | 目录、批准版本读取/导出、审核差异、生命周期和审批事件 Outbox 分层；审核读取与 Mapping 消费之间改为单向端口 | `review_service.py` 的 19 个读取 helper 对象身份和 `version_events.py` 旧常量 | data-channel/ontology/curated 联合 534 passed；事件/增量 67 passed |
| 2026-07-30 | `data_channel/steward` | 39 个 HTTP/SSE handler 分别委派到契约、流式会话、查询、生命周期、浏览器来源和浏览器会话模块；router 分支与 ORM 查询归零 | `run_steward_turn`、请求 DTO 和私有 helper patch/导入入口 | Steward 180、关联 72、architecture 147 passed；33 paths/39 operations 指纹一致 |
| 2026-07-30 | `super_assistant` | 会话/SSE/取消/工具审批、Skill 数据库+文件补偿事务、MCP/平台 MinIO 生命周期分别进入 `conversation_service.py`、`skill_service.py`、`mcp_server_service.py`；24 个端点无 ORM/事务 | router helper 身份、`stream_chat` 与 MinIO collaborator 的调用时 patch | 关联行为 105、architecture 173 passed；16 paths/24 operations 指纹一致；router 636→488 行 |
| 2026-07-30 | `exploration` | session、workspace/attachment、chat/SSE、document、draft 和 apply workflow 进入六个同域 service；26 个 handler 单委派 | 9 个旧 helper/import 与调用时 patch seam | Exploration 100、architecture 180 passed；21 paths/26 operations 指纹一致；router 无 ORM/事务/业务分支 |
| 2026-07-30 | `events` | 列表/上海自然日统计/密钥读取、附件 ZIP 生命周期、第三方批量 ingest 分别进入 `query_service.py`、`attachment_service.py`、`ingest_service.py` | 时间、临时文件与 6 个旧 helper 对象/patch 路径 | 行为 4、新边界 4 passed；全应用 418 paths/550 operations 指纹稳定；router 501→277 行 |
| 2026-07-30 | `model_configs` / LLM | 提供商调用、错误归一化、调用记录与响应处理进入 `llm_gateway.py`，selector/router 不再承载调用实现 | `app.services.llm_service._call_llm` 等既有 patch 扩展点 | 阶段检查点：model configs 26、architecture 13、agent/exploration 19、steward 37 passed |
| 2026-07-30 | `ontologies/sentinels` | 发布门定义校验进入 `validation.py`；项目门禁、查询投影、定义 CRUD 和运行态/CAS 操作分别进入四个 service，11 个 handler 薄委派 | engine/dynamic service 的公开调用契约；router helper 与写围栏 patch | Sentinel 联合 126 passed；8 paths/11 operations 指纹一致 |
| 2026-07-30 | `ontologies/formal_modeling` | schema authoring、实例、Action/HITL、dashboard 和运行时分别进入 service；Action 继续分出校验规则、运行值/契约、执行记录及可独立持久化/通知 effect，公共入口保持兼容 facade | router/action helper 对象身份、历史 monkeypatch 与同事务 effect 顺序 | `tests/ontologies` 298、Action 高风险 92 passed；5,000 组校验差分逐字一致 |
| 2026-07-30 | `ontologies/versions` | release、workspace、trial、运行态冲突/readiness、release gate、查询投影激活、promotion、rollback 分别进入 canonical service；router 仅保留端点和兼容注入 wrapper，4,160→617 行 | 12 个 router helper 签名/调用时 patch 目标与全部版本 API | 版本联合 105 passed；最终定向 32、architecture 115 passed；归一化 OpenAPI hash 稳定 |
| 2026-07-30 | `ontologies/mappings` | MappingService 按身份、关系、对账、候选和投影拆分；router 的查询/实体/关系工作流进入 service；Formal 投影的 12 个纯契约函数单独分层；审批事件经 application command 单向调用 Mapping | MappingService、router helper、Formal projection 旧 facade 的对象身份与 patch seam | mapping + architecture 232 passed；Formal/runtime 42、evolution/trial 64 passed；OpenAPI 稳定 |
| 2026-07-30 | `ontologies/agent_runtime` | profile、图查询、动态 Sentinel、chat、会话、报告、提案执行和错误映射进入八个同域模块；router 只保留 RBAC、HTTP/SSE/HTML 与异常映射 | 31 个 handler 签名/decorator、旧 helper/collaborator 与调用时 patch | 定向 45、ontology 298、architecture 180 passed；26 paths/34 operations 指纹一致；router 828→586 行 |
| 2026-07-30 | `ontologies` runtime boundary | 快照叶子契约进入 `versions/snapshot_contract.py`；Mapping/Action/Sentinel/Release 共享锁进入 `runtime_fence.py`，旧 Mapping lock patch 继续代理 | evolution 与 mapping_service 的历史对象身份 | ontology 298、architecture 144、Mapping CDC/lock 58 passed；全局 SCC 从 23 节点收敛到精确允许的 Sentinel 3 节点环 |

自动边界守卫分层约束当前已验证事实：

- `test_bootstrap_boundaries.py` 固定 `main.py` 与 bootstrap 实现分工、旧 patch
  身份以及启动/关闭顺序；
- `test_canonical_import_boundaries.py` 阻止已迁移包反向导入自己的旧 facade、
  canonical router 跨域互相装配、release 消费者回退到 versions router，并
  固定 `main.py` 的 canonical 装配；
- `test_router_dependency_direction.py` 全局禁止 `main.py` 和兼容
  `app/routers/**` 之外的生产模块导入 HTTP router，同时固定提取后符号的兼容
  对象身份；
- `test_foundation_dependency_direction.py` 固定 auth、data channel 与 shared
  foundation 的依赖方向及薄 facade；
- `test_model_configs_dependency_direction.py` 禁止模型配置域反向依赖本体域，
  并固定 11 个薄 handler、四类同域 service 与旧 helper 身份；
- `test_dataset_router_boundaries.py` 和 `test_formal_router_boundaries.py` 固定
  薄 HTTP adapter、canonical service 与兼容 helper/patch 身份；
- `test_pipeline_router_boundaries.py` 固定已经提取的 validate、publish、run、
  dry-run、管理端点以及兼容 helper/patch 身份；
- `test_pipeline_task_router_boundaries.py` 固定任务 DTO、六类 service、13 个
  薄 handler、调用时 patch 和事务/调度边界；
- `test_events_router_boundaries.py` 固定 Event Registry 查询、附件、ingest
  分层与时间/临时文件兼容 seam；
- `test_settings_rules_router_boundaries.py` 固定规则、Agent 配置和工作流配置的
  三类 service、9 个薄 handler、路由契约、旧 helper 对象及请求时依赖注入；
- `test_exploration_router_boundaries.py`、
  `test_super_assistant_router_boundaries.py` 和
  `test_agent_runtime_router_boundaries.py` 固定三个复杂 API 聚合面的单一委派、
  事务所有权、旧 helper/patch 以及 OpenAPI；
- `test_curated_router_boundaries.py` 与
  `test_mapping_event_dependency_direction.py` 固定 Curated 分层以及审批事件到
  Mapping 的单向依赖；
- `test_mapping_service_boundaries.py`、`test_mapping_router_boundaries.py` 与
  `test_formal_projection_boundaries.py` 固定 Mapping 内部分工和旧 facade；
- `test_sentinel_router_boundaries.py` 固定 Sentinel router/service 委派与写围栏；
- `test_version_workspace_boundaries.py`、`test_version_trial_boundaries.py`、
  `test_version_runtime_state_boundaries.py`、
  `test_version_promotion_boundaries.py` 和
  `test_version_rollback_boundaries.py`、
  `test_version_release_gate_boundaries.py` 固定已提取 service 的端点委派、
  release gate/activation 顺序和兼容注入边界。
- `test_ontology_runtime_import_cycles.py` 扫描包含函数内 import 的完整
  `backend/app` 图，固定唯一 Sentinel 三节点/四边例外，并锁定 Pipeline
  canonical executor/trigger 与两层兼容 facade 身份。

这些守卫只编码已经完成迁移并经兼容测试证明的边界；尚未完成迁移的
model/schema/service facade 不会被笼统判定为可删除。

## 运行期依赖环例外

Import-time 生产模块图保持无环。若把函数内依赖也纳入，当前唯一例外是
`sentinels.cdc`、`sentinels.engine`、`sentinels.dynamic_service` 三节点、四条
精确边：持久 Outbox 消费、调度入队/排空、release overlay reconcile、启用
activation Outbox。架构测试拒绝新增节点或边。这个环跨越同一持久事件事务；
彻底移除需要先引入明确的 Outbox handler 注册与事务所有权迁移，不能仅改成
`importlib` 或字符串导入来规避静态检查。

## 目标内部结构

复杂业务域逐步采用：

```text
feature/
├── api/                 # router、request/response schema
├── application/         # use case、事务和流程编排
├── domain/              # 规则、状态机和端口
└── infrastructure/      # ORM、repository 和外部适配器
```

小模块不强制制造空目录，但 router 不应承载数百行事务和领域规则。

## 当前应用装配

`app/main.py` 仍是 FastAPI 的 HTTP composition root，并保留少量旧测试依赖的
patch 别名；健康检查、生命周期和 seed 的真实实现已经进入：

```text
app/bootstrap/
├── health.py
├── lifecycle.py
└── seeding.py
```

路由表当前仍在 `main.py`，不能把尚不存在的 `application.py` 或 `routing.py`
写成当前事实。若后续继续拆分，必须先固定 OpenAPI/RBAC、router 装配顺序和
monkeypatch 契约。测试应用必须能够关闭 seed、调度器、Sentinel、外部探测和
后台清理任务。
