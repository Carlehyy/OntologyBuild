# 仓库目录与交付治理

| 字段 | 内容 |
|---|---|
| 状态 | Validated |
| 日期 | 2026-07-30 |
| 负责人 | Repository maintainers |
| 评审人 | 待指定 |
| Issue/PR | 当前治理分支 |
| 业务域 | 全仓库 |

## 背景

多人开发导致根目录、前后端模块、测试、过程产物、文档和部署配置缺少统一
边界。后端已经迁移到业务域一半，但仍有大量旧 facade；前端导航、路由、
权限、API 和测试尚未形成统一 feature 边界。

审计还发现生产依赖清单和个人启动配置进入 Git。个人启动配置已替换为示例；
仓库所有者明确要求在持续开发期间暂时保留现有生产依赖清单，后续迁移和历史
处理作为独立运维事项，不混入本次目录治理。

## 目标

- 保持现有生产依赖来源和 push 自动部署前置条件不变，同时建立后续迁移模板；
- 建立 `AGENTS.md`、文档索引和迭代记录；
- 建立 PR 级测试门禁；
- 将过程产物移出版本库；
- 按现有业务域完成后端 canonical import 收敛；
- 按导航能力完成前端 feature 收敛；
- 保证自动部署可验证、可回滚。

## 非目标

- 不在纯目录迁移中改变 API、menu key、数据库表或 UI 行为；
- 不一次性移动 `backend/`、`frontend/` 根目录；
- 不把逻辑重构和文件移动混成一个提交；
- 不在本迭代中直接执行未经团队协调的 Git 历史重写。

## 当前阶段

1. 已完成只读结构、安全、测试和部署审计；
2. 已恢复现有生产依赖清单作为自动部署输入，并建立临时例外和后续迁移边界；
3. 已建立 PR CI、Python 3.12/uv 锁文件门禁和前端 E2E 显式分组；
4. 已清理明确的过程产物、零引用源码/资源和无消费者手工脚本；正式 fixture
   保留；
5. 已完成后端 `platform/overview`、`settings/prompts`、`model_configs` 和
   前端 `features/overview` 的兼容迁移波次；
6. 已将后端根级真实 pytest 按业务域收口，并完成 bootstrap、foundation、
   API Hub、datasets、pipelines、Formal、versions、Sentinel、Agent Runtime、
   Super Assistant、Exploration、Event Registry、model configs、settings/rules
   与 web search 的职责拆层；
7. 已完成 Data Steward、Super Assistant、Agent Workbench 和 Settings 的
   同域页面/组件拆分；除 overview 外尚未宣称其他页面已迁入 canonical
   feature；
8. 已从源码、API 和测试重建核心业务文档，明确数据生产、定义发布、发布后
   刷新与独立 Event Registry 的真实边界。
9. 已从空卷重建最终生产镜像和 9 服务隔离栈，完成迁移、健康、Celery 与
   52 项真实栈浏览器验收；远程 staging/production 尚未触发，因此不是
   Released。

## 已落地结构

- 根目录、backend/app/tests、frontend/src/tests/scripts、config、docs、scripts、
  test_data、docker 和 workflows 均有可下钻 README；
- 需求、ADR、迭代、运维、参考和归档文档分区建立；导航/RBAC 与核心
  data→ontology→runtime 契约均逐项链接到源码和可执行测试；
- 261 个已确认生成物（14,036,522 bytes）从当前版本树移除；正式 fixture
  保留；
- 前端依赖管理统一消费 npm lock；浏览器证据统一进入 `.artifacts/`；
- 平台概览实现原样收敛到 `frontend/src/features/overview/`，旧页面路径仅
  re-export；App 使用 canonical import，依赖方向由自动边界守卫约束；
- 53 个根级 pytest 模块按 `auth/api_hub/data_channel/events/exploration/inbox/
  infra/migrations/model_configs/ontologies/settings` 归档，根测试目录不再堆放
  业务测试；
- 4 个不会提供 pytest case 的历史可执行脚本已删除，其风险和正式替代入口
  记录于 [`docs/archive/legacy-test-scripts.md`](../../archive/legacy-test-scripts.md)；
- 文档链接/索引和仓库卫生守卫同时进入 PR CI 与自动部署 verify。

## 兼容策略

