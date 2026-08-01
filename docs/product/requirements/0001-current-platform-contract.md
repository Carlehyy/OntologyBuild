# 当前平台导航与访问控制契约（可验证现状基线）

| 字段 | 内容 |
|---|---|
| 状态 | Accepted（现状证据基线，不代表新增产品意图） |
| 负责人 | 未在仓库可核验来源中声明 |
| 评审人 | 未在仓库可核验来源中声明 |
| 日期 | 2026-08-02 |
| 关联 Issue | 无可核验记录 |
| 目标版本 | 当前实现；无可核验版本号 |

## 文档性质与证据边界

本文只固化当前源码与自动化测试能够直接证明的导航、前端路由、角色菜单权限
和后端路由守卫，用作后续目录迁移与重构的回归基线。它不解释这些行为背后的
产品动机，也不为源码中不存在的能力补写需求。

证据分为两级：

- **实现证据**：源码当前明确注册或计算了该行为；
- **可执行证据**：现有测试实际请求或操作了该行为，并断言结果。

仅有实现证据的条目不能写成“已有测试覆盖”。源码与测试冲突时，应先记录
冲突并停止把该条目升级为契约；本次核对未发现本文所列集合与断言之间的冲突。

## 证据索引

| 编号 | 来源 | 本文使用的事实 |
|---|---|---|
| E1 | [`frontend/src/config/navigation.ts`](../../../frontend/src/config/navigation.ts) | 导航项、menu key、默认授权集合、路径到 menu key 的映射、首个可访问页面 |
| E2 | [`frontend/src/App.tsx`](../../../frontend/src/App.tsx) | HashRouter 路由、认证/授权包装、重定向与 `returnTo` |
| E3 | [`backend/app/auth/permissions.py`](../../../backend/app/auth/permissions.py) | 后端 menu key、角色默认值、父子归一化与数据库权限读取 |
| E4 | [`backend/app/main.py`](../../../backend/app/main.py) | 后端 router prefix、menu guard、只读跨域授权和 admin guard |
| E5 | [`backend/app/shared/deps.py`](../../../backend/app/shared/deps.py) | JWT 用户依赖、管理员依赖、菜单守卫的读写语义和 403 错误 |
| E6 | [`backend/app/auth/router.py`](../../../backend/app/auth/router.py) | profile 返回数据库解析后的 `menu_permissions` |
| E7 | [`backend/app/settings/users/router.py`](../../../backend/app/settings/users/router.py) 与 [`schemas.py`](../../../backend/app/settings/users/schemas.py) | 单角色类型、非管理员角色权限配置 API、管理员保护 |
| E8 | [`backend/tests/auth/test_user_rbac.py`](../../../backend/tests/auth/test_user_rbac.py) | RBAC、父子归一化、跨域只读和管理员保护的可执行断言 |
| E9 | [`frontend/src/test/e2e/file_asset_links.spec.ts`](../../../frontend/src/test/e2e/file_asset_links.spec.ts) | 登录下载深链保留 `returnTo` 并在登录后返回原地址 |
| E10 | [`backend/tests/architecture/test_retired_legacy_extraction_contract.py`](../../../backend/tests/architecture/test_retired_legacy_extraction_contract.py) | 22 个遗留 operation/raw `/mcp` 的退役，以及独立 MCP 能力保留 |
| E11 | [ADR-0003](../../architecture/adr/0003-retire-legacy-document-ontology-extraction.md) | 文档本体抽取退役范围、兼容与数据回滚决策 |

## 当前角色模型

1. 用户只有一个 `role` 字段；创建和更新 API 接受
   `admin`、`editor`、`viewer`、`custom` 四个值。传入角色数组会被请求校验拒绝。
   证据：E7；E8 的
   `test_custom_role_has_one_assignment_and_configurable_menu_scope`。
