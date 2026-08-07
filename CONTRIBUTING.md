# 参与 OntologyBuild 开发

开始前请先阅读 [AGENTS.md](./AGENTS.md) 和
[文档索引](./docs/README.md)。两者共同定义模块边界、测试门禁和文档责任。

## 提交要求

- 从非自动部署分支创建功能分支，禁止直接向 `nano-ontoprompt` 推送结构调整。
- 一个 PR 聚焦一个业务域或一种治理动作。
- 目录移动、行为修改和全量格式化不得混在同一提交。
- PR 描述必须列出兼容影响、执行过的验证命令、结果、上线及回滚方式。

## 提交前最低检查

```bash
git diff --check
node scripts/ci/check-markdown-links.mjs
bash scripts/ci/check-repository-hygiene.sh

cd backend
uv run pytest -q --disable-warnings --ignore tests/v2/perf

cd ../config
uv run pytest -q

cd ../frontend
npm run test:unit
npm run test:e2e:classification
npm run lint
npm run build
npm run test:e2e:mocked
```

完整测试矩阵和需要真实 E2E 的判定条件见
[测试指南](./docs/development/testing.md)。
