# 数据通道

本目录负责“外部数据 → 不可变数据版本 → 流水线 → 成品审核”的生产链路。业务
状态和数据库事务在本目录及 `app/tasks/v2/`，HTTP 装配仍以
[`app/main.py`](../main.py) 为准。

```text
data_channel/
├── access.py          数据集/成品跨目录访问守卫
├── connections/       SQL、REST、Mongo、文件与 AI-HOT 连接/采集适配
├── datasets/          数据集目录、版本、导入、编辑、共享与版本事件
├── pipelines/         定义、DAG、依赖、校验/发布、执行与管理；采集引擎注册表
│                      （engine_registry.py：canvas/n8n/python）与外部引擎共用
│                      入湖骨架（external_runner.py），python_engine/ 为 Jupyter
│                      Kernel Gateway 脚本引擎（client/service/runner）
├── pipeline_tasks/    调度任务契约、候选/统计/历史、CRUD、触发与入湖执行
├── curated/           成品目录、版本读取、审核、导出与安全删除
├── sync_tasks/        定时/增量同步、调度和版本事件消费
├── file_assets/       流水线文件资产与对象存储生命周期
├── steward/           数据管家 workspace、工具、浏览器协作与编排
└── transforms/        LLM 抽取兼容 API 和受认证的 REST Connector fixture
```

## 关键边界

- `datasets/` 的 `DatasetVersion` 是数据快照；成品审核绑定精确版本，不能只看
  数据集名称或沿用旧版本审批。
- `pipelines/router.py` 的端点主体委派到同目录 service，但仍保留授权/owner
  查询、n8n 生命周期锁、task reference 查询和兼容 wiring，不能描述为纯
  adapter。管理、执行/dry-run、依赖检查、发布校验分别位于
  `management_service.py`、`execution_service.py`、`dependency_service.py`
  和 `validation_service.py`；A/B/C 纯转换位于 `route_executor.py`，同步链式
  run-record 编排位于 `trigger_service.py`，`engine.py` 仅保留新旧 import 的
  兼容 facade。实际异步执行入口在
  [`app/tasks/v2/pipeline_run.py`](../tasks/v2/pipeline_run.py)。
- `pipeline_tasks/router.py` 只装配任务池 HTTP；请求契约、流水线契约校验、
  查询/统计、历史/审计、生命周期和手动触发分别进入同目录 service，
  `engine.py` 保留带数据库租约的执行主链。
- `curated/` 按目录查询、批准版本读取/导出、审核差异和生命周期分层；普通预览
  与导出不能绕过当前版本审批，审批写端通过 `version_event_outbox.py` 与事务
  同步入队。
- DatasetVersion 自动化通过 durable event/outbox 交接；跨域 Mapping 消费必须
  走明确端口，禁止从本目录 import 本体 HTTP router。
- `steward/router.py` 聚合数据管家 HTTP/SSE 表面，但 39 个 handler 都只委派：
  DTO、流式会话、查询、生命周期、浏览器来源与浏览器会话分别位于同域模块；
  SSE session 生命周期和 workspace 清理由边界测试固定。
- `transforms/test_data_router.py` 是 Docker 内 REST Connector 的固定认证 fixture，
  不是生产数据源，也不能扩展为第二套测试目录。
- `connections/collectors_router.py` 的 AI-HOT 是登记过的兼容例外：它在
  ontology/release lock 内直接写 Formal Object/Link/Fact，不经过
  DatasetVersion、Pipeline、Curated review 或 Mapping；当前没有可达 UI
  调用。不得把它推广成绕过标准审核链路的通用采集模式。

完整状态流、API、前端和测试对应关系以本目录实现及 `backend/tests/` 中
data-channel 相关测试为准。
