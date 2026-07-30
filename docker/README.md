# Docker 资源

```text
docker/
├── browser/Dockerfile              数据管家浏览器运行时
└── postgres/init-litellm-db.sql    本地 Compose 的 LiteLLM 数据库初始化
```

根目录三份编排均为当前入口：

| 文件 | 角色 |
|---|---|
| `docker-compose.v2.yml` | 推荐的本地核心完整栈 |
| `docker-compose.yml` | 含 LiteLLM 和 Celery Beat 的扩展本地开发栈 |
| `docker-compose.prod.yml` | 严格生产编排 |

服务集合重叠不是删除依据。修改构建上下文、服务名、volume、健康检查或相对
路径时，必须运行对应 Compose config/build 和部署测试。

`backend/.dockerignore` 与 `frontend/.dockerignore` 会排除测试源码、手工脚本、
本地依赖和运行证据；这些内容留在仓库/CI，不进入生产运行镜像。
