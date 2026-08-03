# 生产运行密钥无损解耦与部署配置保全

| 字段 | 内容 |
|---|---|
| 状态 | Validated |
| 日期 | 2026-08-03 |
| 负责人 | Codex（实施） |
| 评审人 | 待维护者指定 |
| Issue/PR | 本地交付；未创建 PR |
| Commit | 本提交 |
| 目标分支 | `nano-ontoprompt` |
| 业务域 | 生产部署、鉴权、共享加密、API Hub、配置与运维 |

## 背景

稳定版依赖变更第一次自动部署在远端配置校验阶段失败，直接原因为服务器持久
`.env` 中的 `SECRET_KEY` 缺失或仍是历史示例值。旧版本允许
`ENCRYPTION_KEY` 为空，并从 `SECRET_KEY` 派生 Fernet key；因此只随机覆盖
`SECRET_KEY` 虽能通过校验，却会让存量业务密文不可读。

同次审计还发现源码上传先把 `.env` 复制到固定 `/tmp/ontologybuild.env`，再删除
整个应用目录。上传中断或残留临时文件可能分别造成配置遗失或陈旧配置恢复。

## 目标

- 完整保留生产 PostgreSQL、API Hub SQLite、Neo4j、MinIO、uploads 与现有账号；
- 在不更新任何业务密文的前提下，把历史派生加密 key 与 JWT `SECRET_KEY`
  解耦；
- 全新安装生成独立随机 `ENCRYPTION_KEY`；
- 源码替换期间让服务器 `.env` 始终留在原路径；
- 密钥、密文和生产连接值不进入日志、提交或测试 artifact。

## 非目标

- 本次不全量轮换 `ENCRYPTION_KEY`，不重加密 PostgreSQL/SQLite；
- 不修改已有管理员密码哈希，不兼容旧 JWT；
- 不改变 HTTP、数据库 schema、Alembic revision、Celery task 或 Compose volume；
- 不读取或修改受保护的 `production.dependencies.env`。

## 当前状态与变更前基线

- `SECRET_KEY` 同时用于 JWT；当 `ENCRYPTION_KEY` 为空时还间接决定 Fernet key；
- 持久 Fernet 数据分布于 PostgreSQL 12 处和 API Hub SQLite 1 个 setting；
- 两个数据库没有共同事务，代码也没有 key id/MultiFernet，不能安全混入一次
  在线全量重加密；
- 失败发生在远端依赖启动、服务停止和 Alembic 之前，本次失败没有更新生产
  数据库或 volume。

## 变更范围

| 模块/路径 | 改动 | API/menu key/数据库/Celery/环境变量影响 |
|---|---|---|
| `scripts/deploy-prod.sh` | 已知旧状态原子固化派生 Fernet key，再轮换 JWT key；全新安装生成独立 key；非 Git 目录与 `.env` 链接 fail closed | 显式写入 `ENCRYPTION_KEY`；不改数据库/Celery/API |
| 部署 workflow | 部署包先上传，清理源码时排除现有 `.env` | 不再创建固定 `/tmp` 密钥副本；volume 不变 |
| 后端部署契约测试 | 覆盖旧密文可读、幂等、缺失默认值、未知短 key 拒绝、大小写/重复 authority、软链接、日志脱敏和上传顺序 | 只使用合成值 |
| 真实存储 CI E2E | PostgreSQL 12 处 + API Hub SQLite 1 处逐字节/解密验证，输出脱敏 artifact | 仅允许显式 E2E 哨兵 + test + 无 query/fragment 的 loopback URL + `_ci`/`_e2e` 数据库；PG transaction rollback |
| 运维文档与 Changelog | 记录边界、影响、恢复和后续轮换要求 | 无运行时契约新增 |

## 密文盘点与兼容策略

PostgreSQL 密文包括模型 API key、Connection JSON、n8n key、Agent 密码、MinIO
两项凭据、Super Assistant MCP headers/env、数据管家浏览器源 endpoint/headers、
手工 Dataset 分享 token 和 Pipeline 文件分享 token；API Hub SQLite 另保存 W3
密码。它们全部调用同一个共享 Fernet helper。

历史状态为 `SECRET=Slegacy, ENCRYPTION_KEY=""`，有效 Fernet key 是
`base64url(SHA256(Slegacy))`。部署把该有效 key 显式写为 `ENCRYPTION_KEY`，同时
生成新的 `SECRET_KEY`。切换前后 Fernet key 完全相同，所有密文保持原字节。
登录 JWT 和短期上传 JWT 失效并要求重新生成；持久分享 token/hash、用户密码
hash、API Hub proxy key 及其他业务数据不变。

只自动识别缺失键、显式空值和仓库两个已知示例值；缺失键按旧 Settings 默认值
派生，显式空值按空字符串派生。未知自定义短 key 原样保留并拒绝部署，
不能猜测、写空或忽略不可读数据。由公开示例派生的 key 仍是安全债务；后续需在
独立停写窗口实现备份、双 key/离线全量迁移和第三方凭据轮换。

## 安全与数据处理

- `.env` 通过同目录 `0600` 临时文件一次替换，替换前后 flush；
- `.env` 缺失默认拒绝 bootstrap；只有显式 fresh-install 确认允许生成新 authority；
- fresh bootstrap 以同目录 hard-link 原子发布，已有路径、并发恢复或软链接都不会
  被覆盖；普通 `.env` 与依赖清单在任何后续失败前限制为 `0600`；
