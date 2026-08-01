# 稳定版必需运行依赖与降级移除

| 字段 | 内容 |
|---|---|
| 状态 | Validated |
| 日期 | 2026-08-02 |
| 负责人 | Codex（实施） |
| 评审人 | 待维护者指定 |
| Issue/PR | 本地交付；未创建 PR |
| Commit | 本地提交（见 Git 历史）；未 push |
| 目标分支 | `nano-ontoprompt` |
| 业务域 | 平台启动、共享基础设施、数据通道、本体图、搜索、配置与运维 |

## 背景

稳定版发布前审计发现，旧实现和文档允许部分外部依赖缺失时继续启动，并静默
切换平台 SQLite、API 进程线程、NetworkX/SQL 图或本地对象存储。ChromaDB
同时被描述为可选向量投影，语义搜索与关键词搜索的支持边界不明确。这些行为会
让 liveness 被误认为完整平台 ready，也会产生不同环境下无法对账的运行结果。

## 目标

- 正常启动必须配置并真实验证 PostgreSQL、Redis、Celery worker、Neo4j、
  MinIO 和 n8n；Chromium CDP 必须加入启动配置且参与深度 readiness，
  运行中暂时不可达不直接杀死 API 进程；
- 移除静默运行时降级，并让依赖失败通过启动、readiness、HTTP 或任务状态
  明确可见；
- 完全移除 ChromaDB、向量投影和语义检索实现；
- 保留 PostgreSQL 关键词搜索，语义与统一 semantic 模式固定返回 501；
- LLM 不阻断基础平台启动，改为启动后从模型配置页按需配置；
- 保留并明确 API Hub SQLite、测试 SQLite 和历史 `local://` 只读迁移兼容。

## 非目标

- 不改变 API Hub 自有 SQLite 的存储架构；
- 不取消 `ENVIRONMENT=test` 的隔离 SQLite/mocks；
- 不删除迁移前 `local://` 对象，亦不允许向该路径写入新对象；
- 不把 LLM 提供商或真实凭据写入启动默认值；
- 不在本记录中复制或回显生产依赖清单的任何真实值；
- 不改变 HTTP menu key、数据库表名、Alembic revision 或 Celery task name。

## 当前状态与变更前基线

- 文档把 ChromaDB 列为完整栈或可选增强，并描述 NetworkX、SQL 图、本地对象
  存储及最小 SQLite 启动模式；
- 部分任务调度允许 broker 不可用时在 API 线程继续执行；
- 图查询和分析可能在 Neo4j 不可用或无数据时读取其他后端；
- 对象存储与数据导入可能把新对象写入本地兼容路径；
- 语义搜索的失败行为没有作为稳定版公开契约统一说明；
- LLM 与平台启动配置的先后关系不清晰。

## 变更范围

| 模块/路径 | 改动 | API/menu key/数据库/Celery/环境变量影响 |
|---|---|---|
| 启动、健康与配置 | 必需依赖配置和真实 readiness fail closed | liveness 不变；深度 readiness 收紧；不改 menu key/数据库 |
| Redis/Celery | worker 成为必需进程，移除异步派发失败后的 API 线程兜底 | task name 不变；显式同步 API 不改语义；dispatch 失败显式化 |
| 生产依赖清单 | Redis URL 成为客户端单一权威；移除未消费的 PostgreSQL host/port 与 MinIO console 字段 | 精确旧内置 Redis URL 在生成态原子升级；旧冗余字段只读忽略 |
| Neo4j/本体投影 | 移除 NetworkX、Formal/SQL 图读取兜底；新增 project durable fence、稳定 ID 和升级全量对账 | 图失败/半投影返回 503；空图仍是成功空结果 |
| MinIO/文件资产 | 新对象只写环境 MinIO；回归时代数据库托管端点仅供存量读取/删除/列举迁移 | 历史 `local://` 仅保留只读/迁移 |
| n8n/Chromium CDP | n8n 纳入阻塞型启动依赖；CDP 地址配置必需且纳入深度 readiness，但连通失败不终止 API | 工作流/CDP 路径不变；n8n PUT 收紧为 409，设置页改只读 |
| 搜索 | 删除 ChromaDB；keyword 使用 PostgreSQL | semantic 端点和统一 semantic 模式返回 501 |
| LLM | 启动后由模型配置页按需添加 | 模型 API/menu key 不变；不创建本地默认模型 |
| 文档与脚本台账 | 同步入口、架构、开发、运维、参考和示例 | 不包含真实生产配置值 |