- 部署脚本继续消费仓库所有者批准暂时跟踪的
  `production.dependencies.env`，不要求新增 GitHub Environment；
- 后端旧 import facade 保留到内部和测试零引用；
- 前端 `pages/overview/OverviewPage.tsx` 暂留为兼容 re-export，删除条件见
  ADR-0002；
- 现有 URL、menu key、Celery task、Alembic revision 和环境变量名保持不变。

## 前端第一阶段模块收口

- `OverviewPage.tsx`、`cockpit.tsx/css` 和 `flowengine.tsx` 原样移动到
  `features/overview/`，没有调整视觉或业务计算；
- `features/overview/index.ts` 是公开入口，目录 README 记录路由、menu key、
  API、测试和依赖规则；
- 新增边界守卫，禁止 overview 反向依赖 `pages/` 或其他 feature，并检查
  旧 facade 不重新承载实现、App 不回退到旧导入；
- 新增离线浏览器契约用例，覆盖后端统计渲染、`/#/overview` 深链刷新、
  custom 用户菜单/直达 URL 授权、空权限 `/no-access` 状态以及本体建模、
  待办审批跳转。

## 严格冗余清理

- 删除 12 个经静态/动态导入、路由和 E2E 多方审计均为零引用的旧页面实现：
  161,393 bytes、3,558 行；
- 删除 `frontend/scripts/` 中 46 个无 package、CI、文档或源码消费者的历史
  手工脚本：312,613 bytes、6,951 行；目录现在只保留 README 和两个守卫；
- 删除 6 个零引用图片/SVG：37,608 bytes；根 `assets/` 不再作为项目目录；
- 上述 64 个实现、脚本和资源合计移除 511,614 bytes，其中可计数源码/脚本
  10,509 行；另删除已经失去内容的 `assets/README.md`；
- `/rag` 仍由 App 重定向到 `/agent`；不存在的 `/register` 仍由认证测试验证
  回到登录页，不因删除旧组件改变公开行为；
- 历史手工场景只保留意图、风险和正式替代映射，不复制不可审计脚本代码。
- `test_data/` 删除 29 个非正式历史 Python 流程/生成脚本，以及
  `HR/api/db/documents/frontend/prompts` 六组无当前消费者的孤立 fixture；
- 保留供应链、医疗、教育、法律、营销、财务六个有明确消费者的业务域 fixture
  和 `snomed_mental_health.csv`；`test_data/README.md` 现在是 fixture-only
  契约；
- 删除 `backend/scripts/dev/` 中 6 个硬编码旧 ID 的一次性脚本；仓库卫生守卫
  禁止这些旧路径和在 `test_data/` 新增执行脚本；
- 正式替代入口是 `backend/tests/`、49 个分类 Playwright spec 和
  `scripts/data/` 的受控脚本；已删除内容可从 Git 历史恢复。
- 第二轮从 `frontend/src/main.tsx` 构建静态/动态 import 可达图，并以全仓
  路径、符号、测试和文档搜索复核：保留两行 overview 兼容 facade，删除
  19 个零消费者 API wrapper、组件、barrel 和 store，共 71,215 bytes、
  1,972 行；受影响的两个空目录也不再保留；
- 删除 `backend/scripts/demos/seed_financial_risk.py` 和
  `scripts/data/seed_graph.py` 两个有明确陈旧/不安全证据且零消费者的脚本，
  共 25,227 bytes、426 行；第二轮合计删除 21 个文件、96,442 bytes、
  2,398 行；
- `backend/scripts/README.md` 逐项登记其余迁移、维护、演示和 live E2E 的命令、
  依赖、写入范围与清理方式；maintenance/live 脚本不再因“代码零引用”被误判为
  可删除文件，两个已删除入口的证据和正式替代写入归档台账；
- 全部 21 个 stack spec 与 2 个 external spec 统一从
  `support/stack-credentials.ts` 读取隔离环境凭据；classification 守卫会阻止
  真实栈用例重新内嵌默认账号，mocked 用例的显式假凭据不受影响。
- `scripts/data/README.md` 依据 5 个现存入口逐项登记实际参数、依赖、数据写入、
  外部费用与清理边界；其中会先删除同名业务数据的供应链全流程脚本被明确限制
  为仅可在可整体销毁的隔离环境运行。

