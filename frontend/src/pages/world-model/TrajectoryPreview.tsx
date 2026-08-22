import { useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import type { TrajectorySummary } from './trajectorySummary'
import {
  baseWorldModelChartOption,
  WM_CHART_AXIS,
  WM_CHART_SPLIT,
  WM_CHART_TEXT,
} from './worldModelChartTheme'

const SERIES_COLORS = ['#0D9488', '#3B82F6', '#F59E0B', '#8B5CF6', '#F43F5E', '#0EA5E9']

/**
 * 推演轨迹预览：契约返回 trajectory（数值序列/等宽数值二维数组）时，
 * 以折线呈现各步走势，confidence / boundary 以摘要行呈现；
 * 原始 JSON 折叠保留，供精确核对与复制。
 */
export default function TrajectoryPreview({ summary, payload }: {
  summary: TrajectorySummary
  payload: unknown
}) {
  const multiSeries = summary.series.length > 1
  const option = useMemo<EChartsOption>(() => ({
    ...baseWorldModelChartOption(),
    aria: {
      enabled: true,
      description: '推演轨迹折线预览：横轴为推演步，纵轴为各时点数值',
    },
    grid: { left: 4, right: 10, top: multiSeries ? 28 : 14, bottom: 2, containLabel: true },
    ...(multiSeries ? {
      legend: {
        top: 0,
        right: 0,
        icon: 'roundRect',
        itemWidth: 8,
        itemHeight: 8,
        itemGap: 12,
        textStyle: { color: WM_CHART_TEXT, fontSize: 10 },
      },
    } : {}),
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: '#E2E8F0',
      textStyle: { color: WM_CHART_TEXT, fontSize: 12 },
      extraCssText: 'box-shadow:0 8px 24px rgba(15,23,42,0.10);border-radius:10px;padding:8px 12px;',
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: Array.from({ length: summary.pointCount }, (_, index) => `${index + 1}`),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: WM_CHART_AXIS } },
      axisLabel: { color: WM_CHART_TEXT, fontSize: 10 },
      name: '推演步',
      nameTextStyle: { color: WM_CHART_TEXT, fontSize: 10 },
      nameGap: 4,
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: WM_CHART_SPLIT } },
      axisLabel: { color: WM_CHART_TEXT, fontSize: 10 },
    },
    series: summary.series.map((item, index) => ({
      name: item.name,
      type: 'line' as const,
      data: item.values,
      symbol: 'circle',
      symbolSize: 4,
      connectNulls: true,
      lineStyle: { width: 2, color: SERIES_COLORS[index % SERIES_COLORS.length] },
      itemStyle: { color: SERIES_COLORS[index % SERIES_COLORS.length] },
      emphasis: { focus: 'series' as const },
    })),
  }), [summary, multiSeries])

  const scaleLabel = multiSeries
    ? `${summary.series.length} 条 × ${summary.pointCount} 点`
    : `${summary.pointCount} 点`

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
          轨迹 {scaleLabel}
        </span>
        {summary.confidence !== null && (
          <span className="rounded-md bg-teal-50 px-1.5 py-0.5 text-[11px] text-teal-700">
            置信度 {summary.confidence}
          </span>
        )}
      </div>
      {summary.boundary && (
        <p className="rounded-lg bg-slate-50 px-2.5 py-1.5 text-[11px] leading-4 text-slate-500">
          适用边界：{summary.boundary}
        </p>
      )}
      <div className="h-44">
        <ReactECharts
          option={option}
          style={{ width: '100%', height: '100%' }}
          opts={{ renderer: 'canvas' }}
          notMerge
        />
      </div>
      <details>
        <summary className="cursor-pointer select-none text-[11px] text-slate-400 transition-colors hover:text-slate-600">
          原始返回值（JSON）
        </summary>
        <pre className="mt-1.5 max-h-48 overflow-auto rounded-lg bg-slate-50 p-2.5 text-xs leading-5 text-slate-700">
          {JSON.stringify(payload, null, 2)}
        </pre>
      </details>
    </div>
  )
}
