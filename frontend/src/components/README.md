# 共享组件

跨页面共享组件的入口。结构定位见 [`src/README.md`](../README.md)，设计语言与
组件来源分层的事实源是根目录 [`DESIGN.md`](../../../DESIGN.md) §4。本文件回答
两件事：**什么时候用哪层组件**，以及**如何引入下一个 beUI 组件、存量页面如何
渐进采用**。

## 组件分层决策（选型先看这里）

| 需求 | 用哪层 | 位置 |
|---|---|---|
| 表格、树、穿梭、级联等重数据交互 | antd 6（ConfigProvider 已对齐平台色） | 全局 |
| 按钮、卡片、弹窗、输入等基础件 | shadcn 语义组件 | [`ui/`](./ui/) |
| 动效交互组件（当前：每周可用时段编辑器） | vendored beUI | [`availability-scheduler/`](./availability-scheduler/) |
| 动效原语（Switch/Select/Tooltip/Popover/IconButton 与弹簧曲线、手势 hooks） | vendored beUI，共享层 | [`motion-ui/`](./motion-ui/) |
| 本体图谱编辑画布 | palantir-graph 业务单元 | [`../palantir-graph/`](../palantir-graph/) |
| 悬浮助手、收件箱、工单等域组件 | 各业务域组件目录 | 各子目录 |

硬规则：

- 同一组件子树内不得混用两套体系的同类控件（如同一表单里 antd Switch 与
  motion Switch 并存）；
- beUI 组件**只能使用本目录已 vendored 的**，不得直接引用 beui.dev 在线产物；
- vendored 文件内颜色一律走平台语义令牌，禁止新增裸 hex；
- `motion-ui/` 已随第二个消费方（世界模型页域）提升为共享层，页面可直接
  import；新增动效优先复用其中的原语与 ease/touch 基础设施。

## 当前 vendored beUI 清单

| 内容 | 入口 | 说明 |
|---|---|---|
| AvailabilityScheduler | [`availability-scheduler/index.tsx`](./availability-scheduler/index.tsx) | 每周可用时段编辑器；`weekdayLabels` / `hourCycle` / `texts` 三个 props 可覆盖中文与 24 小时制默认；受控/非受控均支持。活示例：登录后访问 `/#/design/components` |
| AnimatedNumber | [`motion-ui/animated-number.tsx`](./motion-ui/animated-number.tsx) | 数字滚动（进视口触发、尊重减少动态效果）；统计卡数值常用 |
| TiltCard | [`motion-ui/tilt-card.tsx`](./motion-ui/tilt-card.tsx) | 指针跟随 3D 倾转 + 高光；仅真悬停设备生效，展示型卡片用 |
| CenterMorphModal | [`motion-ui/center-morph-modal.tsx`](./motion-ui/center-morph-modal.tsx) | 中心展开式 morph 弹窗（role=dialog、焦点圈定）；轻量告知类弹窗可用，重表单仍用平台 Modal |
| motion 原语（Switch、Checkbox、Tooltip、Select、MorphPopover、IconButton） | [`motion-ui/`](./motion-ui/) | 共享层，页面可直接使用；同类控件与 antd/shadcn 不得在同一子树混用 |
| 动效基础设施（ease 弹性曲线、touch 手势工具、use-dismiss 等 hooks、presence-gate） | [`motion-ui/`](./motion-ui/) | 同上 |

**API 文档就是源码**（shadcn 哲学：代码归你，直接读文件）；上游浏览与官方演示
见 <https://beui.dev>（MIT，github.com/starc007/ui-components），但一律以本目录
vendored 版本为准。

## 引入下一个 beUI 组件的流程

1. 先对照分层决策确认选型正确（重数据交互仍走 antd，不要用动效组件硬凑）；
2. 从 GitHub 上游拷贝**完整依赖闭包**（含 motion 原语与手势 hooks），文件头
   固定上游 commit 出处（当前基准 `afba7fa055dd`）；
3. 平台适配四件事：语义令牌映射（如 `border-strong` → `--color-border-hover`、
   焦点环统一 `ring-ring`）；Tailwind 3.4 兼容（TW4 的 `border-(--var)` 简写与
   CSS 变量色透明度修饰符必须改写）；中文默认文案 + 可覆盖 props；无障碍标签
   中文化；
4. 在 [`pages/design/ComponentGalleryPage.tsx`](../pages/design/ComponentGalleryPage.tsx)
   增加活示例（保持从 `main.tsx` 可达），并为示例补一条 mocked E2E（注册进
   `playwright.mocked.config.ts` 的 allowlist）；
5. 跑完整前端门禁；同步更新 DESIGN.md §4.1 与本文件清单。

## 存量页面如何渐进采用

- **新功能**：按分层决策直接选型，无历史包袱；
- **改存量**：只在与本次业务改动**同片的界面**内顺手替换为 vendored 组件
  （touch-to-migrate，同 DESIGN.md §0 的渐进迁移原则）；替换后的旧实现若无
  其他消费者即删（源码卫生），禁止"以后可能用"的保留；
- **不做**无业务驱动的整页重写，也不在功能 PR 里夹带大片视觉重构
  （DESIGN.md §8.5）；结构迁移与视觉迁移分开提交。
