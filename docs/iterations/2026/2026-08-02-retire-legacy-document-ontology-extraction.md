# 退役遗留文档到本体抽取链路

| 字段 | 内容 |
|---|---|
| 状态 | Validated（本地实现与合并回归通过；上线验收待 staging/canary） |
| 日期 | 2026-08-02 |
| 负责人 | Codex |
| 评审人 | 未单独指定 |
| Issue/PR | 当前工作树；无独立编号 |
| Commit | 本记录所在提交 |
| 目标分支 | `nano-ontoprompt` |
| 业务域 | 系统设置、本体、数据通道、平台基础设施 |

## 背景

规则设置和提示词模板原本为“上传文档后由 LLM 解析本体结构”服务。当前本体
结构改由本体管理手工创建，或由业务探索对话生成草稿并经人工确认，不再使用
旧的 LLM + 文档抽取。审计确认提示词、文件、execute、v2 extraction 和系统
设置开放接口共同构成同一遗留链路，需要从前后端、数据库、任务、脚本和测试
一起退役。

## 目标

- 删除旧链路全部用户入口和可调用 HTTP operation；
- 删除专用运行时代码、seed、ORM 注册和五张专用表；
- 保持系统设置其他模块、本体手工/业务探索建模及所有独立 MCP 能力；
- 兼容升级前 Celery 队列和仍发送 `simple_llm` 的旧客户端；
- 用负向 OpenAPI、迁移往返和保留能力测试控制回归风险。

## 非目标

- 不删除业务探索、数据管家或共享文档转换能力；
- 不删除本体业务规则、Agent/业务探索内部 prompt、模型配置或 Mapping；
- 不改变 API Hub `/api/api-hub`、raw `/api-hub`、`/proxy` 协议；
- 不删除 MinIO、Plugin 社区或超级助手 MCP；
- 不在 Alembic 中删除物理 uploads 或对象存储内容；
- 不修改 `production.dependencies.env`。

## 当前状态与变更前基线

变更前共有 22 个待删除 OpenAPI operation：

| 子链路 | operation 数 | 路径/方法 |
|---|---:|---|
| 规则设置 | 2 | `GET/PUT /api/v1/settings/rules` |
| 提示词模板 | 8 | `GET/POST /api/v1/prompts`；`GET /templates`；`GET /by-domain/{domain}`；`GET/PUT/DELETE /{prompt_id}`；`POST /generate-template` |
| 系统设置开放接口 | 3 | `GET /api/v1/mcp/info`；`GET /interfaces`；`POST /interfaces/{operation_id}/open` |
| v1 本体文件 | 3 | `GET/POST /api/v1/ontologies/{ontology_id}/files`；`DELETE .../files/{file_id}` |
| v1 execute | 2 | `POST /api/v1/ontologies/{ontology_id}/execute`；`GET .../execute/status` |
| v2 extraction | 4 | `POST .../extract`；`POST .../extract/nl-to-cypher`；`POST .../candidates/approve`；`GET .../extraction/status` |
| **合计** | **22** | raw `/mcp` 不属于 OpenAPI，另行验证退役 |

专用表为 `rules_config`、`prompts`、`uploaded_files`、
`extraction_tasks`、`mcp_interface_configs`。升级前 Celery 任务名为
`app.tasks.extraction.run_extraction`。

## 变更范围

