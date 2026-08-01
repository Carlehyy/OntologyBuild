# ADR-0003：退役文档到本体的遗留抽取链路

- 状态：Accepted
- 日期：2026-08-02
- 决策人：Repository maintainers
- 关联需求/迭代：[当前平台导航与访问控制契约](../../product/requirements/0001-current-platform-contract.md)、[退役遗留文档本体抽取](../../iterations/2026/2026-08-02-retire-legacy-document-ontology-extraction.md)

## 背景与约束

系统设置中的“规则设置”和“提示词模板”服务于早期的文档 → LLM → 本体结构
链路；同一链路还包含本体文件上传、v1 execute、v2 extraction 和把普通后端
operation 暴露为 `/mcp` 的“开放接口”。当前产品的本体定义来源已经收敛为：

1. 在本体管理中手工创建和维护；
2. 在业务探索中通过对话生成草稿，经人工确认后应用。

继续保留旧链路会同时维护两套建模入口、五张专用表、22 个 OpenAPI operation
和一套独立 MCP 暴露面，且会让用户误以为提示词或阈值仍控制当前建模过程。

“开放接口”只是系统设置中的旧通用 API-to-MCP 功能。API Hub 的接口发布与
`/api-hub`、`/proxy` 协议、MinIO MCP、Plugin 社区 MCP 和超级助手的用户级
MCP server 都是独立能力，不在本次退役范围内。

## 决策

1. 删除系统设置中的规则设置、提示词模板和开放接口页面、API 与专用实现；
   系统设置继续提供用户、智能体、工作流、MinIO 和领域配置。
2. 删除 v1 本体上传文件/execute 和 v2 extraction HTTP 链路。通用文档转换器
   继续供业务探索与数据管家使用；本体图查询中的 NL-to-Cypher 也继续保留。
3. 删除 `rules_config`、`prompts`、`uploaded_files`、`extraction_tasks`、
   `mcp_interface_configs` 五张遗留表。升级不可逆地丢弃表内行；降级只恢复
   空表结构，不承诺恢复数据或文件。
4. 旧客户端提交 `build_mode=simple_llm` 时在 create/update 请求边界归一为
   `manual`，避免继续写入已退役模式；已有历史本体行不在迁移中批量改写。
5. 保留 Celery task name `app.tasks.extraction.run_extraction` 作为最小 tombstone，
   让升级前已入队消息可被新 worker 消费。它只返回 `retired` 结果，不访问
   遗留表、模型或抽取服务。
6. 删除 22 个遗留 OpenAPI operation，并用精确负向契约阻止它们意外返回；
   同一测试正向固定 API Hub、MinIO、社区和超级助手 MCP 的保留入口。旧 raw
   `/mcp` 返回 404，其他独立 raw MCP 路由继续由各自中间件处理。

## 备选方案

- 只隐藏前端入口：数据库、API、worker 和脚本仍可继续运行旧流程，无法满足
  “删干净”的目标，也会持续形成安全和维护面。
- 保留提示词作为通用 LLM 配置：当前 Agent、业务探索和数据管家有各自明确的
  prompt/对话契约，复用遗留模板会重新耦合已分离的业务域。
- 立即删除旧 Celery task name：会让 broker 中升级前消息变成未注册任务，
  不符合任务名兼容约束。
- 迁移时递归删除 uploads：`uploaded_files.file_path` 可能位于共享目录，数据库
  迁移无法证明物理对象没有其他用途，自动删除风险不可接受。

## 结果与权衡

- 用户只看到仍生效的本体建模方式，旧规则/提示词不再制造错误预期；
- OpenAPI、ORM 注册、seed、脚本和测试不再携带旧运行链路；
- 部署前必须额外备份并保留遗留文件清单；数据库 downgrade 不是数据回滚；
- 为队列兼容保留一个无业务行为的 tombstone，以及供其他业务域使用的通用
  文档转换器和 legacy 图/向量迁移 bridge。这些保留项不得重新接回 HTTP 抽取。

## 兼容、迁移与回滚

应用升级前必须备份数据库、共享 uploads 和对象存储，并从 `uploaded_files`
导出 `id`、`ontology_id`、`file_path` 等清单。Alembic 按外键依赖顺序先删除
`extraction_tasks`，再删除上传元数据、提示词、规则和开放接口配置。

0055 是 contract migration，不能在旧 API 或 Celery worker 仍可能访问
这些表时执行。部署必须先记录原先正在运行的服务，再停止 frontend、
Celery worker 和 backend，然后才能运行迁移，因此本次升级接受一次
明确的短暂停机。若迁移失败且未成功到达 head，只重启升级前确实
运行的旧服务；若迁移已成功而新服务启动或健康检查失败，禁止把
旧二进制直接接到已删表 schema，必须恢复同一时间点的数据库与文件
备份并部署旧镜像，或保持停机执行经批准的前向修复。

迁移不删除任何物理文件。上线验证和保留期结束后，运维只能依据升级前清单，
逐个确认对象确属遗留本体上传且没有其他引用，再在独立、可审计的清理变更中
删除；禁止对共享 uploads 或对象存储执行递归清空。

应用回滚必须恢复升级前同一时间点的数据库与文件备份，再部署旧镜像。
Alembic downgrade 只会重建空表，不是数据恢复，也不得用来让旧应用继续
对已经历过 contract migration 的数据库提供服务。

## 验证方式

- 精确断言 22 个遗留 OpenAPI operation 不存在，raw `/mcp` 返回 404；
- 正向断言 API Hub、MinIO、Plugin 社区和超级助手 MCP 仍注册；
- Alembic 单 head、新库升级、带遗留行的 0054 → head 升级、schema-only
  downgrade/re-upgrade；
- Celery 注册集合与 tombstone 返回值；
- Settings/RBAC、模型配置、业务探索、数据通道、Mapping 和本体回归；
- 前端 unit、feature boundary、E2E 分类、lint、build、mocked E2E，以及隔离
  stack 中的系统设置和本体旅程；
- Markdown 链接与仓库卫生门禁。