2. 可由管理员配置菜单范围的角色只有 `editor`、`viewer`、`custom`。
   `admin` 不写入这组可配置记录；后端菜单判断对 admin 直接放行。证据：E3、E7。
3. profile 每次通过 `get_role_menu_keys()` 解析当前角色权限并返回
   `menu_permissions`，不是仅依赖登录时 JWT 中的角色值。证据：E3、E6。
4. 角色权限列表和修改接口只有管理员可调用。普通 editor 对这两个接口均得到
   403。证据：E7；E8 的
   `test_regular_user_cannot_read_or_change_role_permissions`。
5. 当前用户管理接口禁止最后一个有效管理员移除自己的管理员身份或删除自己。
   证据：E7；E8 的 `test_last_admin_cannot_remove_own_access`。

## menu key 基线

### 可配置业务菜单

静态逐项对照 E1 的非 admin-only 导航与 E3 的 `ALL_MENU_KEYS`，两侧均为以下
18 个 key，顺序也一致：

| 功能 | menu key | 前端入口 | editor/viewer 无数据库记录时 | custom 无数据库记录时 |
|---|---|---|---|---|
| 平台概览 | `overview` | `/overview` | 有 | 有 |
| 超级助手 | `super_assistant` | `/super-assistant` | 有 | 无 |
| 业务探索 | `explore` | `/explore` | 有 | 无 |
| 本体管理 | `ontologies` | `/ontologies` | 有 | 无 |
| 本体助手 | `agent` | `/agent` | 有 | 无 |
| 事件登记 | `events` | `/events` | 有 | 无 |
| 数据通道 | `data` | `/data` | 有 | 无 |
| 数据流水线 | `data.pipelines` | `/data/pipelines` | 有 | 无 |
| 数据任务池 | `data.sync_tasks` | `/data/pipelines/sync-tasks` | 有 | 无 |
| 数据资产湖 | `data.structured` | `/data/structured` | 有 | 无 |
| 接口代理 | `api_hub` | `/api-hub` | 无 | 无 |
| 接口管理 | `api_hub.interfaces` | `/api-hub/interfaces` | 无 | 无 |
| 调用历史 | `api_hub.history` | `/api-hub/history` | 无 | 无 |
| 授权配置 | `api_hub.authorization` | `/api-hub/authorization` | 无 | 无 |
| 开放社区 | `community` | `/community` | 有 | 无 |
| 技能社区 | `community.skills` | `/community/skills` | 有 | 无 |
| 插件社区 | `community.plugins` | `/community/plugins` | 有 | 无 |
| 模型配置 | `models` | `/models` | 有 | 无 |

因此，数据库中没有该角色记录时：

- editor/viewer 使用 14 个默认 key，即上表除 API Hub 父项和三个子项之外的
  全部 key；
- custom 只得到 `overview`；
- admin 的后端菜单判断覆盖全部 18 个业务 key。

证据：E1、E3；custom 默认值另由 E8 的
`test_custom_role_has_one_assignment_and_configurable_menu_scope` 实际验证。
当前测试没有单独登录 viewer 验证其默认集合，因此 viewer 默认值属于实现证据，
不是独立的可执行验收证据。

### 管理员专属系统设置

E1 另定义 6 个 admin-only key，它们不属于上述可配置集合：

`system_settings`、`settings.users`、`settings.agents`、
`settings.workflows`、`settings.minio`、`settings.domains`。

前端对所有 `/settings` 深链统一按 `system_settings` 判断，并对非 admin
直接拒绝。后端 `/api/v1/settings` router 使用 `require_admin`；用户和角色权限
管理 API 也在各端点使用 `require_admin`。editor 即使直接请求
`/api/v1/settings/agent-config` 仍得到 403。证据：E1、E4、E5、E7；E8 的
`test_role_menu_permissions_protect_pages_and_apis`。

