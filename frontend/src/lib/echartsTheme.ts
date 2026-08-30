/**
 * 平台 ECharts 图表主题（唯一共享来源，取值口径见根目录 DESIGN.md 第 5 节）。
 *
 * 合并自原页域私有副本：
 * - pages/world-model/worldModelChartTheme.ts（WM_CHART_* 已在其文件内 re-export 本模块）
 * - pages/ontologies/detail/tabs/chartTheme.ts（待该域迁移后退役）
 * governance 页散落的内联 hex 也已收敛至此。
 *
 * 规则：新页面/新图表一律从这里导入常量与 baseChartOption()；
 * 禁止再新建页域主题常量文件或在 option 里写裸 hex。
 * 本体详情等固定浅色作用域页面沿用同一套浅色值（DESIGN.md 第 5.4/7 节）。
 */
import type { EChartsOption } from 'echarts'

/* ── 分类序列色（标准色板，语义映射见 DESIGN.md §5.3） ── */
export const CHART_TEAL = '#059669'
export const CHART_BLUE = '#3B82F6'
export const CHART_VIOLET = '#8B5CF6'
export const CHART_AMBER = '#F59E0B'
export const CHART_RED = '#F43F5E'
export const CHART_EMERALD = '#10B981'
export const CHART_INDIGO = '#6366F1'
export const CHART_ORANGE = '#F97316'
export const CHART_CYAN = '#14B8A6'
export const CHART_PINK = '#EC4899'
/** 扩展位：多序列图超出十色时的补充冷色（当前仅推演轨迹预览使用）。 */
export const CHART_SKY = '#0EA5E9'

/** 分类序列默认轮转顺序（与 tabs/chartTheme.ts 历史 PALETTE 一致）。 */
export const CHART_SERIES_PALETTE = [
  CHART_TEAL,
  CHART_BLUE,
  CHART_VIOLET,
  CHART_AMBER,
  CHART_RED,
  CHART_EMERALD,
  CHART_INDIGO,
  CHART_ORANGE,
  CHART_CYAN,
  CHART_PINK,
]

/* ── 文本 / 轴 / 网格（slate 系，与 tokens.css 中性灰阶同族） ── */
export const CHART_TEXT = '#64748B'
export const CHART_TEXT_STRONG = '#334155'
export const CHART_AXIS = '#CBD5E1'
export const CHART_SPLIT = '#F1F5F9'

/** 无框紧凑图的细弱轴线 / 半透明虚线网格。 */
export const CHART_AXIS_LINE_SOFT = 'rgba(148,163,184,0.4)'
export const CHART_SPLIT_LINE_SOFT = 'rgba(148,163,184,0.25)'

/** tooltip 描边（与 baseChartOption 一致的浅灰）。 */
export const CHART_TOOLTIP_BG = 'rgba(255,255,255,0.96)'
export const CHART_TOOLTIP_BORDER = '#E2E8F0'
/** tooltip 阴影/圆角/内边距的统一 extraCssText。 */
export const CHART_TOOLTIP_CSS = 'box-shadow:0 8px 24px rgba(15,23,42,0.10);border-radius:10px;padding:8px 12px;'

/** CHART_RED 的 RGB 通道，供渐变 areaStyle 组装 rgba(...)。 */
export const CHART_RED_RGB = '244,63,94'

/** 所有图表共用的动效与浮层基调：统一 600ms 入场 + cubicOut 缓动。 */
export function baseChartOption(): EChartsOption {
  return {
    animationDuration: 600,
    animationEasing: 'cubicOut',
    textStyle: { fontFamily: 'inherit', color: CHART_TEXT, fontSize: 11 },
    tooltip: {
      trigger: 'item',
      backgroundColor: CHART_TOOLTIP_BG,
      borderColor: CHART_TOOLTIP_BORDER,
      textStyle: { color: CHART_TEXT_STRONG, fontSize: 12 },
      extraCssText: CHART_TOOLTIP_CSS,
    },
  }
}

/** 类目轴基础配置（轴线/刻度/标签）。 */
export const categoryAxisBase = {
  axisLine: { lineStyle: { color: CHART_AXIS } },
  axisTick: { show: false },
  axisLabel: { color: CHART_TEXT, fontSize: 11 },
}

/** 数值轴基础配置（网格线/标签）。 */
export const valueAxisBase = {
  splitLine: { lineStyle: { color: CHART_SPLIT } },
  axisLabel: { color: CHART_TEXT, fontSize: 10 },
}