## 核心业务文档现状化

- 新增
  [`0002-core-data-ontology-runtime-contract.md`](../../product/requirements/0002-core-data-ontology-runtime-contract.md)，
  固化数据生产、Draft/Trial/Promote、发布后 refresh、权限、幂等、release
  fence 与 rollback；
- `data-flow.md` 从 30 行扩展为 203 行，分别画出三条当前链路，并把
  RegisteredEvent 标为没有自动 Runtime 接线证据的独立能力；
- `ontology.md` 从 2,727 行压缩为 255 行，删除过时 MongoDB/TuGraph/医疗通用
  设计，只保留 PostgreSQL `fo_*`、immutable release、Mapping/Fact lineage 与
  可重建查询投影；
- `sentinel-engine.md` 从 280 行重写为 262 行，纠正 built-in/dynamic 边界、
  Object/Link/control outbox、触发模式、HITL 和真实 Webhook 行为；
- 上述三份旧 active reference 合计从 3,037 行降为 720 行；加入 286 行可执行
  核心契约后合计 1,006 行，比原 active reference 少 2,031 行；
- 根 README 和各层索引统一为“产品概览 → 核心数据流/契约 → 导航/模块地图 →
  开发/部署”，并明确三套 Compose 各自用途。

## 后端第二阶段模块收口

- `settings/prompts` 将 8 份内置模板从 391 行 router 中原样提取到
  `templates.py`，启动 seed 和 API 共享同一 canonical 常量；
- `model_configs` 的 router、models、schemas、selector 已在包内自闭合；
  配置生命周期、连通性、调用统计和安全序列化进一步分入四个同域模块，
  `main.py` 直接装配 canonical router；
- 旧 router/model/schema facade、`app.services.llm_service` 和
  `app.services.model_config_selector` patch 路径均保留；
- 原根级测试移入 `tests/settings/`、`tests/model_configs/`，新增 facade
  对象身份、OpenAPI operation/tag、模板 checksum 和 scoped import 边界契约。

## 后端组合根与核心服务拆层

- `app/main.py` 的健康探测、启动/关闭顺序和数据库 seed 分别进入
  `app/bootstrap/health.py`、`lifecycle.py`、`seeding.py`；旧
  `_seed_db`、`_probe_http_service`、`urllib` patch 身份继续可用，
  composition root 由 937 行降至 372 行；
- auth 的模型/schema/service 使用 canonical import；共享依赖和外部探测保持
  foundation 方向，探索与数据管家共用的搜索实现收口到
  `app/shared/web_search.py`；
- API Hub 的接口契约与 CRUD/发布校验进入 `interface_contracts.py` 和
  `interface_service.py`；datasets 的手工契约、读、写、编辑和消费者解析进入
  `manual_contract.py`、`query_service.py`、`mutation_service.py`、
  `edit_service.py`、`consumers.py`；
- pipelines 的请求契约、依赖引用、执行/dry-run、校验/发布门分别进入
  `contracts.py`、`dependency_service.py`、`execution_service.py`、
  `validation_service.py`、`management_service.py`；A/B/C 纯转换与同步链式
  触发分别进入 `route_executor.py`、`trigger_service.py`，router 的 19 个
  handler 已无业务分支、ORM 或事务；
- Super Assistant 的会话、Skill 与 MCP/MinIO 管理分别进入三个同域 service；
  Exploration 的 session、attachment、streaming、document、draft、apply
  workflow 进入六个同域 service；Event Registry 的查询、附件归档和 ingest
  进入三个同域 service；
- 模型提供商调用进入 `model_configs/llm_gateway.py`，发布门使用的 Sentinel
  定义校验进入 `ontologies/sentinels/validation.py`；
- Formal router 的 schema authoring、实例、Action/HITL、dashboard 查询和
  运行时帮助函数分别进入 `schema_authoring_service.py`、
  `instance_service.py`、`action_workflow_service.py`、
  `dashboard_queries.py`、`runtime_support.py`；
- versions 的 release、workspace、trial、运行态冲突/readiness、promotion、
  rollback 端点事务分别进入 canonical service；`versions/router.py` 由
  4,160 行降至 617 行；release gate、查询投影激活、activation number 和动态
  Sentinel 失效规则已进入对应 service，router 只保留端点、只读详情/退役入口
  和兼容注入 wrapper；
