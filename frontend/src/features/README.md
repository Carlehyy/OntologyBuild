# 前端业务功能

`features/` 按稳定业务能力组织前端实现。只有已通过迁移 ADR、具备公开
`index.ts`、目录说明和自动化测试的业务域，才能在这里建立目录。

## 当前功能

- [`overview/`](./overview/)：平台概览驾驶舱，对应 menu key `overview` 和
  Hash 路由 `/overview`。

业务功能可以依赖 `api/`、`components/`、`config/`、`stores/`、`lib/`、
`types/`、`utils/` 等现有共享基础能力。在 `shared/` 迁移完成前，不复制这些
实现。业务功能不得反向依赖 `pages/`，也不得直接依赖其他 `features/`；跨域
组合应由应用路由或明确的公开契约完成。
