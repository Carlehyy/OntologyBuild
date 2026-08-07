# 本体结构画布首次进入直接居中

| 字段 | 内容 |
|---|---|
| 状态 | Validated |
| 日期 | 2026-08-07 |
| 负责人 | Codex |
| 评审人 | 未指定 |
| Issue/PR | 用户反馈：本体结构首次展示时内容从左侧滑入中心 |
| Commit | 本提交 |
| 目标分支 | `nano-ontoprompt` |
| 业务域 | 本体管理（本体详情 / 本体结构） |

## 背景

从本体详情顶部导航进入“本体结构”后，React Flow 先按默认视口展示节点，随后
延迟 80 ms 执行带 260 ms 过渡的 `fitView`。因此节点首次可见时位于画布左侧，
再明显滑向中心，形成不必要的初始化动画。

## 目标

- 首次进入本体结构时，在节点尺寸测量完成后直接以居中视口显示；
- 首次可见帧到稳定帧不发生可感知的横向或纵向漂移；
- 保留 L1/L2 切换、“适应画布”、搜索与智能整理等用户主动操作的平滑动画。

## 非目标

- 不调整结构图的力导向布局算法、节点坐标或保存格式；
- 不修改本体结构的只读契约、拖拽保存行为或后端接口；
- 不改变顶部导航、路由、权限或 menu key。

## 当前状态与变更前基线

变更前，`ModelStructureView` 为 React Flow 设置固定 `defaultViewport`，并在
组件 effect 中延迟调用带动画的 `fitView`。新增的浏览器回归在原实现上记录到
首个可见帧与稳定帧之间约 `357.48px` 的横向位移，能够稳定复现用户反馈。

## 变更范围

| 模块/路径 | 改动 | API/menu key/数据库/Celery/环境变量影响 |
|---|---|---|
| `frontend/src/pages/ontologies/detail/tabs/ModelStructureView.tsx` | 让 React Flow 在节点测量完成时执行无动画的初始 `fitView`，延迟动画仅用于后续图谱变化 | 无 |
| `frontend/src/test/e2e/ontology_structure_initial_view.spec.ts` | 新增完全 mocked 的首帧居中与稳定性回归 | 无 |
| `frontend/playwright.mocked.config.ts` | 将新增用例归入 mocked suite | 无 |
| `frontend/src/test/e2e/README.md` | 更新 E2E 分类数量 | 无 |

## 兼容策略

HTTP、HashRouter 深链、顶部导航、权限、发布快照、布局保存键和 React Flow 节点
坐标均保持不变。初始化采用 React Flow 自带的 `fitView` 生命周期；用户主动触发
的视口操作继续使用原有动画参数。

## 安全与数据处理

未读取或修改生产环境配置与秘密。新增浏览器 fixture 为确定性的合成本体，只
包含虚构标识和结构数据；测试结果写入仓库忽略的 `.artifacts/`。

## 验收条件

- 点击“本体结构”后，首个可见节点已经位于画布中心；
- 700 ms 采样窗口内，首帧与最终帧的 X/Y 中心位移均不超过 1 px；
- 采样窗口内初始视口不存在逐帧插值动画；
- 新增 spec 仅依赖本地 mock，并通过 E2E 分类门禁；
- 前端单元测试、lint、build 和 mocked E2E 全部通过。

## 验证证据

| 层级 | 实际命令/环境 | 退出结果 | CI URL / artifact / 跳过原因 |
|---|---|---|---|
| 回归基线 | `npx playwright test --config=playwright.mocked.config.ts src/test/e2e/ontology_structure_initial_view.spec.ts`（修复前） | 失败，捕获横向位移约 357.48 px，符合预期 | 本地 `.artifacts/playwright/mocked-results/` |
| targeted 浏览器 | 同上（修复后） | 通过，1 passed | 本地运行，无 CI URL |
| 单元 | `cd frontend && npm run test:unit` | 通过，12 passed | 本地运行，无 CI URL |
| 集成/契约 | `cd backend && uv sync --frozen --group dev && uv run pytest -q --disable-warnings --ignore tests/v2/perf`；`cd config && uv sync --frozen --group dev && uv run pytest -q` | 后端 1857 passed、1 skipped；配置中心 42 passed | 本地运行，无 CI URL；skip 为测试套件既有条件分支 |
| 前端静态 | `npm ci`；`npm run check:feature-boundaries`；`npm run test:e2e:classification`；`npm run lint`；`npm run build` | 全部通过；50 个 E2E spec 唯一分类，生产构建成功 | 本地运行，无 CI URL；Vite 仅报告既有大 chunk 警告 |
| mocked 浏览器 | `cd frontend && npm run test:e2e:mocked` | 通过，82 passed | 本地 `.artifacts/playwright/mocked-results/` |
| 仓库门禁 | `node scripts/ci/check-markdown-links.mjs`；`bash scripts/ci/check-repository-hygiene.sh` | 通过，Markdown 0 error / 0 warning，仓库卫生通过 | 本地运行，无 CI URL |
| stack/external E2E | 不适用 | 未执行 | 本次仅改变前端首帧视口，新增用例完全 mock；不触及真实后端或外部服务 |
| 部署/回滚 | 不适用 | 未执行 | 当前工作区未部署 |

## 上线步骤、监控指标与观察窗口

按标准前端制品流程发布。上线后观察一个发布窗口内本体详情“本体结构”入口的
前端异常率，并在桌面常用视口抽查首次进入、L1/L2 切换、缩放与智能整理。

## 回滚触发条件与逐步方案

若出现结构节点不可见、初始化缩放异常，或 L1/L2 切换后无法适应画布，则回滚
本次前端提交并重新构建发布制品。该变更不包含数据迁移，回滚无需处理数据库或
已保存画布布局。

## 已知风险与后续动作

初始居中依赖 React Flow 的节点尺寸测量生命周期，浏览器回归已覆盖 Chromium
桌面视口。本次无遗留阻断；后续如扩展移动端本体结构工作台，应增加对应小视口
的布局验收。
