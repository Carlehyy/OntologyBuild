---
version: alpha
name: OntologyBuild-Design-Language
description: >-
  OntologyBuild（本体即服务平台）的统一设计语言。基于 VoltAgent/awesome-design-md
  （站点形式 getdesign.md）收录的 Supabase 设计分析骨架适配：白底近单色画布、
  单一绿系强调、克制 chrome、数据密度优先。本文档是前端 UI 的唯一设计事实
  来源，供人类开发者与 AI 编码代理共同遵循；取值的可执行载体是
  frontend/src/styles/tokens.css 与 frontend/src/lib/echartsTheme.ts。
---

# OntologyBuild 设计语言（DESIGN.md）

## 0. 适用范围与优先级

- **适用**：`frontend/` 下全部 B 端产品界面——页面、弹层、表单、表格、图表、图可视化。
- **不适用**：对外营销落地页（如需再另行定义）。
- **冲突裁决**：`tokens.css` / `lib/echartsTheme.ts` > 本文档示例。本文解释
  「为什么、怎么用」，令牌文件定义「是什么」。改取值必须落在令牌文件里，
  不允许在页面里私改。
- 新页面必须遵循本文档；存量页面的历史样式按业务域渐进迁移，不要求一次性重写。
- 已知存量硬编码集中在 `palantir-graph/`、`pages/ontologies/mapping/`、
  `pages/ontologies/detail/`、`pages/login/` 及两份待迁移页域主题
  （`pages/ontologies/detail/tabs/chartTheme.ts`、
  `pages/world-model/worldModelChartTheme.ts`，见 5.1/8.2）；这些文件不作为
  取色参照，触碰时按迁移处理，不得在其上继续扩散。

## 1. 设计原则（源自 Supabase 模板）

1. **近单色画布 + 单一彩色强调**：界面主体由白/近黑墨/灰阶构成，唯一的彩色事件是
   teal 强调（导航、焦点环、主操作、图表主序列）。一个视图内不要出现第二种
   抢眼色相的装饰性用法。
2. **克制的 chrome**：发丝边框 + 极浅阴影区分层级，不用重投影、大圆角或渐变堆砌。
3. **数据密度优先**：这是 B 端数据产品——表格、图、日志是主角。留白服务于扫描效率，
   而非营销式的大标题节奏。
4. **开发者气质**：等宽字体用于代码/ID/数值列；状态用语义徽章而非彩色卡片轰炸。
5. **浅色优先，深色完备**：默认浅色；任何颜色新增必须同步给出 `.dark` 取值。

## 2. 色彩系统

### 2.1 核心语义令牌（唯一来源 `frontend/src/styles/tokens.css`）

| 令牌 | 浅色 | 深色 | 用途 |
|---|---|---|---|
| `--background` | `#eef1f5` | `#0d1117` | 页面底色 |
| `--card` | `#ffffff` | `#161c26` | 卡片/面板 |
| `--foreground` | `#1a1a2e` | `#e6e9ef` | 正文墨色 |
| `--primary` | `#1a1a2e` | `#e6e9ef` | 主按钮/主操作 |
| `--muted-foreground` | `#5a5a72` | `#98a2b3` | 次要文字 |
| `--border` | `#e2e4e9` | `#2a3342` | 发丝边框 |
| `--ring` | `#0d9488` | `#14b8a6` | 焦点环/强调 |

### 2.2 平台强调色（teal 系）

- 品牌强调：teal `#0d9488`（浅）/ `#14b8a6`（深），用于导航底色
  （`--color-nav-bg`）、焦点环、选中态、图表主序列。
- 使用纪律：强调色是「信号」不是「装饰」——同一视图只给最重要的元素。

### 2.3 语义色

success `--color-success #2d8a4e` · warning `#c9861a` · danger
`--destructive #c23b3b`（深 `#e5534b`）· info `#2563eb`；各自配 *-bg 浅底。
状态表达优先「浅底 + 深字」组合，而非大面积实色。

### 2.4 使用规则

