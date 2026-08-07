# 临时隐藏平台概览与超级助手导航入口

| 字段 | 内容 |
|---|---|
| 状态 | Validated |
| 日期 | 2026-08-07 |
| 负责人 | Codex |
| 评审人 | 未单独指定 |
| Issue/PR | 用户直接请求，无独立 Issue/PR |
| Commit | 本记录所在提交 |
| 目标分支 | 当前 worktree（detached HEAD） |
| 业务域 | 平台概览、超级助手、前端导航与 RBAC |

## 背景

平台概览和超级助手将在后续进行集中优化。当前需要先从前端功能
导航中隐藏这两个入口，但不删除页面、路由、权限或后端能力。

## 目标

- admin、editor、viewer 和 custom 用户的桌面及移动导航都不渲染
  “平台概览”与“超级助手”；
- 保留 `overview`、`super_assistant` menu key、默认权限、Hash 路由、
  直达访问、页面实现和默认落地行为；
- 将临时隐藏状态集中在导航配置中，后续可通过移除标记恢复。

## 非目标

- 不禁用或下线两个功能；
- 不更名或删除 menu key、HTTP API、后端守卫和持久化权限；
- 不调整页面内部业务逻辑或视觉设计。

## 当前状态与变更前基线

`PLATFORM_NAV_ITEMS` 直接列出两个入口，`visibleNavigation()` 只按角色和
menu key 过滤，因此拥有权限的用户始终能在侧栏看到它们。

## 变更范围

| 模块/路径 | 改动 | API/menu key/数据库/Celery/环境变量影响 |
|---|---|---|
| `frontend/src/config/navigation.ts` | 增加仅作用于功能导航的隐藏标记并过滤两个入口 | menu key、路由、RBAC 和默认落地不变 |
| `frontend/src/test/e2e/overview.spec.ts` | 断言 custom 与 admin 导航不渲染目标入口 | 继续覆盖 `/overview` 直达和权限边界 |
| `frontend/src/test/e2e/i18n.spec.ts` | 更新真实栈中文界面的导航断言 | 无外部契约变化 |
| 产品契约与迭代文档 | 记录临时隐藏边界、验证与回滚 | 无运行时影响 |

## 兼容策略

`hiddenFromNavigation` 只被 `visibleNavigation()` 消费。`PLATFORM_NAV_ITEMS`
仍保留完整项，`CONFIGURABLE_NAV_ITEMS`、`menuKeyForPath()`、
`canAccessPath()` 和后端权限集合不变。`firstAccessiblePath()` 独立按权限
列表选择落地页，因此旧会话、登录跳转和直达链接不会因导航隐藏失效。

## 安全与数据处理

不读取、修改或回显生产凭据，不变更数据库、用户权限记录或业务数据。

## 验收条件

- 拥有全部菜单权限的 admin 导航也不显示两个入口；
- 只拥有 `overview` 的 custom 用户仍能直达和刷新 `/#/overview`；
- `/super-assistant` 路由、页面导入和 API 代码均保留；
- 权限配置仍包含 `overview` 和 `super_assistant`；
- 前端单测、静态门禁、构建和 mocked E2E 通过。

## 验证证据

| 层级 | 实际命令/环境 | 退出结果 | CI URL / artifact / 跳过原因 |
|---|---|---|---|
| 导航定向 | `npx playwright test src/test/e2e/overview.spec.ts --config=playwright.mocked.config.ts` | 5 passed | 本地 Chromium |
| 前端单元 | `npm run test:unit` | 12 passed | 本地 Node |
| 前端静态 | `npm run check:feature-boundaries`、`npm run test:e2e:classification`、`npm run lint`、`npm run build` | 全部通过 | 49 个 E2E spec 分类唯一；生产 bundle 构建成功 |
| mocked 浏览器 | `npm run test:e2e:mocked` | 81 passed | 本地 Chromium；证据位于被 Git 忽略的 `.artifacts/` |
| 仓库与文档 | `node scripts/ci/check-markdown-links.mjs`、`bash scripts/ci/check-repository-hygiene.sh` | 通过 | 94 个 Markdown / 516 个链接无错误；1300 个文件卫生检查通过 |
| 配置中心 | `cd config && uv sync --frozen --group dev && uv run pytest -q` | 42 passed | 本地锁定依赖 |
| 后端回归 | `cd backend && uv sync --frozen --group dev && uv run pytest -q --disable-warnings --ignore tests/v2/perf` | 1857 passed, 1 skipped | 本地锁定依赖 |
| 生产镜像 | `docker build -f Dockerfile.prod -t ontologybuild-frontend-nav-hide-verify .` | 通过 | Node 构建与 Nginx 镜像成功 |
| 生产静态入口 | 临时容器检查 `/`、`/overview`、bundle asset、`/mcp` | 通过 | 前三者 200；`/mcp` 404；首页 no-cache；asset immutable |
| 真实后端 E2E | 新前端代理到本机既有健康栈，运行 `i18n.spec.ts` | 环境阻塞（2 failed） | 登录前置阶段因既有栈不接受测试默认管理员凭据而超时；未读取生产配置或修改该栈用户 |

## 上线步骤、监控指标与观察窗口

按标准前端镜像流程部署。上线后使用 admin 和一个非管理员角色检查桌面、
折叠和移动导航，再直达两条 Hash 路由。观察一个发布窗口，关注登录后跳转、
403、前端异常和静态资源 404。

## 回滚触发条件与逐步方案

若导航误隐藏其他功能、默认落地失效或直达路由被拒绝，移除两个项上的
`hiddenFromNavigation` 标记并重新构建前端镜像。回滚不涉及数据库或数据迁移。

## 已知风险与后续动作

直达 URL 仍能访问两个功能，这是本次“只隐藏导航”的明确兼容边界。
后续优化完成时应移除标记、恢复导航契约断言，并在可获得隔离栈凭据后重跑
真实后端导航 E2E。