- 日志只说明是否固化/轮换，不输出值；测试确认随机值未出现在 stdout/stderr；
- 源码部署包不含 `.env`，远端删除源文件时明确排除原 `.env`；
- 远端在删除任何旧源码前拒绝 `.env` 软链接，应用目录内的链接目标不会先被
  清理成悬空状态；
- direct-Git 备用部署在任何 fetch/checkout/reset 前拒绝 `.env` 软链接；
- 含依赖清单的部署压缩包在 runner/远端均为 `0600`，远端脚本启动后由 trap
  负责失败清理；
- `FIRST_ADMIN_PASSWORD` 只替换未来 seed；已有 admin 行及 password hash 不变；
- 真正恢复必须把服务器 `.env` 与 PostgreSQL/API Hub volume 作为同一恢复点。

## 验收条件

- 旧 key 产生的合成密文在解耦后仍可解，且部署脚本不接触业务数据库；
- 示例状态只转换一次，后续部署三个运行密钥保持不变；
- 缺失 `SECRET_KEY` 按旧程序实际默认值固化，显式空值按空字符串固化；未知短
  key 不猜测；
- 新安装生成合法、独立的 Fernet key；`.env` 权限保持 `0600`；
- 无 `.env` 且没有 fresh-install 确认时零生成、明确失败；
- 非规范大小写、重复 assignment、`.env`/部署目录软链接均在写入前失败，原配置
  和链接目标不变；
- 远端源码替换在任何 `find` 清理前拒绝 `.env` 软链接；
- 上传脚本先完成 SCP，再清理远端源码，且清理明确排除 `.env`；
- 完整仓库门禁通过；生产发布、canary 和恢复演练仍由受控运维环境执行。

## 验证证据

| 层级 | 实际命令/环境 | 退出结果 | CI URL / artifact / 跳过原因 |
|---|---|---|---|
| 定向部署契约 | `backend/tests/v2/infra/test_production_config.py`；`bash -n scripts/deploy-prod.sh scripts/ci/*.sh`；部署 guard | 73 passed；语法/guard passed | 全合成配置；未读取生产清单 |
| 后端完整 | `uv sync --frozen --group dev && uv run pytest -q --disable-warnings --ignore tests/v2/perf` | 1857 passed，1 skipped | 完整重跑；其后仅前移部署 symlink 守卫，最终整份部署契约另以 73 passed 覆盖 |
| 后端性能 | `uv run pytest -q --disable-warnings tests/v2/perf` | 9 passed | informational suite |
| 配置中心 | `uv sync --frozen --group dev && uv run pytest -q` | 42 passed | 本地工作树 |
| 前端静态/mocked | `npm ci`；unit、feature-boundary、classification、lint、build、mocked E2E | 12 unit、49 classification、81 mocked；其余 passed | 本地工作树；本轮后续未改前端源码 |
| 真实存储 E2E | 一次性 PostgreSQL 16 空库 Alembic 到 `0056_ontology_projection_fence`；当前部署脚本；临时 API Hub SQLite | 12/12 PG + 1/1 SQLite 密文字节不变；13/13 解密；PG rollback | synthetic-only；容器/临时目录已清理；workflow 将上传 `runtime-secret-migration-<run_id>` |
| 文档/仓库卫生 | Markdown self-test/links、部署 guard、repository hygiene、`git diff --check` | 93 files / 513 links，0 error/warning；其余 passed | 本地工作树 |
| 生产发布/恢复 | 不对生产数据执行试探性重加密 | push 后由受控 workflow 验证 | 本次代码路径不更新业务密文；生产备份恢复演练仍由运维窗口执行 |

## 上线步骤、监控指标与观察窗口

先确认一致性备份与无在途 Pipeline 上传，再发布本提交。观察 Actions 中的密钥
固化摘要（无值）、依赖探针、Alembic 和 readiness；发布后验证管理员登录、模型/
Connection/n8n/MinIO/浏览器/MCP 配置读取、两类持久分享链接和 API Hub W3 配置。
至少观察一个 JWT 有效期和一个 Pipeline 周期。

## 回滚触发条件与逐步方案

如果出现任一存量配置不可读、分享链接失效或管理员异常，停止新写入并保留脱敏
记录。因为本次不改业务密文，先停止新版本 runtime，再恢复同一恢复点的 `.env`
与应用版本；如期间已产生新业务写入，不得只回滚某一文件。禁止通过清空密文、
删除数据库或重建 volume 恢复启动。

## 已知风险与后续动作

- 由公开示例派生的历史 `ENCRYPTION_KEY` 只被显式固化，尚未增强保密性；
- 首次远端 verify 暴露部署归档权限守卫的 BSD/GNU `stat` 探测顺序差异；已改为
  GNU 语法优先、BSD/macOS 回退，并在两类环境验证后重新走完整 workflow；
- 后续独立实现 PostgreSQL + API Hub SQLite 的可恢复全量重加密与逐项校验；
- 生产 canary、真实数据只读校验和恢复演练不应在普通开发工作树中执行，证据由
  运维环境保存为 CI artifact。