- 一切界面颜色经 Tailwind 语义类（`bg-background` / `text-muted-foreground` /
  `border-border`…，见 `tailwind.config.ts` 的 shadcn 映射）或 `var(--token)` 引用；
- **禁止**在页面 TSX/CSS 新增硬编码 hex；图表数据序列例外，见第 5 节；
- 需要新颜色时：先进 `tokens.css`（`:root` 与 `.dark` 成对），再使用。

## 3. 字体与排版

- 正文栈：系统栈 + 中文回退（`index.css`）；代码/数值：`JetBrains Mono`
  （`@fontsource/jetbrains-mono`），西文等宽在前、中文苹方/雅黑回退。
- 字号阶梯（`--font-*`）：12 / 13 / 14(基准) / 16 / 18 / 20 / 24px；
  字重只用 400 / 500 / 600；行高 tight 1.25 / normal 1.5 / relaxed 1.75。
- 中文排版：标题不加负字距；数字密集列用等宽字体 + 右对齐。
- 「技术标签」模式（吸收自上游 Supabase 的等宽标签惯例）：JetBrains Mono
  11–12px、全大写、字距约 0.1em、`muted-foreground` 色，用于区块小标题、
  ID 与状态组标签；不引入新字体，继续用 `@fontsource/jetbrains-mono`。

## 4. 组件规范

### 4.1 组件来源分层

| 层 | 来源 | 说明 |
|---|---|---|
| 基础件 | `components/ui/*`（shadcn 语义约定） | Button/Card/Badge/Input/Dialog 等，样式全部走 token |
| 复杂件 | antd 6 | 表格/树/穿梭等重组件，经 ConfigProvider token 对齐平台色 |
| 样例库 | ReUI（shadcn 注册表扩展） | 按需拷贝源码，拷贝后按 4.2 规则换肤 |
| 表格块参考 | Tremor Blocks | 只参考布局结构，配色按 4.2 映射 |

### 4.2 外部样例拷贝规则（治理条款）

- **ReUI**：与 shadcn 同构，可放心作为复杂控件样例；拷贝进仓库前必须把示例中的
  调色板 hex 全部替换为平台 token（或删除纯装饰用色）。
- **Tremor**：自带 `tremor-*` 类命名空间与灰阶体系，**不得原样入库**。布局结构可
  参考，颜色一律映射：`text-gray-900`→`text-foreground`、`dark:text-gray-50`→
  深色前景、`bg-tremor-background-muted`→`bg-muted`、`tremor-border`→`border-border`；
  其品牌蓝/青一律替换为平台 teal 或语义色。
- 任何第三方组件自带的「第二套色板」都不允许进入全局样式层。

### 4.3 基础件要点

按钮圆角 `--radius-md`(8px)、卡片 `--radius-lg`(12px)；弹层遮罩
`--color-bg-overlay`；Toast 层级 `--z-toast: 1100`（高于 antd 弹层，低于悬浮助手）。

## 5. 图表规范（ECharts 为主要标准）

### 5.1 唯一主题来源 `frontend/src/lib/echartsTheme.ts`

分类序列色板（按序轮转，勿在页面重排）：

```
CHART_TEAL #0D9488 · CHART_BLUE #3B82F6 · CHART_VIOLET #8B5CF6 · CHART_AMBER #F59E0B
CHART_RED #F43F5E · CHART_EMERALD #10B981 · CHART_INDIGO #6366F1 · CHART_ORANGE #F97316
CHART_CYAN #14B8A6 · CHART_PINK #EC4899      （扩展位：CHART_SKY #0EA5E9）
```

文本/轴/网格：`CHART_TEXT #64748B` · `CHART_TEXT_STRONG #334155` ·
`CHART_AXIS #CBD5E1` · `CHART_SPLIT #F1F5F9`；紧凑图的半透明轴线/虚线网格用
`CHART_AXIS_LINE_SOFT` / `CHART_SPLIT_LINE_SOFT`。

### 5.2 通用基调

