# ADR-0002：平台概览采用前端 feature 边界

- 状态：Accepted
- 日期：2026-07-30
- 决策人：Repository maintainers
- 关联需求/迭代：[仓库目录与交付治理](../../iterations/2026/2026-07-30-repository-governance.md)

## 背景与约束

平台概览原实现位于 `frontend/src/pages/overview/`，页面、私有可视化组件和
样式已经形成一个内聚单元，但应用只能通过页面技术目录发现它。ADR-0001 要求
前端逐业务能力迁移到 `features/`，且每个能力必须先明确边界、兼容入口和验证
方式。

本次是纯目录迁移，不得改变 `/overview` Hash 路由、`overview` menu key、
`GET /api/v1/overview/stats`、权限行为、页面视觉或流程跳转。其他业务能力
尚未获批迁移，仍以既有 `pages/` 等路径为事实源。

## 决策

1. `frontend/src/features/overview/` 是平台概览前端的 canonical package，
   页面、功能私有组件和样式全部归入该目录。
2. package 只通过 `index.ts` 暴露 `OverviewPage`；`App.tsx` 从
   `@/features/overview` 装配路由。
3. `pages/overview/OverviewPage.tsx` 暂时保留为单行兼容 re-export，不承载
   实现；目录内不保留第二份组件或样式。
4. overview 可以依赖现有 `api/` 等共享基础能力，但不得反向依赖 `pages/`
   或其他 `features/`。跨业务域跳转继续使用公开路由，不直接导入目标业务
   实现。
5. `check-feature-boundaries.mjs` 检查上述依赖方向、兼容 facade 和应用
   canonical import，并由仓库卫生门禁执行。守卫同时维持现有
   `pages/<domain>/` 顶级业务域互不导入；App 装配和测试不在 page 扫描范围。
6. 这项决策只批准 overview 迁移，不能被解释为允许其他业务域提前建立
   `features/<capability>` 并复制现有实现。

## 备选方案

- 保持 `pages/overview/` 不动：风险最低，但不能验证目标 feature 模式和边界
  守卫是否可落地。
- 直接删除旧入口：当前仓库内部虽已改用 canonical import，但会无谓破坏尚未
  清点的分支或扩展代码导入，收益不足。
- 同时迁移共享 API 和 UI：会把目录移动扩大成横向基础设施重构，难以证明
  行为等价。

## 结果与权衡

- 平台概览的页面、私有组件、样式、测试和说明可从一个目录入口发现；
- 应用与新代码有唯一 canonical import，边界守卫阻止重新耦合页面技术目录；
- 迁移期会多保留一个很薄的 facade，但没有双份业务实现；
- `api/`、`components/` 等共享目录暂未迁移，后续由独立 ADR 处理。

## 兼容、迁移与回滚

文件原样移动，旧 `OverviewPage` 路径 re-export 新实现，因此路由、API、
menu key 和 UI 行为保持不变。若上线验证出现目录相关问题，可将四个实现文件
移回 `pages/overview/` 并恢复 `App.tsx` 原导入；不需要数据库或服务端回滚。

删除 facade 前必须确认仓库、测试、扩展和活跃维护分支均无旧路径导入。

## 验证方式

- `npm run check:feature-boundaries`；
- `npm run test:e2e:classification`；
- 定向运行 `overview.spec.ts`，验证统计渲染、Hash 深链、custom 权限和关键
  流程跳转，并验证 custom 空权限落入 `/no-access`；
- `npm run lint`、`npm run build`、`npm run test:e2e:mocked`；
- 文档链接和仓库卫生门禁。