## 兼容策略

- `GET .../search/keyword` 与统一搜索 `mode=keyword` 保持可用，返回结果继续标明
  PostgreSQL backend；
- 语义端点保留 HTTP 路径，但以
  `501 semantic_search_unsupported` 明确退役，不隐式改成关键词结果；
- Neo4j 有效空图继续返回空集合；只有不可用、查询失败或投影未就绪才返回
  明确错误；
- API Hub SQLite、测试 SQLite、确定性演示中的临时 SQLite 有独立生命周期；
- 非测试 n8n 客户端、配置读取和连接测试只使用 `N8N_*` 启动值；原配置路径
  保留，但 PUT 明确返回 409，设置页不再产生数据库覆盖值；
- `ENVIRONMENT=test` 保留数据库 n8n 注入，避免移除确定性测试能力；
- 历史 `local://` URI 只用于读取和迁移，完成迁移后应转换为 MinIO URI；
- 旧迭代与归档继续保留当时证据，不作为当前运行契约。
- 生产内置 Redis 从 `REDIS_URL` 推导同一份 `requirepass`；精确旧无认证 URL
  复用已有强密码或生成新密码后，同时改写服务端密码和客户端 URL。外部 Redis
  URL 不改写，其提供商凭据不绑定到随栈启动但未被客户端使用的内置实例；
- 旧清单中的 `POSTGRES_HOST`、`POSTGRES_PORT`、`MINIO_CONSOLE_URL` 继续被部署
  入口接受但仅记录为 deprecated 并忽略，避免破坏受保护旧清单的部署兼容。

## 迁移方案

1. 在不输出值的前提下盘点目标环境的必需依赖配置项与负责人；
2. 备份 PostgreSQL、Neo4j、MinIO、API Hub 数据目录、n8n workflow 元数据及
   服务器配置；
3. 先部署并验证 PostgreSQL、Redis/worker、Neo4j、MinIO 和 n8n，并配置
   Chromium CDP；CDP 暂时不可达时只允许启动 API 做存活诊断，在 CDP 恢复、
   深度 readiness 通过前不接入业务流量；
4. 扫描历史 `local://` 引用及 DatasetVersion、FileAsset、Media、FileConnector
   等回归时代数据库托管 MinIO 引用，使用受控迁移工具复制到环境 MinIO 并完成
   checksum/可读性对账；迁移完成前保留只读/删除/列举入口；
5. 删除目标环境中不再消费的 Chroma 配置，不启动 Chroma 服务；
6. 验证关键词搜索与 semantic 501、图投影、文件读写、任务入队、n8n workflow
   和浏览器会话；
7. 基础平台 ready 后，再由管理员在模型配置页添加所需 LLM 并执行独立验收。

Alembic `0055_ontology_projection_fence` 会把升级前的本体标记为
`repair_required`；API 启动在 Neo4j 索引完成后同步重建所有非 ready
项目，全部校验通过才启动 worker/对外服务。必须在新库和存量库
副本上分别验证该过程，不允许手工把围栏改成 ready。

## 安全与数据处理

- 迁移与验证日志只记录依赖名称、状态、耗时和脱敏错误，不记录密码、token、
  API key、Cookie、连接串或对象内容；
- 不读取、复制或引用真实生产依赖清单内容；
- `local://` 迁移使用备份副本或隔离窗口，写 MinIO 成功并对账前不删除源文件；
- n8n、MinIO 和 LLM 凭据按各自边界独立轮换，不写入迭代文档或 artifact。
- n8n API Key 只以 `has_api_key` 脱敏状态展示，连接测试不接收 UI 替代密钥；
  修改配置后重启 API/worker，避免多进程持有不同权威值。

## 验收条件

- PostgreSQL、Redis/Celery worker、Neo4j、MinIO 或 n8n 缺失/未就绪时，配置
  生成、API 启动或平台 ready 明确失败；CDP 地址缺失拒绝启动，只有 CDP 连通
  失败时允许 API 保持 liveness，但深度 readiness 固定返回 503；
- API 与 worker 都不会在依赖失败时进入静默替代执行路径；
- Chroma 包、服务、配置字段和 active 文档引用全部清理；
- keyword 两个入口使用 PostgreSQL，semantic 两个入口稳定返回 501；
- Neo4j 不可用/查询失败/投影未就绪返回 503，有效空图不读取 SQL/Formal；
- 新对象只写 MinIO，历史 `local://` 可读且有迁移证据；
- API Hub/test SQLite 不受影响；
- 非测试环境无法经 workflow-config PUT/测试接口覆盖 n8n 启动配置，远端工作流
  启用状态读取失败时编排 fail closed；
