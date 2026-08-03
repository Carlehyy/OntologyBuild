# 配置与秘密管理

## 配置分区

- `.env.example`：本地开发与 Compose 的无秘密模板；
- `config/generated/local/.env`：本地配置中心生成，不进入 Git；
- `backend/.env`：旧本地兼容入口，不应作为新配置源；
- `.coze`：Coze 运行器兼容配置，前端同样消费 npm 的
  `frontend/package-lock.json`；
- `production.dependencies.env`：当前自动部署读取的生产第三方依赖清单；
- `production.dependencies.example.env`：不含秘密的人工校验与后续迁移模板。

直接启动应用进程时，系统环境变量仍按框架规则具有最高优先级。本地完整模式由
配置中心生成统一环境文件；生产部署则先保留服务器已有 `.env`，再把当前版本
中的 `production.dependencies.env` 合并进去，并以合并后的 `.env` 作为唯一
Compose 权威。`scripts/deploy-prod.sh` 的所有 Compose 调用都会清除宿主 shell
中同名的环境、端口、依赖、镜像及严格镜像校验变量，避免已验证配置被临时
`export` 静默覆盖。

生产清单只保留运行时实际消费的连接事实：PostgreSQL 以 `DATABASE_URL` 为
连接权威，且服务器最终 `.env` 统一使用 `postgresql://`；部署入口会兼容读取
旧清单中的 `postgres://` 与 `postgresql+psycopg2://`，但在校验和启动前规范化
为 `postgresql://`，不会把别名继续写入运行配置。MinIO 以
`MINIO_ENDPOINT`（S3 API 端口）为连接权威；历史
`POSTGRES_HOST`、`POSTGRES_PORT`、`MINIO_CONSOLE_URL` 字段部署时仅为旧清单
兼容而忽略，不再写入运行环境。n8n 调用超时由 `N8N_TIMEOUT_SECONDS` 控制，
生产模板和 Compose 默认均为 30 秒。

Redis 客户端连接以 `REDIS_URL` 为唯一清单权威。使用随 Compose 启动的 Redis
时填写 `redis://:<password>@redis:6379/0`，部署脚本从同一 URL 推导服务端
`requirepass`；内置密码必须至少 16 位，并只使用字母、数字、`.`、`_`、`~`、
`-`，无需 URL 转义。旧清单中精确的 `redis://redis:6379/0` 会在服务器生成态
安全升级并同时持久化服务密码和带密码 URL。外部 Redis（包括 `rediss://`）的
URL 保持原样；Compose 仍启动的内置 Redis 使用独立本地密码，但 backend 和
worker 不会连接它。

## 应用密钥边界

`SECRET_KEY` 与 `ENCRYPTION_KEY` 不能作为同一个可随意替换的“应用密码”处理：

- `SECRET_KEY` 用于登录 JWT 和短期 Pipeline 文件上传 JWT；
- `ENCRYPTION_KEY` 用于 PostgreSQL 中保存的模型、Connection、n8n、Agent、
  MinIO、浏览器源、分享令牌和 MCP 凭据，以及 API Hub SQLite 中的 W3 密码；
- 用户密码哈希、API Hub proxy key 和设备/分享 token hash 不依赖上述可逆密钥。

只有在维护者已经证明 PostgreSQL、API Hub、Neo4j、MinIO、uploads 等全部持久
存储均为空，并通过手工 `workflow_dispatch` 勾选 fresh-install 确认时，服务器
才由 `scripts/deploy-prod.sh` 分别生成随机 `SECRET_KEY` 和独立 Fernet
`ENCRYPTION_KEY`。普通 push 或未确认的手工部署遇到 `.env` 缺失会 fail closed，
避免“配置文件丢了但数据还在”时生成新的、无法解密旧数据的 authority。
生产部署当前只支持应用目录内权限为 `0600` 的普通 `.env` 文件；有效或失效的
软链接都会在写入前被拒绝，以免一次原子更新悄悄替换 secret mount。若使用外部
秘密管理器，应先由受控发布步骤把完整配置原子落成该普通文件，再启动部署。