- Agent Runtime 的 profile、图查询、动态 Sentinel、chat、会话、报告与提案
  进入八个同域模块；31 个 handler 保持原签名/decorator，router 不再持有业务
  事务；
- `settings/rules` 的规则读写、QwenPaw Agent 配置/连通性和 n8n 工作流
  配置/连通性分别进入 `rules_service.py`、`agent_config_service.py`、
  `workflow_config_service.py`；9 个 handler 均为单一委派，router 由 412 行
  降至 184 行且不再直接执行 ORM、事务、网络或加解密逻辑；
- `app/routers/settings.py` 聚合入口、旧 model/schema/helper 对象和请求时
  encryption/httpx/n8n/settings patch seam 均保留；architecture 契约固定
  7 paths/9 operations、管理员边界与 service 不反向依赖 router；
- 以上拆层没有借机修改 HTTP、RBAC、数据库、Celery 或 release 语义。
  architecture suite 固定组合根、依赖方向、单一委派、事务所有权、完整应用
  SCC 以及旧 monkeypatch/facade 对象身份。

## 前端复杂页面拆层

- `DataStewardPage.tsx` 从 1,194 行降至 587 行，保留页面状态、请求编排和主
  布局；会话时间线、composer、workspace、浏览器协作和受管流水线进入同域
  `components/`，时间线/筛选/错误与字节格式纯逻辑进入 `stewardModel.ts`；
- `SuperAssistantPage.tsx` 保留页面状态、请求编排和顶层布局；消息/Markdown/
  工具轨迹/确认卡与 Skill/MCP 配置分别进入两个同域组件；
- `AgentWorkbenchPage.tsx` 从 1,840 行降至 714 行，保留 React Query、API、
  流式状态和顶层 workbench；消息/步骤/溯源/导出/分栏与本体网络分别进入
  `AgentWorkbenchPresentation.tsx`、`OntologyNetworkView.tsx`；
- Agent 其他卡片、drawer、图表和惰性视图仍在 `pages/agent/`，没有误报为
  全部迁入 `components/`；上述页面拆分仍属于既有 `pages/<domain>/`，不是
  未获批的 `features/` 迁移；
- `SettingsPage.tsx` 收敛为 67 行，只负责 URL/tab 解析、固定顺序调用五个
  能力 hook 和视图选择；五个 `hooks/use*Settings.ts` 承载状态、React
  Query/API、副作用和动作，五个 `tabs/*SettingsTab.tsx` 只接收显式
  view-model props；原文件 1,244 行的 13 个状态/API/effect 区块和 5 个完整
  JSX 区块经机械核对 18/18 与拆分后原文匹配；
- `palantir-graph/components/Panel.tsx` 从 1,165 行收敛为 73 行 dispatcher；
  Object、Link、Action、Function 编辑器和共享 panel shell 进入同域
  `components/editors/`，原函数体、Store 调用、保存/删除逻辑、DOM、样式和
  文案保持一致；
- feature boundary 守卫递归检查页面目录，上述拆分均未产生兄弟 page domain
  import 或循环依赖。

## 部署与构建配置收口

- 新增唯一的 `validate-deploy-app-dir.sh` 校验入口；GitHub Actions 在任何
  SSH 命令前调用，服务器部署脚本在任何 `mkdir`、`rm` 或 `cd` 前再次调用。
  默认 `/opt/ontologybuild` 保持可用，根目录、顶层目录、非绝对/未规范化
  路径、引号、控制字符和 shell 标点均失败关闭；
- `STRICT_IMAGE_DIGESTS` 现在从合并依赖 manifest 后的最终 `.env` 读取；只有
  显式进程环境才覆盖它。自测证明 `.env=true` 会拒绝浮动镜像，显式
  `STRICT_IMAGE_DIGESTS=0` 可按优先级覆盖；
- 部署守卫自测同时进入 PR CI 和自动部署 verify，避免工作流复制另一套判断；
- `create-deployment-archive.sh` 改为显式运行时白名单，只包含生产 Compose、
  前后端构建输入、Alembic、容器资源、部署入口和受控 maintenance 脚本；
  `test-deploy-guards.sh` 同时断言必需成员存在，并拒绝 `docs/`、测试、fixture
  与 `.artifacts/` 进入上传包；自动部署已改用这一受测试入口；
