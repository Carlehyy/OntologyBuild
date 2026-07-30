# 统一模块地图

这张表用于从用户可见功能定位当前源码、主要 API 与测试。它描述的是当前
事实，不代表所有目录迁移已经完成。

| 能力 | menu key / 路由 | 前端当前入口 | 后端优先实现位置 | 主要 API | 测试入口 |
|---|---|---|---|---|---|
| 平台概览 | `overview` / `/overview` | [`features/overview/`](../../frontend/src/features/overview/)（[`pages/overview/OverviewPage.tsx`](../../frontend/src/pages/overview/OverviewPage.tsx) 仅兼容） | [`app/platform/`](../../backend/app/platform/) | `/api/v1/overview/stats` | [`overview.spec.ts`](../../frontend/src/test/e2e/overview.spec.ts)、[`tests/platform/`](../../backend/tests/platform/) |
| 超级助手 | `super_assistant` / `/super-assistant` | [`pages/super-assistant/`](../../frontend/src/pages/super-assistant/) | [`app/super_assistant/`](../../backend/app/super_assistant/) | `/api/v2/super-assistant` | [`tests/super_assistant/`](../../backend/tests/super_assistant/)、[`super_assistant_*.spec.ts`](../../frontend/src/test/e2e/) |
| 业务探索 | `explore` / `/explore` | [`pages/explore/`](../../frontend/src/pages/explore/) | [`app/exploration/`](../../backend/app/exploration/) | `/api/v2/exploration` | [`tests/exploration/`](../../backend/tests/exploration/)、[`explore_*.spec.ts`](../../frontend/src/test/e2e/) |
| 本体管理 | `ontologies` / `/ontologies` | [`pages/ontologies/`](../../frontend/src/pages/ontologies/)、[`palantir-graph/`](../../frontend/src/palantir-graph/) | [`app/ontologies/`](../../backend/app/ontologies/) | `/api/v1/ontologies`、`/api/v2/ontologies`、`/api/v2/formal/ontologies` | [`tests/ontologies/`](../../backend/tests/ontologies/)、[`tests/v2/mapping/`](../../backend/tests/v2/mapping/)、[`tests/v2/graph/`](../../backend/tests/v2/graph/)、[本体 Playwright](../../frontend/src/test/e2e/) |
| 本体助手 | `agent` / `/agent` | [`pages/agent/`](../../frontend/src/pages/agent/) | [`agent_runtime/`](../../backend/app/ontologies/agent_runtime/)、[`decision_simulation/`](../../backend/app/ontologies/decision_simulation/) | `/api/v2/formal/ontologies` | [`tests/ontologies/test_agent_runtime.py`](../../backend/tests/ontologies/test_agent_runtime.py)、[`agent_*.spec.ts`](../../frontend/src/test/e2e/) |
| 事件登记 | `events` / `/events` | [`pages/events/`](../../frontend/src/pages/events/) | [`app/events/`](../../backend/app/events/) | `/api/v2/events`、`/api/v2/ingest` | [`test_events.py`](../../backend/tests/events/test_events.py)、[`event_registry_edit.spec.ts`](../../frontend/src/test/e2e/event_registry_edit.spec.ts) |
| 数据通道 | `data.*` / `/data/...` | [`pages/pipelines/`](../../frontend/src/pages/pipelines/)、[`pages/data-management/`](../../frontend/src/pages/data-management/) | [`app/data_channel/`](../../backend/app/data_channel/) | `/api/v2/connections`、`/api/v2/datasets`、`/api/v2/manual-dataset-sharing`、`/api/v2/pipelines`、`/api/v2/file-transfer`、`/api/v2/file-assets`、`/api/v2/curated`、`/api/v2/incremental`、`/api/v2/sync-tasks`、`/api/v2/pipeline-tasks`、`/api/v2/steward`；兼容采集例外 `/api/v2/collectors`；另有 `/api/public/manual-datasets`、`/api/public/file-assets` | [`tests/data_channel/`](../../backend/tests/data_channel/)、[`tests/v2/connection/`](../../backend/tests/v2/connection/)、[`datasets/`](../../backend/tests/v2/datasets/)、[`pipeline/`](../../backend/tests/v2/pipeline/)、[数据通道 Playwright](../../frontend/src/test/e2e/) |
| 接口代理 | `api_hub.*` / `/api-hub/...` | [`pages/api-hub/`](../../frontend/src/pages/api-hub/) | [`app/api_hub/`](../../backend/app/api_hub/) | `/api/api-hub`、`/api-hub`、`/proxy` | [`tests/api_hub/`](../../backend/tests/api_hub/)、[`api_hub_*.spec.ts`](../../frontend/src/test/e2e/) |
| Skill 社区 | `community.skills` / `/community/skills` | [`SkillCommunityPage.tsx`](../../frontend/src/pages/community/SkillCommunityPage.tsx) | 当前无社区 Skill 后端；超级助手 Skill 属于另一能力 | 无 | [`community.spec.ts`](../../frontend/src/test/e2e/community.spec.ts) 固定维护中占位 |
| Plugin 社区 | `community.plugins` / `/community/plugins` | [`PluginCommunityPage.tsx`](../../frontend/src/pages/community/PluginCommunityPage.tsx) | [`app/community/`](../../backend/app/community/) 适配到 [`mcp_server_service.py`](../../backend/app/super_assistant/mcp_server_service.py) | `/api/v2/community` | [`community.spec.ts`](../../frontend/src/test/e2e/community.spec.ts)、[`tests/auth/test_user_rbac.py`](../../backend/tests/auth/test_user_rbac.py) |
| 模型配置 | `models` / `/models` | [`pages/models/`](../../frontend/src/pages/models/) | [`app/model_configs/`](../../backend/app/model_configs/) | `/api/v1/models` | [`model_configs/test_router.py`](../../backend/tests/model_configs/test_router.py)、[`models.spec.ts`](../../frontend/src/test/e2e/models.spec.ts) |
| 系统设置 | `system_settings` / `/settings/...` | [`pages/settings/`](../../frontend/src/pages/settings/) | [`app/settings/`](../../backend/app/settings/)；规则、Agent 配置和工作流配置由 [`settings/rules/`](../../backend/app/settings/rules/) 三类 service 承接，旧聚合入口仍保留 | `/api/v1/settings`、`/api/v1/users`、`/api/v1/prompts`、`/api/v1/domains`、`/api/v1/mcp` | [`tests/settings/`](../../backend/tests/settings/)、[`test_settings_rules_router_boundaries.py`](../../backend/tests/architecture/test_settings_rules_router_boundaries.py)、[`tests/auth/`](../../backend/tests/auth/)、[`settings.spec.ts`](../../frontend/src/test/e2e/settings.spec.ts) |
| 支撑能力 | 登录、收件箱、分享与下载 | [支撑页面](../../frontend/src/pages/)与组件 | [`app/auth/`](../../backend/app/auth/)、[`app/inbox/`](../../backend/app/inbox/) 及相关领域 | `/api/v1/auth`、`/api/v2/inbox`、`/api/public/...` | [`tests/auth/`](../../backend/tests/auth/)、[`tests/inbox/`](../../backend/tests/inbox/)、[分享/下载 Playwright](../../frontend/src/test/e2e/) |

