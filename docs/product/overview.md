# 产品概览

OntologyBuild 是一个“本体即服务（Ontology-as-a-Service）”平台：把外部数据
接入、清洗和映射为对象、关系、动作与规则，并通过 Sentinel Engine 在数据
变化时评估条件和执行受治理的动作。

## 核心业务链路

平台核心由三个有独立状态门的流程组成：

1. **数据生产**：连接、上传或手工输入形成 immutable DatasetVersion；流水线经过
   dry-run、定义校验、发布和启用后产生成品版本，再由管理员按版本审核；
2. **定义发布**：从当前 immutable Ontology release 创建草稿，经隔离试跑和
   影响确认后由管理员晋级，激活精确对象/关系/Fact 投影；
3. **发布后刷新**：新的 approved 数据版本只通过显式订阅触发 Mapping 完整协调，
   再进入 release-scoped Sentinel 和受治理 Action。

完整图、失败路径与权威存储见[核心数据流](../architecture/data-flow.md)；状态门、
权限、幂等和回滚基线见
[核心运行契约](./requirements/0002-core-data-ontology-runtime-contract.md)。

平台同时包含业务探索、数据管家、超级助手、事件登记、API Hub、模型配置与
系统治理等能力。导航只是用户入口；源码按稳定业务能力组织。

事件登记提供人工/JWT 管理和第三方 X-API-Key ingest。它可以保存可选的
`ontology_id` 关联，但当前源码没有证明 RegisteredEvent 会自动转换为 Formal
实例或触发 Sentinel；该能力不能从 UI 名称或字段存在推断。

## 主要运行组件

- React/Vite 前端；
- FastAPI API；
- Celery worker；
- PostgreSQL 主数据；
- PostgreSQL 主事实、MinIO 非结构化对象；
- Redis/Celery 执行通道、Neo4j/ChromaDB 可重建查询投影；
- 本地配置中心；
- GitHub Actions 与 Docker Compose 生产部署。

下一步按需要进入：
[导航与业务能力](./navigation-business-map.md)、
[统一模块地图](../architecture/module-map.md)、
[Ontology 当前实现](../reference/ontology.md)、
[Sentinel Engine 当前实现](../reference/sentinel-engine.md)。