历史版本允许 `ENCRYPTION_KEY` 为空，此时实际加密密钥是从
当时生效的 `SECRET_KEY` 派生出来的。部署脚本会区分“没有 `SECRET_KEY` 行”与
“存在 `SECRET_KEY=` 空行”：前者旧程序使用 Settings 默认值，后者会以空字符串
覆盖默认值，两者派生 key 不同。对这些状态或仍为仓库已知示例值的旧安装，部署
脚本会先把这个**原有效加密密钥**固化为显式
`ENCRYPTION_KEY`，再更换 JWT `SECRET_KEY`。该状态转换不更新 PostgreSQL、
API Hub SQLite 或任何业务行，存量密文字节保持不变。

新 backend/worker 使用转换后的配置启动时，旧登录会话和尚未消费的短期上传
令牌会失效，用户需要重新登录；永久
Dataset/File 分享链接仍保留。`FIRST_ADMIN_PASSWORD` 的示例值也会被替换为随机
的未来 seed，但已有管理员的数据库密码哈希不会改变。未知的自定义短
`SECRET_KEY` 不会被猜测或自动迁移，部署会保留原值并明确失败。

不得手工同时生成新的 `SECRET_KEY` 和 `ENCRYPTION_KEY`，不得先更换
`SECRET_KEY` 后再补 `ENCRYPTION_KEY`，也不得为通过校验而清空密文。由公开示例
值派生的历史加密密钥虽然经显式固化后能完整保留数据，但保密强度没有因此
提高；真正轮换 `ENCRYPTION_KEY` 必须作为独立维护变更，在 PostgreSQL 与 API
Hub SQLite 完整备份、停写、全量解密校验和可恢复迁移机制就绪后执行。

## 必需运行依赖

| 依赖 | 运行责任 | 未就绪行为 |
|---|---|---|
| PostgreSQL | 平台主数据库、Alembic、发布与审计事实 | 启动/readiness 失败；不切平台 SQLite |
| Redis + Celery worker | durable 入队、异步调度和后台执行 | readiness/入队失败；异步路径不自动改在 API 线程执行，显式同步 API 保持原契约 |
| Neo4j | 图查询、分析和可重建发布投影 | 图接口返回明确 503，发布/投影 fail closed；不切 NetworkX/SQL 图 |
| MinIO | 新文件与对象的权威对象存储 | 写入失败；不切本地对象目录 |
| n8n | 数据管家工作流执行 | 启动/readiness 失败，不按可选增强跳过 |
| Chromium CDP | 浏览器接管与数据管家浏览器会话 | `STEWARD_BROWSER_CDP_URL` 只接受 HTTP(S) 服务根地址；启动配置必需，不可达不杀死 API liveness，但 readiness 失败，不用 mock 冒充就绪 |

外部依赖必须在本地配置中心或生产只读校验中提供完整配置，并以真实服务探针
验证；API 与 worker 启动后还必须通过 `/health/ready` 和 Celery ping。
`/health/live` 只证明 API 进程存活，不能替代上述检查。

ChromaDB 已从依赖、配置模型和运行链路移除。旧 Chroma 配置不应继续复制到新
环境；关键词搜索使用 PostgreSQL，语义搜索和统一 semantic 模式返回 501。
LLM 不参与启动门禁：平台就绪后，管理员在“模型配置”页面按需添加提供商、
模型和凭据。

n8n 在非测试环境中只有一个配置权威：启动环境中的 `N8N_API_URL`、
`N8N_API_KEY` 与 `N8N_TIMEOUT_SECONDS`（本地由配置中心生成）。启动同步仅将
这组值镜像到历史数据库记录，运行时客户端、系统设置读取和“测试连接”均使用
启动值；`PUT /api/v1/settings/workflow-config` 返回 409，不允许用数据库/UI
覆盖。系统设置的“工作流配置”页因此只读展示脱敏状态并保留连接测试。修改后
必须重启 API 与 worker，使所有进程同时切换到同一配置。只有
`ENVIRONMENT=test` 可注入并持久化隔离 n8n 配置，以支持确定性测试。
`N8N_API_URL` 的新模板统一填写 n8n 服务根地址（例如
`https://n8n.example.com`）；运行时会规范化为 `/api/v1`。历史清单中已经带
`/api/v1` 的地址继续兼容，不要求为本次升级改写。

