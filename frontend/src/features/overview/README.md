# 平台概览功能

本目录是平台概览前端的 canonical 实现，对应：

- menu key：`overview`；
- Hash 路由：`/overview`；
- 后端能力：`backend/app/platform/`；
- HTTP 契约：`GET /api/v1/overview/stats`；
- 浏览器契约测试：`src/test/e2e/overview.spec.ts`。

## 文件

- `OverviewPage.tsx`：驾驶舱页面与查询、跳转编排；
- `cockpit.tsx`、`cockpit.css`：本功能私有的可视化组件和样式；
- `flowengine.tsx`：本功能私有的治理闭环视图；
- `index.ts`：对外公开入口。

应用路由从 `@/features/overview` 导入。旧
`pages/overview/OverviewPage.tsx` 只保留兼容 re-export，禁止继续放入业务
实现。该功能可以依赖共享基础能力，但不得反向依赖 `pages/` 或其他
`features/`；`npm run check:feature-boundaries` 会执行这一约束。