- 无 LLM 配置时基础平台可 ready，配置后对应 LLM 旅程通过。

## 验证证据

| 层级 | 实际命令/环境 | 退出结果 | CI URL / artifact / 跳过原因 |
|---|---|---|---|
| 文档 | `node scripts/ci/check-markdown-links.mjs`；`bash scripts/ci/check-repository-hygiene.sh`；`git diff --check` | 90 个 Markdown/498 个链接零错误零警告；仓库卫生通过；diff check 通过 | 本地工作树 |
| 后端定向 | 图/Mapping/正式模型、基础设施/部署/健康/迁移、架构边界、投影冲突回归、旧迁移脚本相关 pytest | 分组分别为 108、99、30、29、10 passed；分组之间存在重叠，不合计为总用例数 | 本地工作树 |
| 配置中心 | `cd config && uv run pytest -q` | 41 passed | 本地工作树 |
| 后端完整 | `cd backend && uv run pytest -q --disable-warnings --ignore tests/v2/perf` | 1811 passed，1 skipped | 本地工作树 |
| 前端静态/mocked | unit、feature boundary、classification、lint、build、mocked E2E | unit 12 passed；classification 50 个 spec 唯一归类；boundary、lint、build 通过；mocked E2E 81 passed | 本地工作树 |
| 隔离真实依赖栈 | 临时 PostgreSQL 16、Redis、Neo4j、MinIO、n8n、Chromium CDP 与真实 Celery worker | 新库迁移和 0054→0055 存量升级通过；全部真实探针、Celery PONG、MinIO CRUD、SQL→Neo4j 重建和完整 lifespan ready=200 通过 | 本地隔离环境；短期凭据未落盘，临时容器已全部清理 |
| 故障注入 | 完整 lifespan 下分别中断 CDP、配置不可达 MinIO | CDP 不可达时 live=200、ready=503；MinIO 不可达时 API 启动非零失败 | 本地隔离环境 |
| 搜索/LLM | keyword/semantic 定向契约；LLM 启动边界 | keyword 使用 PostgreSQL、semantic 返回 501 的定向测试通过；未执行真实付费 LLM external E2E，且它不属于启动门禁 | LLM 旅程在管理员配置提供商后独立验收 |
| 部署/回滚 | 部署脚本语法、配置权威/白名单守卫；生产 canary 与回滚 | 脚本语法和部署守卫通过；未执行生产 canary、真实生产部署或生产回滚演练 | 发布前由运维在受控环境补充 artifact |

## 上线步骤、监控指标与观察窗口

按迁移方案先依赖后应用部署。至少观察一个完整发布/Mapping/Sentinel 周期和一
次 worker 重启窗口，监控 PostgreSQL/Redis/Neo4j/MinIO 连接、Celery active/
queued/failed、n8n 调用、CDP 会话、503/501 分布、对象写入 URI，以及是否出现
SQLite、线程任务、NetworkX/SQL 图或新 `local://` 写入迹象。

## 回滚触发条件与逐步方案

触发条件包括：主数据不可读、任务丢失、Neo4j 投影无法对账、对象写入丢失、
n8n/CDP 持续不可用或深度 readiness 误判。发生后：

1. 停止发布和写入，保存脱敏状态及失败任务 ID；
2. 回滚到最后一个已验证的完整应用/Compose/配置契约，不单独重新开启某个旧
   fallback；
3. 恢复与目标版本匹配的数据库、对象、n8n 元数据和依赖配置；若目标版本仍
   需要已移除服务，必须由维护者显式批准整套兼容回滚，不能临时混用；
4. 重新验证七项依赖、worker、关键词/semantic 契约、图投影和对象读写；
5. 对 `local://` 迁移采用“源保留、目标重建”回滚，不删除尚未对账的源对象。

## 已知风险与后续动作

- 隔离本地真实服务栈与 CDP/MinIO 故障注入已完成；生产 canary、真实生产部署
  和生产回滚演练仍是发布前运维动作，需要补充受控 artifact；
- 历史 `local://` 数量、容量和迁移窗口需由运维盘点；
- 旧部署环境可能残留不再使用的 Chroma 配置，清理前需证明无旧版本回滚需求；
- 当前实现与本地完整门禁已达到 `Validated`；生产发布及其 canary/回滚证据完成
  后再改为 `Released`。