- 删除与完整 TypeScript 配置并存的旧 `frontend/tailwind.config.js`，唯一事实
  源为 `tailwind.config.ts`，且复盘后已把有效 token 恢复为旧 JS 配置语义；
  同时移除 Compose v2 已废弃且无行为作用的 `version` 字段。

## 行为复盘修正

- 对 HEAD 与当前实现执行路由、OpenAPI、RBAC、Celery、Alembic、前端文案、
  API 调用和 DOM 签名差分后，确认核心用户入口与业务流程保持不变；
- 删除 Agent Runtime 动态 Sentinel 启停接口中重复的发布上下文读取，并增加
  单次调用回归守卫；
- 删除报告模板发布查询中重复的相同 `template_id` 条件；
- 恢复 Data Steward 文件选择框原有清空时机，不把失败重试体验变化混入目录
  整理；
- 恢复 Tailwind 原有字号、圆角和字体配置语义，避免目录治理引入全局视觉变化；
- 取消自动部署对新建 GitHub `production` Environment 的强制依赖，继续使用
  既有生产依赖清单和 Repository SSH Secrets；部署目录校验、SSH host key
  校验、完整 verify 与运行时白名单等安全增强继续保留。
- 首轮 clean runner 回归暴露 API Hub 权限边界测试仍直接创建平台
  `TestClient`，因而绕过标准测试数据库 fixture、意外依赖默认 SQLite 已建表
  状态；现已复用统一 `client` fixture，消除测试文件归档后显现的执行顺序依赖，
  未改动生产权限逻辑。
- 次轮远端回归暴露资产湖 mocked E2E 把 UTC 时间在东八区的显示结果
  `14:35` 写死；页面按浏览器本地时区显示的既有语义正确，测试现改为从同一
  UTC 原值在浏览器上下文计算期望文本，并用原始 ISO `title` 精确定位单元格，
  在 UTC 与 Asia/Shanghai 环境均可验证而不改变用户时间展示。

## 后端测试目录收口

- 本轮只改变测试文件位置；49 个模块内容原样移动，另 4 个模块仅机械调整
  `Path(__file__).parents[...]` 的目录层级；
- 移动前后完整 collect-only 均为 1475 cases，测试函数、fixture、patch 目标
  和生产源码均未改动；
- `test_core_features.py` 等 4 个根级文件不是 pytest 测试，分别存在导入时
  清库/退出、固定端口和账号、真实外部数据或当前数据库写入风险，已按审计
  结论删除并保留归档台账；
- `tests/v2/` 明确为 API/runtime v2 兼容契约族，根目录不再堆放测试；原有 7
  个散落模块按职责移入 `datasets/curated/logic_actions/migrations/`，5 个
  文件 SHA-256 原样一致，2 个文件仅机械调整 `__file__` 父目录层级；
- 这 7 个模块移动前后均收集 37 cases，移动后定向执行 37 passed；完整收集数
  受并行生产代码和测试改动影响，必须在共享工作树稳定后由最终回归重新记录。

## 验证证据