表中的 Playwright 文件都位于 `frontend/src/test/e2e/`；后端测试以
`backend/tests/` 为根。带通配符的链接表示该目录内的测试族。

`/api/v2/collectors` 当前只有 AI-HOT 采集器：它在 ontology/release lock 内
直接写 Formal Object、Link 与 Fact，没有经过 DatasetVersion、Pipeline、
Curated review 或 Mapping；前端只有 API client 定义，没有从当前可达 UI
调用。它是明确登记的兼容例外，不代表标准数据生产链可以跳过审核。

系统设置的 HTTP 装配仍经
[`app/routers/settings.py`](../../backend/app/routers/settings.py) 聚合
rules 与 object storage，再挂载到 `/api/v1/settings`。其中 rules router 只做
鉴权依赖、请求/响应适配和调用时兼容注入；规则持久化、QwenPaw Agent
连通性及 n8n 工作流策略分别由同域三个 service 所有。不要因为 handler 已经
变薄就提前删除旧聚合入口或 helper/monkeypatch 路径。

## 前端复杂页面的当前内部分工

以下拆分仍位于既有 `pages/<domain>/` 内，是“页面编排 + 同域复杂展示组件”
边界，不代表这些业务域已经迁入 `features/`：

| 页面入口 | 页面保留责任 | 已提取的同域组件 |
|---|---|---|
| [`DataStewardPage.tsx`](../../frontend/src/pages/pipelines/steward/DataStewardPage.tsx) | 页面状态、SSE/上传/轮询请求编排和顶层布局 | [`ConversationTimeline.tsx`](../../frontend/src/pages/pipelines/steward/components/ConversationTimeline.tsx) 负责消息/工具轨迹/上传时间线；[`StewardComposer.tsx`](../../frontend/src/pages/pipelines/steward/components/StewardComposer.tsx) 负责目标选择、输入和历史跳转；[`WorkspaceModal.tsx`](../../frontend/src/pages/pipelines/steward/components/WorkspaceModal.tsx) 负责 workspace 创建/编辑；[`BrowserCollaboration.tsx`](../../frontend/src/pages/pipelines/steward/components/BrowserCollaboration.tsx) 负责浏览器接管与协作展示；[`ManagedPipelinesPanel.tsx`](../../frontend/src/pages/pipelines/steward/components/ManagedPipelinesPanel.tsx) 负责受管流水线面板 |
| [`SuperAssistantPage.tsx`](../../frontend/src/pages/super-assistant/SuperAssistantPage.tsx) | 页面状态、请求编排和顶层布局 | [`AssistantConversation.tsx`](../../frontend/src/pages/super-assistant/components/AssistantConversation.tsx) 负责消息、Markdown、工具轨迹、上下文用量和确认卡；[`AssistantConfiguration.tsx`](../../frontend/src/pages/super-assistant/components/AssistantConfiguration.tsx) 负责 Skill/MCP 配置与创建/编辑弹窗 |
| [`AgentWorkbenchPage.tsx`](../../frontend/src/pages/agent/AgentWorkbenchPage.tsx) | React Query/API/流式状态编排和顶层 workbench | [`AgentWorkbenchPresentation.tsx`](../../frontend/src/pages/agent/components/AgentWorkbenchPresentation.tsx) 负责消息、步骤/溯源/调用链、导出和分栏交互；[`OntologyNetworkView.tsx`](../../frontend/src/pages/agent/components/OntologyNetworkView.tsx) 负责类型网络、viewport、对象卡片、实例表和导出 |
| [`SettingsPage.tsx`](../../frontend/src/pages/settings/SettingsPage.tsx) | URL/tab 解析、固定顺序调用五个能力 hook、选择当前视图；复用现有用户和 MinIO 面板 | [`hooks/`](../../frontend/src/pages/settings/hooks/) 分别承载规则、提示词、Agent、工作流和领域的状态/API/副作用；[`tabs/`](../../frontend/src/pages/settings/tabs/) 是接收显式 view-model props 的展示层 |
| [`Panel.tsx`](../../frontend/src/palantir-graph/components/Panel.tsx) | 按选中定义类型分发编辑器，并保留只读入口 | [`editors/`](../../frontend/src/palantir-graph/components/editors/) 分别承载 Object、Link、Action、Function 编辑器；`DefinitionPanelShell.tsx` 只共享面板壳，不隐藏领域状态 |

