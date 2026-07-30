# Alembic env.py 修复调用方显式 URL 被本地 .env 覆盖

| 字段 | 内容 |
|---|---|
| 状态 | Validated |
| 日期 | 2026-07-30 |
| 负责人 |  |
| 评审人 |  |
| Issue/PR |  |
| Commit |  |
| 目标分支 |  |
| 业务域 | 平台基础设施（Alembic 迁移运行时） |

## 背景

`tests/v2/migrations/` 下 13 个迁移测试稳定失败，报错
`sqlite3.OperationalError: table rules_config already exists`，发生在
`upgrade -> 0001_full_baseline` 的第一步。

根因不在迁移脚本：`backend/alembic/env.py` 用
`"database_url" in settings.model_fields_set` 判断是否用
`settings.database_url` 覆盖调用方显式设置的 `sqlalchemy.url`。
`Settings` 同时读取 gitignored 的本地 dotenv（`backend/.env`、
`config/generated/local/.env`），因此只要开发机存在 `backend/.env`
（本机 `DATABASE_URL=sqlite:////tmp/ontoprompt.db`），迁移测试的临时库
URL 就被静默覆盖，全部 `command.upgrade` 实际打在开发库上。该开发库由
启动时 `create_all` 管理（104 张表、`alembic_version` 为空），Alembic
从 base 起跑，0001 基线的第一个 `CREATE TABLE rules_config` 即冲突。

该覆盖逻辑由 9fb2e680（feat(config): add local full-stack configuration
center）引入；此前实现只认进程环境变量 `DATABASE_URL`，测试隔离正常。
CI 干净检出无 `.env`、pytest 步骤也不设 `DATABASE_URL`，不触发此路径。

## 目标

- 调用方显式提供的 `sqlalchemy.url`（迁移测试、自定义 Config）始终权威；
- 本地开发 / 生产经 `alembic upgrade head` 启动时仍从 Settings
  （进程环境变量 + 本地 dotenv）解析占位 URL。

## 非目标

- 不改动任何迁移版本脚本及已发布迁移语义；
- 不改动测试断言与测试组织；
- 不处理 `/tmp/ontoprompt.db` 开发库自身的 stamp/升级（属运维操作）。

## 当前状态与变更前基线

修复前：`tests/v2/migrations/` 13 failed, 10 passed。

## 变更范围

| 模块/路径 | 改动 | API/menu key/数据库/Celery/环境变量影响 |
|---|---|---|
| `backend/alembic/env.py` | 仅当 `sqlalchemy.url` 为仓库占位符（`driver://` 前缀）时才用 `settings.database_url` 覆盖；删除 `model_fields_set` 分支 | 无契约影响；`alembic.ini` 占位符与 `DATABASE_URL` 解析链路不变 |

## 兼容策略

- 生产/本地 CLI 启动：`alembic.ini` 保持占位符 → 仍由 Settings 解析
  （进程 `DATABASE_URL` 优先，其次本地 dotenv），行为不变；
- 迁移测试：显式临时库 URL 不再被覆盖，恢复 9fb2e680 之前的隔离语义；
- 无任何部署脚本改写 `alembic.ini`（已全仓检索确认），无其他代码路径
  依赖被删除的 `model_fields_set` 分支。

## 安全与数据处理

修复前测试会对开发库执行建表乃至 `DROP TABLE` 前置步骤（因 0001 失败
未实际执行到 DROP），存在误伤本地开发数据的风险；修复后测试完全隔离，
已确认运行前后 `/tmp/ontoprompt.db` mtime 不变。

## 验收条件

- `tests/v2/migrations/` 全部通过；
- 全新库 `DATABASE_URL="sqlite:////tmp/verify_mig.db" alembic upgrade head`
  成功且单 head；
- 无进程 `DATABASE_URL` 时本地 dotenv 解析路径仍可用（offline `--sql`
  非破坏性验证）。

## 验证证据

| 层级 | 实际命令/环境 | 退出结果 | CI URL / artifact / 跳过原因 |
|---|---|---|---|
| 单元 | `cd backend && .venv/bin/python -m pytest tests/v2/migrations/ -q --disable-warnings` | 23 passed |  |
| 集成/契约 | `cd backend && DATABASE_URL="sqlite:////tmp/verify_mig.db" .venv/bin/alembic upgrade head && .venv/bin/alembic heads` | 99 表、单 head `0054_fact_lineage_indexes`、stamp 正确；验证后删除临时库 |  |
| 集成/契约 | `cd backend && .venv/bin/python -m pytest tests/migrations/ -q` | 2 passed |  |
| 本地解析 | `env -u DATABASE_URL .venv/bin/alembic upgrade 0001_full_baseline --sql` | 经 `backend/.env` 解析并输出 SQLite DDL | 非破坏性 offline 模式 |
| 前端静态 | 不涉及 | — | 后端 Alembic 运行时变更 |
| mocked 浏览器 | 不涉及 | — | 同上 |
| stack/external E2E | 未执行 | — | 无对外契约变更；CI 本就覆盖全新库 upgrade head 步骤 |
| 部署/回滚 | 不涉及 | — | 仅测试期 URL 解析顺序变化 |

## 上线步骤、监控指标与观察窗口

随常规合并流程；无需特殊发布动作。

## 回滚触发条件与逐步方案

如生产 `alembic upgrade head` 解析到错误 URL，回退 `backend/alembic/env.py`
单文件即可。

## 已知风险与后续动作

- 开发者若曾用旧逻辑意外把迁移跑到本地开发库，需自行
  `alembic stamp` 或重建该库；本变更不做运维处置。
- 本机 `/tmp/ontoprompt.db` 为 `create_all` 管理且 `alembic_version`
  为空，属历史遗留；需要升级该库时按运维手册单独处理。
