# 前端仓库守卫

本目录只保留可重复执行的前端测试/结构守卫：

- `README.md`：本说明；
- `run-unit-tests.mjs`：递归收集 `src/test/unit/**/*.test.ts`，用 Node 22
  内置 runner 和 TypeScript 类型剥离执行，无测试时失败；
- `check-e2e-classification.mjs`：保证每个 Playwright spec 恰好属于一个
  mocked、stack 或 external 分组；
- `check-feature-boundaries.mjs`：检查已迁移 feature 的依赖方向、兼容入口和
  canonical import，禁止 `pages/<domain>/` 导入兄弟 page domain，并验证
  生产源码零未登记孤儿、零循环依赖；
- `check-color-tokens.mjs`：颜色令牌门禁（DESIGN.md §2.4/§8），禁止
  tokens.css / echartsTheme.ts 之外新增硬编码颜色（hex、rgb(a)、hsl(a)），
  存量违例连同数量棘轮锁定在 `color-gate-manifest.mjs`，只许收敛不许扩散；
  TS/TSX 侧的 ESLint 实时约束从同一份清单派生豁免。

这里不再存放一次性调试、截图、数据修补或手工验收脚本。正式浏览器场景统一
写入 [`src/test/e2e/`](../src/test/e2e/)；历史一次性脚本已删除，需要追溯时
使用 Git 历史。
