# 后端测试

测试采用“业务域优先、稳定契约族次之、层级最后”的组织方式。小业务域直接
使用 `tests/<domain>/test_*.py`；只有用例足够多时再拆
`unit/api/integration`，避免制造空目录。`tests/v2/` 是有意保留的 API/runtime
v2 兼容契约族，内部仍必须按能力分目录，不能把新文件堆在其根目录。

使用 `test_router.py` 等跨业务域会重复的通用文件名时，业务域目录必须包含
`__init__.py`，保证 pytest 完整收集时使用唯一模块名；否则改用
`test_<domain>_router.py`。定向测试通过不能替代完整收集检查。

当前树：

```text
tests/
├── platform/
├── auth/
├── super_assistant/
├── exploration/
├── ontologies/
├── events/
├── data_channel/
├── api_hub/
├── model_configs/
├── settings/
├── inbox/
├── architecture/      import 与边界约束
├── migrations/        Alembic 和模型注册
├── infra/             配置、依赖与部署契约
├── object_storage/
└── v2/                API/runtime v2 契约族，按能力二级分组（含 perf）
```

根目录只保留 `conftest.py`、包入口和本说明，不再放置业务测试。
`tests/v2/` 根同样只保留包入口与共享 fixture；测试进入
`connection/curated/datasets/graph/incremental/infra/logic_actions/mapping/
migrations/models/perf/pipeline/search/services` 等明确二级目录。若某组测试的
主要身份已经是 canonical 业务域而不是 v2 兼容协议，可在独立迁移波次移入
`tests/<domain>/`：先记录收集数和文件 SHA，只移动测试、机械调整路径，不同时
重写逻辑。

当前已完成的业务域收口：

- `tests/platform/test_router.py`：平台概览路由与兼容入口；
- `tests/auth/`、`tests/inbox/`：认证、RBAC 和跨域收件箱契约；
- `tests/api_hub/`：接口定义、凭据、发布、代理和 TLS；
- `tests/exploration/`：探索会话、文档、权限和质量门；
- `tests/ontologies/`：本体、正规建模、版本、Agent 和 Sentinel；
- `tests/data_channel/`：采集、文件资产、任务引用和数据管家；
- `tests/events/`：事件登记、附件和 ingest；
- Plugin 社区由 `frontend/src/test/e2e/community.spec.ts` 覆盖用户可见契约，
  `tests/auth/test_user_rbac.py` 覆盖独立 MCP 权限边界；Skill 社区用例固定当前
  维护中占位。尚无独立 `tests/community/`，新增同域后端实现测试时再建立；
- `tests/architecture/test_retired_legacy_extraction_contract.py`：精确固定 22 个
  已退役 OpenAPI operation、raw `/mcp` 和独立 MCP 能力的保留边界；
- `tests/settings/test_n8n_client.py`：工作流客户端错误契约；
- `tests/model_configs/`：模型配置行为、旧 facade、selector patch 路径与
  OpenAPI 契约；
- `tests/infra/`、`tests/migrations/`：启动配置、仓库路径和专项迁移；
- `tests/architecture/test_canonical_import_boundaries.py`：已迁移 canonical 包
  和 `main.py` 装配的依赖方向。
- 原 `tests/v2/` 根下 7 个散落文件已按职责移入
  `datasets/curated/logic_actions/migrations/`；其中 5 个文件 SHA-256 原样
  一致，2 个 Alembic 文件只把 `Path(__file__).parents[2]` 机械调整为
  `parents[3]`，归一化后 SHA-256 与移动前一致。v2 根级业务测试数现在为 0。

上述目录都包含 `__init__.py`。第一波仅移动 53 个真实 pytest 模块并机械调整
4 个模块的 `__file__` 层级，没有改变测试语义；该波移动前后的完整
collect-only 均为 1475 cases。第二波只整理上述 7 个 v2 根级模块：5 个文件
SHA-256 不变，2 个路径敏感文件归一化父目录层级后 SHA-256 不变，移动前后均
收集 37 cases，移动后 37 passed。四个不会被 pytest 执行的根级历史脚本已
清理，依据和正式替代入口见
[归档记录](../../docs/archive/legacy-test-scripts.md)。

定向测试：

```bash
uv run pytest -q \
  tests/auth tests/api_hub tests/data_channel tests/events \
  tests/exploration tests/inbox tests/infra tests/migrations \
  tests/model_configs tests/ontologies tests/settings
```

完整回归：

```bash
uv run pytest -q --disable-warnings --ignore tests/v2/perf
```

性能测试：

```bash
uv run pytest -q --disable-warnings tests/v2/perf
```

fixture 应放在最近的业务域或 `tests/<domain>/fixtures/`。禁止使用真实生产
数据、个人绝对路径和测试间隐式顺序。完整矩阵见
[测试指南](../../docs/development/testing.md)。
