# 从本体卡片直达本体助手

| 字段 | 内容 |
|---|---|
| 状态 | Validated |
| 日期 | 2026-08-07 |
| 负责人 | Codex（实施） |
| 评审人 | 待维护者指定 |
| Issue/PR | 本地交付；未创建 PR |
| Commit | 本提交 |
| 目标分支 | `nano-ontoprompt` |
| 业务域 | 本体管理、本体助手 |

## 背景

本体管理卡片原有“编辑”和“查看”入口。用户如需基于某个本体发起助手对话，
必须先进入本体助手，再从下拉框中手动选择已发布本体，路径重复且容易选错。

## 目标

- 在每张本体卡片的“查看”右侧增加“对话”按钮；
- 点击后进入本体助手，并自动选中该本体的当前发布版本；
- 将选择写入 HashRouter 深链，刷新、登录 `returnTo` 和用户后续手动切换均保持
  可预测；
- 没有当前发布版本时保留入口位置，但明确禁用并说明原因。

## 非目标

- 不改变本体发布流程、助手授权边界或会话协议；
- 不让草稿工作区替代不可变的当前发布版本；
- 不改变 HTTP API、menu key、数据库、Celery、环境变量或部署配置。

## 当前状态与变更前基线

- 本体管理卡片仅能进入编辑弹窗或本体详情；
- 本体助手只接受页面内手动选择，且以 `current_release_id` 作为可查询范围；
- `/agent` 已是受 RBAC 保护的 HashRouter 路由，但此前没有本体选择深链参数。

## 变更范围

| 模块/路径 | 改动 | API/menu key/数据库/Celery/环境变量影响 |
|---|---|---|
| `frontend/src/pages/ontologies/list/OntologyListPage.tsx` | 卡片新增“对话”按钮并导航到助手深链 | 无 |
| `frontend/src/pages/agent/AgentWorkbenchPage.tsx` | 读取并同步 `ontology_id` 查询参数 | 新增前端可选查询参数；不改 HTTP API |
| `frontend/src/test/e2e/agent_header.spec.ts` | 覆盖卡片跳转和自动选择 | 无 |
| `docs/product/requirements/0003-ontology-agent-deep-link.md` | 固化用户流程、权限和兼容要求 | 无 |
| `docs/iterations/` | 记录范围、兼容与验证证据 | 无 |

## 兼容策略

`/agent` 无查询参数时保持原有“请选择已发布本体”行为。带 `ontology_id` 时仅在该
ID 存在于当前用户可见且拥有 `current_release_id` 的列表中才自动选择；未知、不可见
或尚无当前发布版本的 ID 不会绕过助手既有发布边界。用户手动选择时使用 replace
更新查询参数，避免为每次下拉切换堆积浏览器历史。

## 安全与数据处理

深链只携带本体 ID，不携带令牌、模型配置、业务数据或会话内容。页面仍经过现有
`ProtectedRoute` 与后端授权检查，查询参数不扩大数据访问范围。

## 验收条件

- 已有当前发布版本的本体卡片展示“对话”，点击后 URL 包含正确的编码 ID；
- 本体助手加载完成后，下拉框和工作台标题均指向该本体；
- 没有当前发布版本的卡片按钮不可点击，并提供可理解的说明；
- 直接访问 `/agent`、手动选择本体和现有 `/rag` 重定向保持兼容；
- 前端定向 mocked E2E、静态门禁和仓库文档门禁通过。

## 验证证据

| 层级 | 实际命令/环境 | 退出结果 | CI URL / artifact / 跳过原因 |
|---|---|---|---|
| 单元 | `cd frontend && npm ci && npm run test:unit` | 12 passed | 锁文件安装；本地工作树 |
| 集成/契约 | `cd backend && uv sync --frozen --group dev && uv run pytest -q --disable-warnings --ignore tests/v2/perf`；`cd config && uv sync --frozen --group dev && uv run pytest -q` | 1857 passed、1 skipped；42 passed | 本地工作树；无后端源码或协议变更 |
| 前端静态 | `npm run check:feature-boundaries`；`npm run test:e2e:classification`；`npm run lint`；`npm run build` | 全部通过；49 个 E2E spec 唯一分类 | Vite 保留既有大 chunk 警告，无构建失败 |
| mocked 浏览器 | `PLAYWRIGHT_PORT=52735 npm run test:e2e:mocked` | 82 passed；新增用例覆盖 1280px 无溢出、卡片跳转、自动选择与 URL 同步 | 本地 Chromium；过程文件位于忽略的 `.artifacts/` |
| 文档/仓库卫生 | `node scripts/ci/check-markdown-links.mjs`；`bash scripts/ci/check-repository-hygiene.sh`；`git diff --check` | 95 个 Markdown / 519 条链接无错误或警告；1301 个文件卫生检查通过；diff 通过 | 本地工作树 |
| stack/external E2E | 未执行 | 未执行 | 当前可见运行栈不属于本工作树的隔离 staging，未复用或修改；本变更不调用外部供应商 |
| 部署/回滚 | 未执行 | 未执行 | 本地交付；未部署 |

## 上线步骤、监控指标与观察窗口

随常规前端版本发布。上线后检查本体卡片按钮、`/#/agent?ontology_id=...` 深链、
登录回跳、助手下拉选择和能力接口请求中的本体 ID；观察一个常规发布窗口内的
前端路由错误和助手能力接口 4xx/5xx。

## 回滚触发条件与逐步方案

若按钮指向错误本体、深链导致助手空白或现有手动选择回归，回滚本次前端与文档
提交并重新构建静态资源。该变更没有数据迁移或持久化写入，无需数据库回滚。

## 已知风险与后续动作

- 自定义角色若没有本体助手 menu 权限，仍由现有 `ProtectedRoute` 拒绝访问；
- 真实部署环境中的导航、登录 `returnTo` 和权限矩阵验证需在受控 staging 完成。