规则设置、提示词模板和系统设置“开放接口”已按 ADR-0003 退役。这里的开放
接口专指旧 `/api/v1/mcp` 与 raw `/mcp`；API Hub、MinIO MCP、Plugin 社区和
超级助手 MCP 不属于该模块，继续使用各自独立权限与协议。
生产入口同样必须让 `/mcp` 明确返回 404，并把保留的 `/mcp/minio` 流式代理
到 backend，避免任一路径被前端 SPA fallback 掩盖。
证据：E10、E11。

### 父子 key 归一化

后端保存角色权限前执行以下确定性归一化：

1. 丢弃不在 `ALL_MENU_KEYS` 中的值并去重；
2. 授予子项时自动补父项；
3. 只有父项、没有任何子项时移除该父项；
4. 最终按 `ALL_MENU_KEYS` 的固定顺序返回。

父子组只有 `data`、`api_hub`、`community`。现有测试已验证：

- `community.plugins` 保存为 `community` + `community.plugins`；
- `data.structured` 保存为 `data` + `data.structured`。

证据：E3；E8 的
`test_plugin_community_has_an_independent_mcp_permission_boundary` 和
`test_authorized_pages_can_read_cross_module_reference_data`。

## 前端路由与导航行为

1. 应用使用 `HashRouter`。除下列公开或仅认证路由外，业务页面通过
   `ProtectedRoute` 同时检查登录态和 `canAccessPath()`：
   - `/login`：公开；
   - `/share/manual/:token`：公开；
   - `/file-assets/:assetId/download`：只要求登录，不检查 menu key。
   证据：E2。
2. 未登录访问受保护页面时，目标路径和 query 被编码进
   `/login?returnTo=...`，同时写入 navigation state。下载深链的登录、返回原地址
   和 Bearer 下载已由 E9 实际验证。
3. 已登录但无对应 menu key 时，页面渲染 `AccessDeniedPage`；返回目标优先使用
   最近一次仍有权访问的路径，否则使用当前首个可见导航入口。没有任何可见入口
   时首个可访问路径为 `/no-access`。证据：E1、E2。
4. `/` 和未知路径对已登录用户重定向到首个可访问入口，对未登录用户重定向到
   `/login`。证据：E2。
5. 当前兼容重定向为：
   - `/data` → `/data/pipelines`；
   - `/api-hub` → `/api-hub/interfaces`；
   - `/community` → `/community/skills`；
   - `/settings`、`/settings/skills` → `/settings/users`；
   - `/settings/extraction`、`/settings/rules`、`/settings/prompts`、
     `/settings/open-interfaces` → `/settings/users`；
   - `/pipelines` 及其深链 → `/data/pipelines`；
   - `/rag` → `/agent`。
   证据：E2。
6. `menuKeyForPath()` 未识别的路径返回 `null`，`canAccessPath()` 对这种路径
   不施加菜单限制。因此 `/inbox` 当前是“已登录即可访问”，不是 18 个业务
   menu key 之一。证据：E1、E2。本文只记录现状，不推断这是否为长期产品设计。
7. 前端兼容旧持久化会话：当 `menu_permissions` 字段缺失时，editor/viewer
   使用普通角色默认集合，custom 使用 `overview`；显式空数组表示没有获授页面，
   不触发 fallback。证据：E1。

除 E9 的下载深链外，仓库当前没有直接针对 `navigation.ts` 全量 menu/路径矩阵
的前端自动化测试；以上其余前端行为均标记为实现证据。

## 后端菜单守卫行为

`require_menu_permission()` 的当前行为如下：

- admin 对任意业务 menu key 通过；
- 非 admin 从数据库角色权限记录或角色默认集合判断；
- `GET`、`HEAD`、`OPTIONS` 可额外接受路由声明的 `read_menu_keys`；
- 其他方法只接受 API 所属 menu key；
- 拒绝时返回 403，detail 中包含
  `code="MENU_ACCESS_DENIED"` 和所属 `menu_key`。

证据：E3、E5。

E4 当前明确注册的主要边界为：