非测试 API 的 lifespan 会先完成 PostgreSQL、Redis、Neo4j、MinIO、n8n 的
真实连接探测，再初始化 Neo4j 索引、修复非 ready 本体投影，最后才启动 API
Hub、Sentinel、数据调度器和 MCP 会话。任一阻塞型依赖或投影修复失败都不会
留下后台 worker。Chromium CDP 同样会在此时探测，但只记录提示并允许 API
进程提供诊断；`/health/ready` 在 CDP 恢复前仍返回 503。
CDP 配置只填写服务根地址（例如 `http://browser:9222`）；平台会自行请求
`/json/version`。带该 discovery 路径、查询参数、fragment 或 URL 用户信息的
地址会在配置阶段被拒绝，避免配置校验通过后实际探测到重复路径。

明确例外只有：API Hub 自有 SQLite；`ENVIRONMENT=test` 的隔离 SQLite 与 n8n
配置注入；历史 `local://` 对象的只读读取/迁移。新平台数据不写入这些兼容路径。

早期开发模式曾有一段时间会把 DatasetVersion、FileAsset、Media、FileConnector
等平台对象写到数据库中“系统设置”保存的 MinIO 端点。该端点现在只通过显式的
legacy read/list/delete 适配器参与回归时代对象迁移：仅当权威环境 MinIO 已正常
响应“对象不存在”（DatasetVersion 还允许 checksum 不匹配）时才会尝试，环境
MinIO 连接/鉴权/服务故障不会触发它。适配器不暴露上传能力；迁移完旧对象并核验
内容后，所有新对象继续只写环境 `MINIO_*` 所指向的端点。

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
显式 `DEPLOY_HEALTH_URL` 必须是无空白、无原始单引号的 HTTP(S) URL；不合法值
会在发起 SSH 前失败。

`DEPLOY_APP_DIR` 为空时使用 `/opt/ontologybuild`。自定义值必须是规范化的
绝对路径，并位于某个顶层目录之下；根目录、顶层目录本身、`.`/`..` 段、重复
或末尾斜杠，以及空格、引号、控制字符和 shell 标点都会在任何 SSH 删除/解包
命令前被拒绝。远端最终路径还必须是真实目录而非软链接，避免源码替换跟随链接
写入另一个目录。

## 首个管理员配置

`.env.example` 中的 `FIRST_ADMIN_USER=admin` 和示例密码只用于本地模板。首次
安装在确认全部持久存储为空后，必须从 Actions 手工运行 workflow 并勾选
`bootstrap_production_env`；脚本随后保留默认用户名 `admin`，为
`FIRST_ADMIN_PASSWORD` 生成随机值，将 `.env` 设为 `0600`，且不会把值打印到
Actions 日志。普通 push 不具有创建新加密 authority 的权限，后续部署会保留
服务器已有 `.env`。

`FIRST_ADMIN_PASSWORD` 只在数据库没有任何 admin 时用于 seed。管理员一旦创建，
数据库中的密码哈希就是登录事实源；只编辑 `.env` 不会轮换现存账号密码。
初次读取、立即轮换和账号恢复步骤见
[部署手册](./deployment.md#首次登录与管理员密码恢复)。

镜像变量来自服务器最终合并后的 `.env`。其中
`STRICT_IMAGE_DIGESTS=true` 会要求全部镜像使用 `@sha256`，合法值为
`true/false`、`yes/no` 或 `1/0`。部署入口不接受宿主 shell 中的同名变量覆盖
`.env`；如需改变策略，必须先修改持久配置并重新执行完整校验。

## 安全要求

- 不在 PR、Issue、日志或迭代文档中粘贴真实值；
- 现有跟踪清单只作为仓库所有者批准的临时兼容例外，不得复制到其他文件；
- 新增或轮换凭据时应优先规划逐项 Secret，使其可独立轮换和撤销；
- 生产依赖变化必须同时更新部署验证和本说明；
- 内置 Redis 的 `REDIS_URL` 与 `requirepass` 必须由部署脚本一次性规范化，
  禁止在服务器 `.env` 中手工维护两份不一致的密码；
- 不得用测试 SQLite、API Hub SQLite 或历史 `local://` 兼容路径绕过必需依赖；
- 删除当前文件前必须先完成新配置源迁移和部署验证；删除工作树文件不会清理
  Git 历史。
