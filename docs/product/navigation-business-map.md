# 导航、业务能力与源码映射

导航配置以 `frontend/src/config/navigation.ts` 为当前界面事实源。目录迁移不得
改变已经持久化的 menu key。

| 导航 | 前端当前入口 | 后端 canonical package |
|---|---|---|
| 平台概览 | `features/overview/`（`pages/overview/OverviewPage.tsx` 仅兼容 facade） | `app/platform/` |
| 超级助手 | `pages/super-assistant/` | `app/super_assistant/` |
| 业务探索 | `pages/explore/` | `app/exploration/` |
| 本体管理 | `pages/ontologies/`、`palantir-graph/` | `app/ontologies/` |
| 本体助手 | `pages/agent/` | `app/ontologies/agent_runtime/`、`app/ontologies/decision_simulation/` |
| 事件登记 | `pages/events/` | `app/events/` |
| 数据流水线/任务池/资产湖 | `pages/pipelines/`、`pages/data-management/` | `app/data_channel/` |
| 接口代理 | `pages/api-hub/` | `app/api_hub/` |
| Skill 社区 | `pages/community/SkillCommunityPage.tsx` | 当前为维护中占位页，未接后端 Skill API |
| Plugin 社区 | `pages/community/PluginCommunityPage.tsx` | `app/community/` 是适配入口；MCP 能力由 `app/super_assistant/mcp_server_service.py` 实现 |
| 模型配置 | `pages/models/` | `app/model_configs/` |
| 系统设置 | `pages/settings/` | `app/settings/` |
| 登录、收件箱与分享 | 支撑页面 | `app/auth/`、`app/inbox/` 及相关领域 |

平台概览已经按 ADR-0002 迁入 `src/features/overview/`。其他前端能力仍以表中
`pages/` 等路径为权威；只有单个业务域迁移 ADR 获批并建立目标骨架后，才切换
该域的新代码位置。后端以稳定业务能力为边界，不按每个页面或 Tab 复制模型、
service 或 router。

“开放社区”父导航下的两个页面并非同一完成度：Plugin 社区已经通过
`/api/v2/community` 管理当前用户的 MCP server；Skill 社区当前只展示维护中
占位状态。超级助手自己的 Skill 管理 API 位于 `/api/v2/super-assistant`，不能
据此把社区 Skill 页面描述成已接通。

包含路由、API 和测试入口的完整定位表见
[统一模块地图](../architecture/module-map.md)。本表回答“入口属于哪个业务域”；
数据生产、定义发布和刷新顺序见[核心数据流](../architecture/data-flow.md)。