Agent 的 `ProposalCard`、`SentinelProposalCard`、`BoundaryDrawer`、
`DynamicSentinelDrawer`、`AgentChart` 及惰性加载的
`InstanceKnowledgeGraph`、`DecisionSimulationView` 仍是
`pages/agent/` 下的同域实现；不要误写成已全部迁入 `components/`。当前
`check:feature-boundaries` 会递归检查这些页面目录，阻止兄弟 page domain
之间直接 import。Settings 的依赖方向是
`SettingsPage → hooks/tabs`，tabs 只 type-import 对应 hook，hooks 不依赖 tabs。
图谱入口保持 `Panel → editors` 单向依赖；编辑器直接使用既有 Store/API，不
通过 barrel 隐藏依赖。

## 当前应用入口

- FastAPI 装配和生命周期：[`backend/app/main.py`](../../backend/app/main.py)；
- Celery 注册入口：[`backend/app/tasks/celery_app.py`](../../backend/app/tasks/celery_app.py)；
- React 路由：[`frontend/src/App.tsx`](../../frontend/src/App.tsx)；
- 导航与前端 menu key：[`frontend/src/config/navigation.ts`](../../frontend/src/config/navigation.ts)；
- 服务端 menu key / RBAC：[`backend/app/auth/permissions.py`](../../backend/app/auth/permissions.py)；
- 推荐本地核心完整栈：[`docker-compose.local.yml`](../../docker-compose.local.yml)；
- 生产编排：[`docker-compose.prod.yml`](../../docker-compose.prod.yml)。

## 依赖规则

1. 业务域可以依赖 `app/shared` 的稳定能力，不应直接依赖另一个业务域的
   router；
2. 跨域流程通过明确的 application service、事件或公开契约组合；
3. `app/routers|models|schemas|services` 目前以兼容层为主，但例外台账中的
   实现仍是当前事实，迁移前不得复制；
4. 平台概览已按 ADR-0002 迁入 `src/features/overview/`；其他前端业务域只有
   在对应迁移 ADR 获批并建立骨架后才能切换 canonical 路径；
5. API 路径、menu key、数据库 revision 与 Celery task name 是兼容契约，
   不能随目录整理顺手改名。

## 三处路由与授权事实

导航、React 路由和服务端授权目前是三处事实源。修改任一处时，必须同时核对
另外两处并验证 admin、editor、viewer、custom 的可见菜单与直接 URL 访问。
未来可通过 route manifest 减少重复，但在该迁移完成前不能假设自动同步。

兼容层详情见 [后端模块边界](./backend-modules.md)，浏览器协议见
[前端路由与权限](./frontend-routing.md)，跨业务域运行顺序见
[核心数据流](./data-flow.md)。
