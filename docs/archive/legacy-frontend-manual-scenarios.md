# 已删除的前端手工场景脚本

本记录只说明 2026-07-30 删除的 46 个 `frontend/scripts/` 历史脚本曾尝试覆盖
什么，以及当前应从哪里验证；它不是可执行手册，也不保留脚本代码。

## 历史场景意图

这些脚本大致涉及：

- 登录、API 调试和页面按钮探查；
- 本体、实体、关系、详情页和知识图谱操作；
- 供应链流水线、数据映射和多领域样例；
- LLM 提取、提示词初始化和跨领域结果检查；
- 页面截图、演示录制素材和临时缺陷复验。

## 删除依据

全仓静态导入、动态调用、package scripts、CI、文档、路由和正式 E2E 审计均未
发现消费者。脚本还普遍具有一项或多项不可接受的验收特征：

- 固定弱账号、localhost 端口、数据库对象 ID 或本地浏览器状态；
- 直接创建、修补或重提取持久化业务数据，缺少隔离和可靠清理；
- 以截图或控制台输出代替断言，部分路径捕获错误后继续执行；
- 场景间共享隐式状态，无法重复运行或并行运行；
- 与正式 Playwright 场景重复，却绕过测试分类、报告和 CI artifact。

因此，保留它们会制造“脚本存在即已覆盖”的错误信号。删除的是不可审计的
执行方式，不是放弃对应业务验收。

## 当前替代入口

正式浏览器测试统一位于
[`frontend/src/test/e2e/`](../../frontend/src/test/e2e/)，当前 49 个 spec
必须且只能进入 mocked、stack、external 三组之一。

| 历史意图 | 当前主要入口 |
|---|---|
| 登录、退出和不存在的注册页 | [`auth.spec.ts`](../../frontend/src/test/e2e/auth.spec.ts) |
| 本体创建、列表、详情和导入导出 | [`ontology_list.spec.ts`](../../frontend/src/test/e2e/ontology_list.spec.ts)、[`ontology_detail.spec.ts`](../../frontend/src/test/e2e/ontology_detail.spec.ts)、[`export.spec.ts`](../../frontend/src/test/e2e/export.spec.ts) |
| 供应链流水线、数据通道和映射 | [`pipeline_ontology_supply_chain.spec.ts`](../../frontend/src/test/e2e/pipeline_ontology_supply_chain.spec.ts)、[`data_channel_real_e2e.spec.ts`](../../frontend/src/test/e2e/data_channel_real_e2e.spec.ts) |
| 图谱、关系和映射交互 | [`graph_interaction.spec.ts`](../../frontend/src/test/e2e/graph_interaction.spec.ts)、[`agent_graph.spec.ts`](../../frontend/src/test/e2e/agent_graph.spec.ts)、[`data_mapping_preview.spec.ts`](../../frontend/src/test/e2e/data_mapping_preview.spec.ts) |
| 多领域与真实 LLM | [`three_domains_comparison.spec.ts`](../../frontend/src/test/e2e/three_domains_comparison.spec.ts)、[`all_domains_full_test.spec.ts`](../../frontend/src/test/e2e/all_domains_full_test.spec.ts) |
| UI、图表、图片与浏览器证据 | [mocked E2E 组](../../frontend/playwright.mocked.config.ts)；证据写入 `.artifacts/playwright/` 或 CI artifact |

这张表是场景级替代关系，不声称旧脚本的每一行都有一一对应实现。新缺口应写成
可复现的正式测试，而不是恢复一次性脚本。

## 如何新增正式场景

1. 在 `frontend/src/test/e2e/` 新建能够独立运行、具有明确断言的 spec。
2. 使用最小、确定、脱敏的 fixture；需要写数据时使用隔离栈并负责清理。
3. 完全替代应用请求才能进入 mocked；需要真实后端的进入 stack；真实付费或
   供应商依赖进入 external，并保留显式开关。
4. 在唯一一个 Playwright allowlist 中登记，并运行
   `npm run test:e2e:classification`。
5. 凭据通过测试环境变量注入，不写死弱账号、token 或个人路径。
6. 让截图、trace、video 和报告进入 `.artifacts/` 或 CI artifact，不提交到
   Git。

完整规则见 [测试与验收](../development/testing.md) 和仓库根
[`AGENTS.md`](../../AGENTS.md)。
