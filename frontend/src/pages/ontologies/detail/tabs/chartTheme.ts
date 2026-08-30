// 实例数据页图表主题：硬编码 hex 沿用事件登记/数据通道页惯例
// （本体详情页是固定浅色作用域，不随 .dark 翻转，见 ontology-glass.css）。
import type { EChartsOption } from 'echarts'

export const CHART_PALETTE = [
  '#059669', '#3B82F6', '#8B5CF6', '#F59E0B', '#F43F5E',
  '#10B981', '#6366F1', '#F97316', '#14B8A6', '#EC4899',
]

export const CHART_TEAL = '#059669'
export const CHART_TEXT = '#64748B'
export const CHART_TEXT_STRONG = '#334155'
export const CHART_AXIS = '#CBD5E1'
export const CHART_SPLIT = '#F1F5F9'

/** 所有图表共用的动效与字体基调：统一 600ms 入场 + cubicOut 缓动。 */
export function baseChartOption(): EChartsOption {
  return {
    animationDuration: 600,
    animationEasing: 'cubicOut',
    textStyle: { fontFamily: 'inherit', color: CHART_TEXT, fontSize: 11 },
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: '#E2E8F0',
      textStyle: { color: CHART_TEXT_STRONG, fontSize: 12 },
      extraCssText: 'box-shadow:0 8px 24px rgba(15,23,42,0.10);border-radius:10px;padding:8px 12px;',
    },
  }
}

export const categoryAxisBase = {
  axisLine: { lineStyle: { color: CHART_AXIS } },
  axisTick: { show: false },
  axisLabel: { color: CHART_TEXT, fontSize: 11 },
}

export const valueAxisBase = {
  splitLine: { lineStyle: { color: CHART_SPLIT } },
  axisLabel: { color: CHART_TEXT, fontSize: 10 },
}
