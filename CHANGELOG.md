# Changelog

本项目按迭代记录维护详细工程证据，本文件仅保留面向发布的结果摘要。

## Unreleased

### Security

- 明确现有生产依赖清单是仓库所有者批准的临时兼容例外；本轮不改变自动部署
  的配置来源，后续秘密迁移作为独立运维变更处理。
- 将个人启动配置替换为无密钥、无绝对路径的示例。

### Documentation

- 建立版本化开发准则、需求/ADR/迭代索引和按目录下钻的 README 体系。
- 增加从导航、React 路由、后端权限和 RBAC 测试交叉核验的平台契约基线。
- 依据当前源码重建核心数据生产、本体版本发布、发布后刷新、Sentinel/Action
  与 Event Registry 的业务流程及实现索引。

### Changed

- 平台概览改由应用直接装配 canonical router，并把测试归入
  `backend/tests/platform/`；旧 import facade 继续保留。
- FastAPI 健康检查、生命周期和数据库 seed 从 `main.py` 收口到
  `app/bootstrap/`；API Hub、datasets、pipelines、Formal、Mapping、
  Sentinel、versions、Agent Runtime、Super Assistant、Exploration、
  Event Registry、模型配置和 web search 按稳定职责拆入 canonical service，
  HTTP router 保留鉴权、协议适配和兼容注入。
- Pipeline A/B/C 纯执行与同步触发分别进入 `route_executor.py` 和
  `trigger_service.py`，旧 `engine.py` 保持兼容 facade；Settings 的规则、
  QwenPaw Agent 配置和 n8n 工作流配置分别由三个同域 service 承接。
- Data Steward、Super Assistant、Agent Workbench 与 Settings 在现有页面
  业务域内拆成“页面编排 + 同域组件”，没有改变路由、menu key 或公开 API。
- 复盘修正 Data Steward 附件清空时机、Tailwind 全局 token、动态 Sentinel
  发布上下文读取和报告模板查询，使其恢复为整理前的交互与执行语义。
- 后端测试按业务域归档，`tests/v2/` 继续作为明确的 API/runtime v2 契约族。
- 前端 49 个 Playwright spec 明确分为 mocked、stack、external 三组。
- 手工浏览器脚本的资源定位和运行证据统一指向仓库根与 `.artifacts/`。

### Infrastructure

- 建立 PR 级文档、仓库卫生、后端、配置中心和前端验证门禁，并在自动部署前
  重复执行。
- 前端源码门禁增加递归 feature/page-domain 边界检查；后端增加 bootstrap、
  dataset、pipeline、formal、versions 及复杂聚合路由的自动依赖方向守卫。
- 部署目录在任何远端删除/解包前经过统一安全校验；镜像摘要严格模式从最终
  运行配置读取，并允许显式进程环境覆盖。
- 部署上传包改由受测试的运行时白名单生成，排除文档、测试、fixture、E2E
  源码和过程产物。
- 自动部署继续使用现有生产依赖清单与 Repository SSH Secrets，不要求本轮
  额外迁移 GitHub Environment；新增配置来源回归守卫。
- 前端、后端、Celery 和浏览器生产镜像使用清理后的构建上下文。

### Removed

- 移除已确认无运行时消费者的截图、HTML/JSON 测试结果等过程产物。
- 前端依赖管理统一为 npm，移除未被构建与 CI 使用的 Bun/pnpm 锁文件。
