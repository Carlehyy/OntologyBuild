# 前端测试

测试按成本分成两层：

- [`unit/`](./unit/)：Node 22 内置 test runner 执行的 TypeScript 纯逻辑测试，
  无浏览器和网络依赖；
- [`e2e/`](./e2e/)：Playwright 负责 DOM、路由、API 交互和完整用户旅程。

```bash
npm run test:unit
npm run test:e2e:mocked
```

当前没有引入 Vitest/React Testing Library；需要 DOM 的组件行为仍进入
Playwright，纯函数优先进入 `unit/<domain>/`。每个 E2E spec 必须在
`frontend/playwright.*.config.ts` 中恰好归入
`mocked`、`stack` 或 `external` 一组。分组说明见
[e2e/README.md](./e2e/README.md)。