| 后端入口 | 所属 guard | 声明的只读共享 key |
|---|---|---|
| `/api/v1/overview` | `overview` | 无 |
| 本体 v1/v2/formal 管理入口 | `ontologies` | `agent`、`explore`、`events` |
| `/api/v1/models` | `models` | `super_assistant`、`explore`、`ontologies`、`agent`、`data.pipelines` |
| `/api/v2/pipelines` | `data.pipelines` | `data.structured`、`data.sync_tasks` |
| `/api/v2/exploration` | `explore` | 无 |
| 超级助手 `/api/v2/super-assistant` | `super_assistant` | 无 |
| 社区 MCP `/api/v2/community` | `community.plugins` | 无 |
| `/api/v2/events` | `events` | 无 |
| API Hub 管理入口 | 对应 `api_hub.interfaces`、`api_hub.history` 或 `api_hub.authorization` | 无 |
| `/api/v1/settings` | admin | 不适用 |

这张表只描述 E4 中显式挂载的 router 依赖，不主张“一个页面只调用一组 API”，
也不把无一一对应证据的前端子页面强行映射到某个后端 router。

现有 E8 已执行断言以下边界：

- editor 只获授 `models` 后，overview stats 返回 403，models GET 返回 200，
  settings GET 返回 403；
- custom 默认 profile 返回 `overview`；改授 `models` 后 overview 返回 403，
  models GET 返回 200；
- 只获授 `agent` 时可 GET 本体和模型列表，但不能删除模型；
- 只获授 `data.structured` 时，直接调用 `data.pipelines` guard 的 GET 分支通过，
  POST 分支被拒绝；
- `community.plugins` 不授予超级助手 MCP 权限；社区 MCP 仅暴露该用户可管理的
  社区记录，测试中的内置 MCP 记录不可通过社区端点修改。

## 可核验验收条件

后续目录迁移或重构涉及本文范围时，至少保持：

1. E1 的 18 个可配置业务 key 与 E3 的 `ALL_MENU_KEYS` 集合及顺序一致；
2. editor/viewer 默认集合继续排除全部 API Hub key，custom 默认集合仍为
   `overview`，除非另有独立需求和迁移方案；
3. admin-only 设置不能通过伪造非管理员 menu key 绕过；
4. 子项保存后的父项补全、孤立父项移除和固定排序保持不变；
5. 后端只读共享授权不能扩大到写请求；
6. 未登录深链保留 `returnTo`，受保护页面仍进行前后端双层授权；
7. E8 的全部 RBAC 测试和 E9 所属 mocked E2E 分类通过。

本文没有证明所有角色 × 所有导航 × 所有直达 URL 的完整端到端矩阵已覆盖。
若未来把这份基线作为发布门禁，应补一组从 E1 生成或逐项断言的前端导航契约
测试；在该测试落地前，不得把这项覆盖写成“已完成”。

## 上线、兼容与迁移约束

- menu key 是前端、后端和数据库角色权限记录共同使用的持久化契约；重命名或
  拆分必须附数据迁移和兼容读取，不能作为纯目录调整处理。
- Hash 深链、`returnTo`、上述旧路径重定向和其余后端 router prefix 是当前
  入口契约；移动模块时应保持外部路径不变。
- 本文随 ADR-0003 更新系统设置当前集合；退役 API 不再是应保持的兼容入口。

## 关联 ADR 与迭代记录

- 目录治理决策：[`docs/architecture/adr/0001-business-domain-structure.md`](../../architecture/adr/0001-business-domain-structure.md)
- 遗留抽取退役决策：[`docs/architecture/adr/0003-retire-legacy-document-ontology-extraction.md`](../../architecture/adr/0003-retire-legacy-document-ontology-extraction.md)
- 当前目录整理迭代：
  [`docs/iterations/2026/2026-07-30-repository-governance.md`](../../iterations/2026/2026-07-30-repository-governance.md)
