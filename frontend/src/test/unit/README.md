# 前端单元测试

纯函数和无浏览器依赖的业务规则按
`unit/<domain>/<module>.test.ts` 就近组织。当前使用 Node 22 内置 test runner
和类型剥离，不引入第二套测试框架；React DOM、路由、请求拦截和完整用户旅程
仍由 `../e2e/` 的 Playwright 分组负责。

```bash
npm run test:unit
```

单元测试必须确定、无网络、无数据库、无时间顺序依赖。若代码依赖 DOM、Store
副作用或 API，应先提取纯边界，不能在这里伪造一套与生产不同的实现。