| 模块/路径 | 改动 | API/menu key/数据库/Celery/环境变量影响 |
|---|---|---|
| `frontend/src/pages/settings`、本体详情/创建入口 | 删除规则、提示词、开放接口与文件抽取 UI；系统设置默认用户管理 | 删除对应子导航；旧 Hash 深链重定向到 `/settings/users` |
| `backend/app/settings` | 删除规则持久化、提示词和旧开放接口实现 | 删除规则/提示词/MCP 管理 API；Agent、workflow、MinIO、domains 保留 |
| `backend/app/ontologies`、`data_channel/transforms` | 删除 files/execute/v2 extraction HTTP 与专用 service | 删除 9 个 operation；通用转换、Mapping、图 NL-to-Cypher 保留 |
| `backend/app/tasks/extraction.py` | 改为无数据库依赖的 retired tombstone | task name 不变；结果固定为 `retired` |
| Alembic 0055 | 按依赖顺序删除五张遗留表；downgrade 恢复空 schema | 数据不可由 downgrade 恢复；无环境变量变化 |
| v1 迁移/维护/真实数据脚本 | 删除 prompt/files/execute 假设，供应链脚本改用手工项目 + Mapping | 不再查询已删表或调用已删 API |
| 测试/文档 | 删除旧功能正向用例，增加精确负向与保留能力断言 | Playwright 48 specs：26 mocked、21 stack、1 external |

## 自测中发现并收口的运行时缺陷

完整生产栈验证在本体发布链路中发现：显式业务属性 `id/name` 与旧 `Entity`
信封同名时，正规投影曾把已经恢复的业务主键再次当作保留字段过滤；修正后，
Neo4j 派生投影又会因原样接收嵌套 `__business_properties__` Map 而拒绝全量
重建。两处都属于既有 Mapping 边界缺陷，不通过放宽发布/动作门禁或修改 E2E
数据来掩盖：

- 正规投影只对显式映射或主键声明的同名字段放行，未声明的运行时字段仍过滤；
- Neo4j 继续以稳定 `Entity.id` 作为 MERGE、关系和删除身份，业务冲突字段使用
  `business_*` 别名，完整冲突内容以确定性 JSON 保留；
- 字典、嵌套数组、混合类型数组在 Neo4j 写入边界序列化，实体增量写入、全量
  重建和关系属性使用同一契约；整数只在 Neo4j Int64 范围内原样写入，越界
  标量转字符串，包含越界整数的数组整体转确定性 JSON；
- Mapping 派生图写入使用显式“完整替换属性”语义，普通图 API 仍保持原来的
  merge 默认值，避免增量投影删除字段后 Neo4j 残留旧属性；
- 真实浏览器用例在 Mapping 激活后另发一个 DatasetVersion，等待对应 outbox
  到 `completed`，并断言 `manual_mapping.status=applied`、结果包含目标本体且
  Mapping 保持 `applied` 后才执行动作，消除无订阅事件造成的假通过窗口。

## 兼容策略

- `build_mode=simple_llm` 在本体 create/update schema 边界归一为 `manual`；
- Celery 保留原 task name 消费存量消息，但不访问任何遗留模型或服务；
- `/settings/extraction`、`/settings/rules`、`/settings/prompts`、
  `/settings/open-interfaces` 只作为前端深链兼容，统一跳转用户管理；
- 数据库 downgrade 只用于 schema 往返验证和取证，不属于受支持的应用回滚；
  旧应用恢复必须使用升级前同一时间点的数据库与文件备份；
- 历史迭代和 archive 保留原文，当前事实由本记录、ADR-0003 和模块地图更新。

## 安全与数据处理

升级前必须取得同一时间点的数据库、共享 uploads 和对象存储备份，并导出
`uploaded_files` 行清单。0055 只删除数据库表，不读取、移动或删除物理路径；
这样避免误删共享目录中属于业务探索、数据管家或文件资产的对象。

物理遗留文件清理必须等上线验收和保留期结束，依据升级前清单逐项核对引用，
作为独立运维变更记录操作者、对象列表、备份和恢复方式。禁止对 uploads 根
目录、对象存储 bucket 或未解析变量路径执行递归删除。

## 验收条件

- 22 个遗留 OpenAPI operation 全部不在 schema，raw `/mcp` 返回 404；
- API Hub、MinIO、Plugin 社区、超级助手 MCP 均有正向保留断言；
- 五张遗留表从新库 head 和 0054 升级结果中消失；其他核心表保持；
- 0055 downgrade/re-upgrade 可运行且测试证明降级表为空；
- task registry 仍含旧 extraction task，调用只返回 tombstone；
- `simple_llm` create/update 均写为 `manual`；
- 活跃源码、测试和脚本不再调用 prompt/files/execute/v2 extraction；
- 文档链接、仓库卫生、相关后端与前端门禁通过。

