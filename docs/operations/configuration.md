# 配置与秘密管理

## 配置分区

- `.env.example`：本地开发与 Compose 的无秘密模板；
- `config/generated/local/.env`：本地配置中心生成，不进入 Git；
- `backend/.env`：旧本地兼容入口，不应作为新配置源；
- `.coze`：Coze 运行器兼容配置，前端同样消费 npm 的
  `frontend/package-lock.json`；
- `production.dependencies.env`：当前自动部署读取的生产第三方依赖清单；
- `production.dependencies.example.env`：不含秘密的人工校验与后续迁移模板。

系统环境变量优先级最高。本地完整模式由配置中心生成统一环境文件；生产部署
会先保留服务器已有 `.env`，再把当前版本中的 `production.dependencies.env`
合并进去。

## 当前自动部署兼容策略

仓库所有者已明确决定在持续频繁开发期间暂时保留现有生产依赖清单。因此本轮
目录治理不迁移它的配置来源，也不要求新建 GitHub `production`
Environment。工作流继续使用：

- 仓库中已跟踪的 `production.dependencies.env` 作为依赖事实源；
- GitHub Repository Secrets 中已有的 SSH 参数负责传输和远端执行；
- `scripts/deploy-prod.sh` 在日志中只报告应用字段数，不打印字段值；
- 部署包仍以 `0600` 权限应用清单，并保留服务器已有 `.env`。

日常功能、重构和文档 PR 不得修改、复制或回显该文件。后续迁移到逐项 GitHub
Environment Secrets/Variables 时，使用
`production.dependencies.example.env` 和
`scripts/ci/materialize-production-dependencies.sh` 作为迁移工具，并在独立
运维变更中验证审批、端口、严格模式和回滚，不能与目录整理混在一起。

## GitHub 部署传输参数

部署传输参数由工作流直接读取：

| GitHub Secret | 是否必填 | 工作流行为 |
|---|---:|---|
| `DEPLOY_HOST` | 是 | SSH/SCP 目标；空值立即失败 |
| `DEPLOY_PASSWORD` | 是 | 当前密码式 SSH 凭据；空值立即失败 |
| `DEPLOY_USER` | 否 | 为空时使用 `root` |
| `DEPLOY_APP_DIR` | 否 | 映射为远端 `APP_DIR`，默认 `/opt/ontologybuild` |
| `DEPLOY_HEALTH_URL` | 否 | 映射为 `HEALTH_URL`；为空时按 `PUBLIC_PORT` 生成 |

`DEPLOY_HEALTH_URL` 未设置时，`PUBLIC_PORT=80` 使用
`http://${DEPLOY_HOST}/`，其他端口使用
`http://${DEPLOY_HOST}:${PUBLIC_PORT}/`。这里的 `${...}` 表示工作流运行时
取值，不是要求把字面量保存为 Secret。当前已跟踪清单继续使用 `8088`，因此
本轮整理不会改变现有公网端口。

`DEPLOY_APP_DIR` 为空时使用 `/opt/ontologybuild`。自定义值必须是规范化的
绝对路径，并位于某个顶层目录之下；根目录、顶层目录本身、`.`/`..` 段、重复
或末尾斜杠，以及空格、引号、控制字符和 shell 标点都会在任何 SSH 删除/解包
命令前被拒绝。

## 首个管理员配置

`.env.example` 中的 `FIRST_ADMIN_USER=admin` 和示例密码只用于本地模板。首次
部署且服务器尚无 `.env` 时，`scripts/deploy-prod.sh` 会保留默认用户名
`admin`，为 `FIRST_ADMIN_PASSWORD` 生成随机值，将 `.env` 设为 `0600`，且
不会把值打印到 Actions 日志。后续部署会保留服务器已有 `.env`。

`FIRST_ADMIN_PASSWORD` 只在数据库没有任何 admin 时用于 seed。管理员一旦创建，
数据库中的密码哈希就是登录事实源；只编辑 `.env` 不会轮换现存账号密码。
初次读取、立即轮换和账号恢复步骤见
[部署手册](./deployment.md#首次登录与管理员密码恢复)。

镜像变量来自服务器最终合并后的 `.env`。其中
`STRICT_IMAGE_DIGESTS=true` 会要求全部镜像使用 `@sha256`；只有显式导出同名
进程环境变量时才覆盖 `.env`，合法值为 `true/false`、`yes/no` 或 `1/0`。

## 安全要求

- 不在 PR、Issue、日志或迭代文档中粘贴真实值；
- 现有跟踪清单只作为仓库所有者批准的临时兼容例外，不得复制到其他文件；
- 新增或轮换凭据时应优先规划逐项 Secret，使其可独立轮换和撤销；
- 生产依赖变化必须同时更新部署验证和本说明；
- 删除当前文件前必须先完成新配置源迁移和部署验证；删除工作树文件不会清理
  Git 历史。
