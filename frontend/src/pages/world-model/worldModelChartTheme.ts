// 世界模型页图表主题：硬编码 hex 沿用事件登记/实例数据页惯例（页面为固定浅色作用域）。
// 注意：feature-boundaries 禁止跨 pages 域引用，故本域自带主题常量。
import type { EChartsOption } from 'echarts'

export const WM_CHART_TEAL = '#0D9488'
export const WM_CHART_RED = '#F43F5E'
export const WM_CHART_BLUE = '#3B82F6'
export const WM_CHART_TEXT = '#64748B'
export const WM_CHART_TEXT_STRONG = '#334155'
export const WM_CHART_AXIS = '#CBD5E1'
export const WM_CHART_SPLIT = '#F1F5F9'

/** 图表共用的动效、字体与浮层基调 */
export function baseWorldModelChartOption(): EChartsOption {
  return {
    animationDuration: 600,
    animationEasing: 'cubicOut',
    textStyle: { fontFamily: 'inherit', color: WM_CHART_TEXT, fontSize: 11 },
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: '#E2E8F0',
      textStyle: { color: WM_CHART_TEXT_STRONG, fontSize: 12 },
      extraCssText: 'box-shadow:0 8px 24px rgba(15,23,42,0.10);border-radius:10px;padding:8px 12px;',
    },
  }
}