## 验证证据

| 层级 | 实际命令/环境 | 退出结果 | CI URL / artifact / 跳过原因 |
|---|---|---|---|
| 迁移专项 | `cd backend && uv run pytest -q tests/migrations/test_retire_legacy_extraction_migration.py --disable-warnings` | 1 passed | 本地 SQLite，覆盖 0054 → head → 0054 → head |
| 后端退役契约 | `cd backend && uv run pytest -q tests/architecture/test_retired_legacy_extraction_contract.py --disable-warnings` | 4 passed | 本地隔离 SQLite |
| PostgreSQL 迁移 | 本地隔离 PostgreSQL 16：空库 → 0055；另建 0054 副本并向五张退役表各插入有效关联行，再执行 upgrade → downgrade → re-upgrade | 本提交独立预验收 passed；当时唯一 head 为 `0055_retire_legacy_extraction`，合并后的后继迁移为 `0056_ontology_projection_fence` | 五张退役表按预期删除/空表恢复，保留核心表；最终单 head 与完整迁移链见同日稳定依赖迭代记录 |
| Mapping 回归 | `cd backend && uv run pytest -q tests/v2/graph/test_neo4j_service.py tests/v2/mapping/test_projection_adapter.py tests/v2/mapping/test_runtime_hardening.py tests/architecture/test_canonical_import_boundaries.py tests/architecture/test_retired_legacy_extraction_contract.py tests/migrations/test_retire_legacy_extraction_migration.py --disable-warnings` | 64 passed；其中 graph service + projection adapter 核心组合 17 passed | 覆盖增量/全量实体、关系属性、稳定图身份、业务 `id/name`、Int64 边界及两次写入删除旧属性 |
| 配置与前端单元 | `cd config && uv run pytest -q`；`cd frontend && npm run test:unit` | 44 passed；12 passed | 本地 |
| 前端静态门禁 | `npm run check:feature-boundaries`；`npm run lint`；`npm run build` | 214 个生产模块无孤儿/环；lint passed；production build passed | Vite 仅保留既有 chunk size warning |
| 前端分类 | `cd frontend && npm run test:e2e:classification` | 48 specs 唯一分类；22 个 real-suite spec 使用共享凭据 | 本地 |
| 前端 mocked E2E | `cd frontend && npm run test:e2e:mocked` | 80 passed | 本地 Chromium |
| 部署 guard | `bash -n scripts/deploy-prod.sh scripts/ci/test-deploy-guards.sh`；`bash scripts/ci/test-deploy-guards.sh` | passed | 先停旧 runtime 再迁移；仅当 revision 未变化时恢复原先运行的服务；迁移后启动/就绪失败会停止已部分启动的新版 runtime |
| 文档/仓库 | `node scripts/ci/check-markdown-links.mjs`；`bash scripts/ci/check-repository-hygiene.sh`；`git diff --check` | 91 files / 502 links，0 error/0 warning；hygiene passed；diff check passed | 本地 |
| 完整后端 | `cd backend && uv run pytest -q --disable-warnings --ignore tests/v2/perf` | 1713 passed，1 skipped | 本地隔离 SQLite；跳过项为已有显式 skip |
| 本地候选 production Compose stack | `PUBLIC_PORT=5173 docker compose -p codex-retire-settings -f docker-compose.prod.yml ...`；从空卷升级 0055 后 `npm run test:e2e:stack -- --workers=1` | 全量 48 passed，6 个有意跳过；11 个 Mapping 全部 `applied`，39 个 DatasetVersion 事件全部 `completed` | `.artifacts/playwright/stack-results/`（本地 ignored 证据）；健康检查全部 `ok`，投影日志无错误；staging artifact 待补 |
| 最终镜像 Mapping 浏览器复验 | `PLAYWRIGHT_BASE_URL=http://127.0.0.1:5183 PLAYWRIGHT_API_URL=http://127.0.0.1:5183 PLAYWRIGHT_PORT=5183 npm run test:e2e:stack -- src/test/e2e/ontology_evolution.spec.ts --workers=1` | 最终 backend/celery/frontend 镜像连续 2 次通过（54.7s、29.3s）；6 个事件全部 `completed`、0 个 `last_error`，2 个发布后事件明确为 `manual_mapping=applied` | 首次成功运行覆盖 Chroma 冷启动下载瞬时失败后的 durable retry；缓存就绪后的第二次运行无新增投影异常；临时 Compose 项目与卷已精确清理 |
| Nginx/API 本地预验收 | 对候选 frontend 容器执行 `curl` HTTP 探针；容器内生成 OpenAPI | `/` 200、`/api/health` 200、`/mcp` 404、`/mcp/` 404、`/mcp/minio` 401、`/api-hub/mcp` 503；528 operations | 本地候选 production Compose 镜像；staging artifact 待补 |
| external 付费 E2E | 未执行 | 测试要求显式外部模型凭据和付费开关，本次变更未获授权使用 | 上线前按需在 staging/canary 执行 |
| 生产备份恢复/canary | 未执行 | 本地已完成代表性 0054 PostgreSQL 副本往返，但无生产备份系统或生产数据 | 上线前由运维执行并留 CI artifact |

