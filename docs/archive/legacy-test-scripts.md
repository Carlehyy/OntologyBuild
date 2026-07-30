# 遗留测试与演示脚本清理记录

2026-07-30 的测试目录审计确认，下列四个 `backend/tests/test_*.py` 文件不是
pytest 测试模块：三个只定义 `main()`，一个在 pytest 导入阶段直接跳过。它们
不会为标准后端门禁提供可执行断言，因此已从当前版本树删除。

| 已删除文件 | 主要风险 | 当前正式替代入口 |
|---|---|---|
| `test_core_features.py` | 导入时改写进程环境、执行 `Base.metadata.drop_all()`，使用历史绝对路径，末尾 `sys.exit()` | [`tests/auth/`](../../backend/tests/auth/)、[`tests/ontologies/`](../../backend/tests/ontologies/) |
| `test_formal_e2e.py` | 固定访问 `localhost:8000`，使用默认账号并写入运行中数据库；pytest 收集 0 项 | [`tests/ontologies/`](../../backend/tests/ontologies/)、[`tests/data_channel/test_collector_type_integrity.py`](../../backend/tests/data_channel/test_collector_type_integrity.py) |
| `test_multitable_fk_projection.py` | 拉取真实 AI HOT 数据并直接写当前数据库，依赖已有用户，末尾 `sys.exit()`；pytest 收集 0 项 | [`test_data_mapping_bridge_e2e.py`](../../backend/tests/v2/mapping/test_data_mapping_bridge_e2e.py)、[`test_supply_chain_golden.py`](../../backend/tests/v2/mapping/test_supply_chain_golden.py) |
| `test_pipeline_formal_projection.py` | 拉取真实外部数据并直接写当前数据库，依赖当前工作目录和已有用户；pytest 收集 0 项 | [`tests/v2/pipeline/`](../../backend/tests/v2/pipeline/)、[`pipeline_ontology_supply_chain.spec.ts`](../../frontend/src/test/e2e/pipeline_ontology_supply_chain.spec.ts) |

需要真实外部依赖时，使用明确标识且由操作者主动执行的入口：

- [`api_hub_http_proxy_live_e2e.py`](../../backend/scripts/api_hub_http_proxy_live_e2e.py)
  验证真实 AI HOT 与 API Hub HTTP 代理；
- [`run_full_supply_chain.py`](../../scripts/data/run_full_supply_chain.py) 和
  [`run_supply_chain_pipeline.py`](../../scripts/data/run_supply_chain_pipeline.py)
  验证运行中服务上的供应链链路；
- [`pipeline_ontology_supply_chain.spec.ts`](../../frontend/src/test/e2e/pipeline_ontology_supply_chain.spec.ts)
  是正式真实栈浏览器验收入口。

这些真实环境入口会创建或修改数据，必须按
[测试指南](../development/testing.md)在隔离环境执行，证据写入 CI artifact，
不得重新放回 `backend/tests/` 冒充默认单元测试。

## 陈旧演示入口

同一轮全仓零引用审计还删除了两个不属于正式测试、且无法满足当前脚本安全
契约的入口：

| 已删除文件 | 多方核验结论 | 当前替代入口 |
|---|---|---|
| `backend/scripts/demos/seed_financial_risk.py` | 源码、测试、配置和文档均无消费者；包含历史个人绝对路径 `/mnt/agents/nano-ontoprompt/backend`，不能在干净检出中可靠定位项目 | 受控演示见 [`backend/scripts/README.md`](../../backend/scripts/README.md)；金融 fixture 继续由 [`test_data/财务/`](../../test_data/财务/) 和正式 E2E 消费 |
| `scripts/data/seed_graph.py` | 源码、测试、配置和文档均无消费者；文档命令与实际路径不符，import 阶段即以固定 `admin/admin123` 请求运行中服务写入本体，无 main guard、dry-run 或清理 | 图谱/本体行为由 [`tests/ontologies/`](../../backend/tests/ontologies/) 和 [`ontology_detail.spec.ts`](../../frontend/src/test/e2e/ontology_detail.spec.ts) 验证 |

其他 maintenance/demo/live E2E 即使没有代码 import，也因承担明确的人工运维或
真实验收职责而保留；其运行依赖、写入范围和清理要求已逐项登记在
[`backend/scripts/README.md`](../../backend/scripts/README.md)，不得按静态
“零引用”批量删除。
