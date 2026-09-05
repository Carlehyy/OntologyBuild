# OpenOntology 前端

前端使用 React、TypeScript、Vite 和 Tailwind CSS。当前导航事实源是
`src/config/navigation.ts`，路由装配位于 `src/App.tsx`。
Tailwind 只有一个配置事实源：`tailwind.config.ts`；不要新增同名 JS 配置，
否则 PostCSS 自动发现会产生加载顺序歧义。

## 当前结构

```text
src/
├── features/
│   └── overview/      已迁移的平台概览 canonical 实现
├── pages/             未迁移业务入口及迁移期兼容 facade
├── palantir-graph/    本体图谱编辑与运行视图
├── api/               HTTP 客户端与 API 封装
├── components/        UI 与跨页面组件
├── config/            导航等应用配置
├── stores/            客户端状态
└── test/
    ├── unit/           Node 22 纯逻辑单元测试
    └── e2e/            Playwright 浏览器测试
```

平台概览已迁入 `src/features/overview/`。`src/app/`、其余 `features/` 和
`src/shared/` 仍是逐业务域迁移的目标结构；业务域迁移方案未经维护者批准并
建立目标骨架时，继续维护当前权威路径，不要创建第三套并行实现。

全局悬浮 AI 助手位于 `components/assistant-widget/`（挂载于 `Layout`，
状态在 `stores/assistantWidgetStore.ts`）。其面板为懒加载分包，是仓库目前
唯一使用 antd / Ant Design X 的组件岛；其余 UI 仍是 Tailwind + Radix +
CSS 变量 token 体系，新增通用组件不要默认引入 antd。

复杂页面当前在原业务目录内按“页面编排 + 同域组件”拆分：

- 数据管家：`pages/pipelines/steward/DataStewardPage.tsx` 保留状态、请求和
  顶层布局，`components/` 分别承载会话时间线、目标/composer、workspace
  弹窗、浏览器协作和受管流水线；
- 超级助手：`pages/super-assistant/SuperAssistantPage.tsx` 保留编排，
  `components/` 分别承载会话展示与 Skill/MCP 配置；
- 本体助手：`pages/agent/AgentWorkbenchPage.tsx` 保留 API/流式编排，
  `components/` 承载 workbench 展示和本体网络；其他 Agent 卡片、drawer、
  图表与惰性视图仍在同一 `pages/agent/` 业务目录。
- 系统设置：`pages/settings/SettingsPage.tsx` 只解析 URL/tab、调用五个能力
  hook 并选择视图；`hooks/` 承载状态/API/副作用，`tabs/` 只接收显式
  view-model props。

## 开发与静态门禁

```bash
npm ci
npm run dev

npm run test:unit
npm run check:feature-boundaries
npm run test:e2e:classification
npm run lint
npm run build
```

## 浏览器测试分组

```bash
npm run test:e2e:mocked    # 后端被故意指向不可达端口，可用于 PR
npm run test:e2e:stack     # 需要隔离的 OpenOntology 后端
npm run test:e2e:external  # 需要显式开关及真实外部服务
```

每个 `*.spec.ts` 必须且只能属于一个显式分组。新增或移动用例后，
`test:e2e:classification` 会阻止遗漏或重复分类。测试截图、trace、video 和
HTML 报告写入仓库根目录 `.artifacts/playwright/`，不得提交。

`check:feature-boundaries` 同时验证 page domain 之间没有直接依赖、生产模块
没有循环依赖，并且除 ADR-0002 登记的 Overview 兼容入口外，全部生产
TypeScript/TSX 都能从 `src/main.tsx` 到达。

真实验收条件见
[测试指南](../docs/development/testing.md)。
