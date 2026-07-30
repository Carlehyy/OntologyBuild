# 共享测试 fixture

这里仅保存被自动化测试或受控数据脚本直接消费的、可提交且已脱敏的数据。
测试代码、生成器、截图和运行结果都不属于本目录。

| 路径 | 权威消费者 |
|---|---|
| `供应链/` | `pipeline_ontology_supply_chain.spec.ts`、`all_domains_full_test.spec.ts`、`three_domains_comparison.spec.ts`、后端 Route C 测试，以及 `scripts/data/` 的受控供应链/Sentinel 脚本 |
| `医疗/`、`教育/`、`法律/`、`营销/`、`财务/` | 全领域和三领域真实栈 E2E |
| `snomed_mental_health.csv` | `scripts/data/import_snomed.py`、`link_entities.py` 与 `link_entities.sh` |

目录约束：

- 新 fixture 必须在同一变更中加入明确的自动化消费者；
- 只保留验证行为所需的最小数据，不提交生产导出、真实用户、token、Cookie
  或凭据；
- 浏览器截图、JSON 结果和 trace 写入被忽略的 `.artifacts/`；
- 测试放在 `backend/tests/` 或 `frontend/src/test/e2e/`，运维/演示脚本放在
  `scripts/`，不得在这里新增可执行脚本；
- 删除或迁移 fixture 时，必须同步更新消费者并运行相关后端与 stack E2E。

历史上混放于此的手工流程、数据生成脚本和无消费者样例已删除；需要追溯时使用
Git 历史，正式替代入口见
[`docs/archive/legacy-test-scripts.md`](../docs/archive/legacy-test-scripts.md)。