所有图表以 `baseChartOption()` 为基底：600ms 入场、cubicOut 缓动、白底 96% 圆角
tooltip（描边 `#E2E8F0`）。紧凑 KPI 迷你图可关动画与坐标轴，但配色仍须来自本模块。

### 5.3 语义映射

成功/运行 → teal·emerald 系；失败/危险 → `CHART_RED` 系；命中/警告 → amber；
信息/对比 → blue；占比/评级 → violet。同一图表内避免 teal 与 emerald 同时承担
不同语义（易混淆）。

### 5.4 固定浅色作用域

部分页面（本体详情等）为固定浅色作用域、不随 `.dark` 翻转：此类页面必须在域内
注释声明（沿用 `worldModelChartTheme.ts` 头注格式），且仍使用本模块常量值。

### 5.5 备选图引擎（AntV G6 / X6）

ECharts 关系图能力不足时可选 G6（图可视化）/X6（图编辑），定位为**备选引擎**：
节点/边/画布取值必须对齐本节色板与 token（画布=`var(--card)`、常规边=
`var(--border)` 加深一档、主实体=teal 强调）；引入属技术选型变更，须单独评估，
不在样式 PR 内夹带。

## 6. 布局与密度

- 4px 栅格（`--space-1..12`）；圆角阶梯 sm4/md8/lg12/xl16/full；阴影三级
  （sm/md/lg，均为极浅投影）。
- 页面骨架：顶部导航（teal）→ 页头（标题 + 主操作右置）→ 卡片栅格；
  列表/表格页保持行高紧凑（约 40px 行高量级）。
- 空态：一句话说明 + 可选主操作；加载态用既有 LoadingState/skeleton 惯例，
  不自造转圈。

## 7. 深浅模式

- 切换机制：`<html class="dark">`（`lib/theme.ts` + tailwind `darkMode: ['class']`）。
- **任何 token 改动必须 `:root` 与 `.dark` 成对维护**（AGENTS.md 亦有所述）。
- 深色模式的层级表达优先用边框与表面色阶（`--card`/`--border`/`--accent`），
  阴影只作极轻辅助（对齐上游 Supabase「深度靠边框层级、不靠阴影」的治理）；
  禁止在深色下叠加重阴影制造层级。
- 图表现状为固定浅色作用域；新增深色图表支持时优先把取值改为 CSS 变量注入，
  不要再复制第二套主题文件。

## 8. 禁止事项

1. 页面私有 CSS 内新造色板、TSX 内联样式写 hex（图表序列除外，且必须 import
   `@/lib/echartsTheme`）；
2. 再新建页域级 `*chartTheme*`/`*colors*` 常量文件（存量三处已收敛或待迁移）；
3. 第三方样例（Tremor/ReUI 等）配色未经映射直接入库；
4. 单独调整某个页面的颜色而不经过 tokens/共享主题（「顺手硬编码」）；
5. 一个 PR 内混合结构调整与视觉重构（遵循 AGENTS.md §3 分开提交）。

## 9. 变更方式

设计语言演进 = `tokens.css` / `lib/echartsTheme.ts` / 本文档**三者同一 PR** 同步修改，
并运行完整前端门禁（feature-boundaries、lint、build、unit、mocked e2e）。

## 附：来源

- 风格骨架改编自 [VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md)
  收录的 Supabase 设计分析（white canvas + near-black ink + 单一绿色 CTA + 数据平台气质）。
- 上游站点形式：[getdesign.md 的 Supabase 设计分析](https://getdesign.md/supabase/design-md)
  （同源内容；本平台适配保留 light 优先与数据密度取向，未照搬其营销站排版尺度，
  差异与可吸收项见 2026-08-30 对比记录）。
- 组件生态参照：shadcn/ui 主题约定、[reui.io](https://reui.io)、
  [blocks.tremor.so](https://blocks.tremor.so)、[ECharts](https://echarts.apache.org/)、
  [AntV G6](https://g6.antv.antgroup.com) / [AntV X6](https://x6.antv.antgroup.com)。