| 层级 | 命令/环境 | 当前结果 |
|---|---|---|
| 后端完整回归 | 修复后、标准 CI 默认环境执行 `pytest -q --disable-warnings --ignore tests/v2/perf` | 1694 passed、1 个显式 live MinIO 用例 skipped、0 failed；新增 2 个行为复盘守卫 |
| API Hub 空库隔离 | 全新临时 SQLite 执行失败用例及 `tests/api_hub` | 失败用例 1 passed；完整域 39 passed；不依赖默认 `/tmp/ontoprompt.db` |
| 后端性能门禁 | `pytest -q tests/v2/perf` | 9 passed |
| 后端依赖边界 | `pytest -q tests/architecture` 与全 `backend/app` AST 图 | 186 passed；557 个模块/1856 条本地边；service→router、canonical router→router 均为 0；仅保留已登记的 Sentinel SCC |
| 生产配置与配置中心 | backend production config 专项；`pytest config/` | 46 passed；36 passed |
| OpenAPI | 删除不稳定 `operationId` 后比较 schema | 418 paths、550 operations、217 schemas；SHA-256 `393c06b353ba7145f3dfae3f26776c5c81bc6b89e5520aa8784c81f7d1d49b0b` |
| Alembic | 临时空 SQLite 与空 PostgreSQL 分别 `upgrade head/current/heads` | 两种数据库均完整升级到唯一 head `0054_fact_lineage_indexes` |
| Celery | 最终 worker `inspect ping` 与应用任务集合断言 | worker pong；6 个稳定应用任务名精确一致 |
| 前端静态与单元 | `test:unit`、`lint`、`build` | 12 passed；lint/build 通过；5372 个模块完成生产构建，仅保留既存 chunk 体积提示 |
| 前端模块边界 | `check:feature-boundaries` | 4 个 overview 源文件、111 个页面文件/15 个页面域、221 个生产模块；0 orphan、0 cycle |
| 前端 E2E 分层 | classification、mocked、最终 strict stack | 49 个 spec 精确归入 26 mocked + 21 stack + 2 external；时区修复后 `TZ=UTC CI=1` 完整 mocked 80 passed，Asia/Shanghai 目标用例 1 passed；stack 52 passed、3 个付费外部 LLM 用例 skipped |
| Compose 与镜像 | 三套 `config --quiet`；最终 backend/celery/frontend/browser 及开发前端镜像构建 | 全部通过；严格项目从空卷启动 9 个服务且全部满足 Compose 等待条件 |
| 生产栈冒烟 | `127.0.0.1:18088` 首页、JS/CSS、`/api/health`、PostgreSQL 初始化日志 | HTTP 200；`status=ok`、`unavailable=0`；数据库日志 0 个 ERROR/FATAL/PANIC |
| 部署守卫 | 配置只读校验、`test-deploy-guards.sh`、仓库与解包后两次生产 Compose 构建 | 现有 26 项生产依赖成功合并且 `PUBLIC_PORT=8088`；配置来源契约、运行时白名单、目录正反例和严格镜像模式通过；backend/celery/frontend/browser 镜像从仓库及最终上传包均构建成功 |
| 文档与仓库卫生 | Markdown 自测/链接；`check-repository-hygiene.sh` | 87 份 Markdown/488 个链接 0 error、0 warning；1330 个文件中无过程产物、非包零字节文件或非空精确重复 |
| 编译与格式 | Python `compileall`、`git diff --check` | 通过 |

上述结果证明当前工作树可在本地严格生产配置下构建、迁移、启动并完成核心真实
业务流程，因此状态为 `Validated`。首轮远端 Actions
（run `30541030291`）在 clean runner 暴露上述数据库隔离问题；次轮
（run `30546579509`）的后端、迁移、配置、文档、前端单元、lint 与 build
均通过，随后由上述时区断言停止（mocked E2E 79 passed、1 failed）。两轮
deploy 均未执行、生产环境未变更；远端发布状态只以修复提交对应 Actions
的终态为准，本地证据不单独标记为 `Released`。

## 回滚

第一阶段文件和已删除过程产物均可从 Git 通过单独 revert 恢复。前两轮远端
验证均未进入 deploy；如后续部署配置校验失败，应先核对当前版本的生产依赖
清单和既有 Repository SSH Secrets，不得在日志中输出真实值。

## 已知风险与后续动作

- 已进入 Git 历史的生产配置按仓库所有者决定暂时保留；后续迁移、轮换与历史
  处理作为独立运维变更，本迭代不改写历史；
- 当前部署仍会在服务器重新构建且缺少自动回滚；
- 前端 stack/external 真实 E2E 尚未进入自动 CI；
- 本地最终镜像和空库已验证，但未运行远程 staging/production、现存
  PostgreSQL 副本升级、2 个 external spec 或 3 个付费 LLM 场景；
- 前端生产依赖审计仍报告 6 条告警（1 low、1 moderate、4 high），其中
  `xlsx` 没有上游修复版本；本轮未把依赖替换混入目录治理；
- FastAPI 仍报告 2 个 API Hub proxy 重复 operation ID；本轮比较 OpenAPI 时
  移除不稳定 `operationId`，该冲突需独立修复；
- 现有前端主 bundle 和 Python 依赖仍有构建/弃用告警，需独立治理。
