# 开发文档

```text
development/
├── README.md
├── setup.md                    本地环境与启动
└── testing.md                  测试分层和强制门禁
```

命令必须与 `pyproject.toml`、`uv.lock`、`package.json` 和 GitHub Actions
一致。新命令只有在本地或 CI 实际执行通过后才能写入本文档。

开始开发前先读仓库根目录 [AGENTS.md](../../AGENTS.md)，再读
[本地开发](./setup.md) 和 [测试指南](./testing.md)。
