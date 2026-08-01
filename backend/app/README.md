# 后端应用目录

`app/main.py` 是当前 FastAPI 装配入口。业务实现优先按稳定能力定位：

```text
app/
├── main.py            FastAPI HTTP composition root
├── config.py          兼容配置导入；canonical 配置在 shared/config.py
├── database.py        SQLAlchemy engine、Session 与 Base
├── deps.py            兼容依赖入口；canonical 依赖在 shared/deps.py
├── bootstrap/         FastAPI 健康检查、生命周期与启动 seed
├── platform/          平台概览
├── super_assistant/   超级助手、Skill 与 MCP
├── exploration/       业务探索
├── ontologies/        本体、映射、图、Agent 与 Sentinel
├── events/            事件登记
├── data_channel/      连接、数据集、流水线与数据管家
├── api_hub/           接口定义、发布、凭据与代理
├── community/         Plugin 社区 MCP 适配；Skill 社区当前无后端
├── model_configs/     模型配置
├── settings/          系统设置
├── auth/              身份、角色与菜单授权
├── inbox/             收件箱契约
├── shared/            迁移期共享基础能力
├── tasks/             Celery 任务入口
├── engine/            预留的运行引擎 package；旧 post-harness 已退役
├── routers/           以兼容 facade 为主，仍有例外
├── models/            以兼容 facade/注册为主，仍有例外
├── schemas/           以兼容 facade 为主，仍有例外
└── services/          以兼容 facade 为主，仍有例外
```

不要根据目录名猜测 canonical。兼容例外、特殊模块身份和迁移顺序见
[后端模块边界](../../docs/architecture/backend-modules.md)。HTTP、menu key、
Alembic revision、Celery task name 和 patch 路径都可能是兼容契约。

两条最复杂的核心链路另有目录入口：

- [数据通道](./data_channel/README.md)
- [本体业务域](./ontologies/README.md)
