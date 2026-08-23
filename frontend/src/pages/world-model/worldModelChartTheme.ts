// 世界模型页图表主题：自平台共享主题 re-export（原页域私有副本已收敛，
// 唯一来源为 @/lib/echartsTheme，取值口径见根目录 DESIGN.md 第 5 节）。
// 保持既有 WM_CHART_* 导出名以稳定本域调用方（CallsTrendChart/TrajectoryPreview）。
// 页面为固定浅色作用域，取值即共享浅色标准。
import type { EChartsOption } from 'echarts'
import {
  baseChartOption,
  CHART_AXIS,
  CHART_BLUE,
  CHART_RED,
  CHART_SPLIT,
  CHART_TEAL,
  CHART_TEXT,
  CHART_TEXT_STRONG,
} from '../../lib/echartsTheme.ts'

export {
  CHART_AXIS as WM_CHART_AXIS,
  CHART_BLUE as WM_CHART_BLUE,
  CHART_RED as WM_CHART_RED,
  CHART_SPLIT as WM_CHART_SPLIT,
  CHART_TEAL as WM_CHART_TEAL,
  CHART_TEXT as WM_CHART_TEXT,
  CHART_TEXT_STRONG as WM_CHART_TEXT_STRONG,
}

/** 图表共用的动效、字体与浮层基调（共享 baseChartOption 的本域别名）。 */
export function baseWorldModelChartOption(): EChartsOption {
  return baseChartOption()
}
