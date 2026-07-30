# 模块责任与评审

当前仓库尚未维护人员级 CODEOWNERS。本文件先定义能力责任，具体负责人应由
项目管理员补充到团队配置；在此之前，相关变更至少需要一名熟悉该能力的维护者
评审。

| 能力 | 主要目录 | 必需评审关注 |
|---|---|---|
| 本体运行时 | `backend/app/ontologies`、前端本体/Agent | 发布、版本、事实、动作、Sentinel |
| 数据通道 | `backend/app/data_channel`、前端 pipelines/asset lake | 数据丢失、异步任务、文件资产 |
| API Hub | `backend/app/api_hub`、前端 api-hub | 出站安全、凭据、代理授权 |
| 助手与探索 | `super_assistant`、`exploration`、前端对应页面 | 工具边界、会话隔离、SSE |
| 身份与系统设置 | `auth`、`settings`、前端 settings | RBAC、menu key、秘密 |
| 平台运维 | Compose、Actions、部署脚本、配置中心 | 迁移、依赖、健康检查、回滚 |

迭代记录中必须注明实际负责人和评审人，不能只写抽象团队名。