## 上线步骤、监控指标与观察窗口

1. 停止发布，记录 backend、Celery worker、frontend 中原先确实在运行
   的服务，并确认目标版本和 broker 中旧 extraction 消息量；
2. 备份数据库、共享 uploads、对象存储及当前镜像/Compose，并验证可读；
3. 导出五张表行数及 `uploaded_files` 物理路径清单，保存到受控运维 artifact，
   不提交仓库；
4. 先停止 frontend、Celery worker 和 backend，在旧 runtime 不再访问数据库
   后运行 0055，再启动新 API/worker/frontend；
5. 执行负向/正向契约和手工/业务探索建模旅程；canary 部署后观察
   至少一个完整业务周期，关注 404、未注册 Celery task、
   数据库缺表异常、旧深链访问和 MCP 401/503/成功率；
6. 确认无需应用回滚并经过保留期后，再单独审批物理遗留文件清理。

## 回滚触发条件与逐步方案

若 0055 未成功到达 head，部署脚本还会核对数据库 revision；只有 revision 与
停机前完全相同，才重启升级前确实运行的旧服务。revision 无法证明或发生部分
推进时拒绝自动重启。
若 0055 已成功而新服务启动或健康检查失败，禁止重启旧应用直接连接
已删表 schema；部署脚本先停止任何已部分启动的新版 runtime，再保持停机，
恢复同一时间点的数据库与文件备份并部署上一镜像，或执行经批准的前向修复。
仅执行 Alembic downgrade 只会得到空表，不能满足数据回滚。非预期 404、
保留 MCP 失效、旧队列无法消费、数据库缺表影响非遗留业务或手工/业务探索
建模失败均触发停止 canary 扩量。

## 已知风险与后续动作

- 代表性 0054 PostgreSQL 副本和本地候选 production Compose stack 仅构成开发
  预验收，不能替代仓库门禁要求的隔离 staging/canary。上线前必须在那里重跑
  PostgreSQL 迁移往返、真实 stack/Nginx 探针与相关 E2E，并把证据保存为 CI
  artifact；真实生产备份恢复和 canary 观察窗口也仍需由运维执行；
- Chroma 首次生成 embedding 时可能下载模型；本地最终镜像验证观察到一次
  瞬时 SSL 中断，DatasetVersion durable outbox 自动重试后成功，缓存就绪后的
  再次运行无新增投影错误。staging/canary 仍需验证受控网络与模型缓存策略；
- 物理遗留 uploads 暂时保留，占用空间但可避免误删；后续清理必须独立审计；
- `app.tasks.extraction.run_extraction` tombstone 需保留到确认所有支持环境的旧
  队列和定时消息耗尽，再由独立兼容移除决策处理。
