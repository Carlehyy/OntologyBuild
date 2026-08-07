# 前端源码

当前源码正按业务域逐步迁移；平台概览已建立首个 canonical feature，其余
业务仍以页面和技术类型为主要边界：

```text
src/
├── main.tsx           React 挂载入口
├── App.tsx            路由、授权 guard 与跨域页面组合
├── features/
│   └── overview/      平台概览 canonical 实现
├── pages/             未迁移页面及迁移期兼容入口（见 pages/README.md）
├── palantir-graph/    本体图谱编辑与运行视图（见 palantir-graph/README.md）
├── api/               API 客户端
├── assets/            由源码显式引用的静态资源
├── components/        UI 与跨页面组件
├── config/            导航等应用配置
├── stores/            客户端状态
├── styles/            全局和共享样式
├── i18n/              中英文资源
├── lib|utils|types/   共享基础能力
└── test/
    ├── unit/          Node 22 TypeScript 纯逻辑单元测试
    └── e2e/           Playwright 浏览器测试
```

路由事实源是 `App.tsx`，导航与 menu key 事实源是
`config/navigation.ts`。平台概览遵循已批准的迁移边界，应用从
`@/features/overview` 导入，`pages/overview/OverviewPage.tsx` 只保留
兼容 re-export。其他业务域只有在迁移方案获批并建立目标骨架后才能进入
`features/`；此前继续维护当前权威路径，不创建并行实现。

`features/overview` 不得依赖 `pages/` 或其他 feature。运行
`npm run check:feature-boundaries` 可验证当前边界、生产模块可达性和循环
依赖。迁移前的 `pages/<domain>/` 也不得导入兄弟 page domain；跨域页面组合
只能留在 `App.tsx`，共享实现应进入共享基础目录。

当前复杂页面和图谱编辑器采用同域内拆分，避免继续把状态编排、定义编辑与复杂
展示堆在单文件：

```text
pages/pipelines/steward/
├── DataStewardPage.tsx          状态、请求编排、主布局
└── components/                 会话、composer、workspace、浏览器协作、受管流水线
pages/super-assistant/
├── SuperAssistantPage.tsx      状态、请求编排、顶层布局
└── components/                 会话展示、Skill/MCP 配置
pages/agent/
├── AgentWorkbenchPage.tsx      API/流式状态编排、顶层 workbench
└── components/                 workbench 展示、本体网络
pages/settings/
├── SettingsPage.tsx            URL/tab 解析、能力 hook 调用、视图选择
├── hooks/                      Agent、工作流与领域设置的状态、API 与副作用
└── tabs/                       接收显式 view-model props 的展示层
palantir-graph/components/
├── Panel.tsx                   选中定义类型的 dispatcher 与只读入口
├── editors/                    Object/Link/Action/Function 编辑器与共享壳
└── panels/                     Sentinel controller、定义/Action 编辑和运行视图
```

Agent 的卡片、drawer、图表和惰性视图仍有同域文件位于 `pages/agent/` 根；
这不是跨域耦合，也不能据此声称该目录已经全部组件化。Settings 的依赖方向
固定为 `SettingsPage → hooks/tabs`；tabs 只 type-import
对应 hook，hooks 不依赖 tabs，也不使用 barrel 隐藏边界。图谱入口保持
`Panel → editors` 单向依赖。Sentinel 面板的 controller/view/compiler 分工及
本地演示初始状态的保留依据见
[`palantir-graph/README.md`](./palantir-graph/README.md)。

## 源码卫生

生产源码必须能从 `main.tsx` 的静态/动态 import 图到达，或者在本目录说明中
登记为有明确删除条件的兼容入口。禁止为“以后可能会用”保留零消费者页面、
API wrapper、组件或 barrel；删除前需同时搜索源码、测试和文档消费者，删除后
至少运行：

```bash
npm run test:e2e:classification
npm run check:feature-boundaries
npm run lint
npm run build
```

当前唯一刻意不从应用入口导入的页面源码是
`pages/overview/OverviewPage.tsx` 两行兼容 re-export，其删除条件按登记的
兼容入口管理；其他生产 TypeScript/TSX 必须可从 `main.tsx` 到达，且全图不得
形成循环依赖。
