import { useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'

interface RuntimeDay {
  date: string
  firings: { fired: number; error: number }
  actionRuns: { success: number; failed: number }
}

const formatDay = (date: string) => {
  const value = new Date(`${date}T00:00:00`)
  if (Number.isNaN(value.getTime())) return date
  return value.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

const SERIES = [
  { name: '哨兵命中', stack: 'sentinel', color: '#0ba28d', pick: (day: RuntimeDay) => day.firings.fired },
  { name: '哨兵错误', stack: 'sentinel', color: '#ec7474', pick: (day: RuntimeDay) => day.firings.error },
  { name: '动作成功', stack: 'action', color: '#3478ee', pick: (day: RuntimeDay) => day.actionRuns.success },
  { name: '动作失败', stack: 'action', color: '#e6a148', pick: (day: RuntimeDay) => day.actionRuns.failed },
] as const

export default function RuntimeTrendChart({ days, rangeLabel = '所选时段' }: {
  days: RuntimeDay[]
  rangeLabel?: string
}) {
  const option = useMemo<EChartsOption>(() => ({
    aria: {
      enabled: true,
      description: `${rangeLabel}每日运行趋势：哨兵命中与错误、动作成功与失败按日堆叠展示`,
    },
    animationDuration: 550,
    animationEasing: 'cubicOut',
    animationDelay: index => index * 45,
    grid: { left: 4, right: 10, top: 30, bottom: 2, containLabel: true },
    legend: {
      top: 0,
      right: 0,
      icon: 'roundRect',
      itemWidth: 8,
      itemHeight: 8,
      itemGap: 12,
      textStyle: { color: '#7d899a', fontSize: 10 },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      borderColor: '#dfe6ed',
      textStyle: { color: '#39475c', fontSize: 12 },
    },
    xAxis: {
      type: 'category',
      data: days.map(day => formatDay(day.date)),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#d7e0e8' } },
      axisLabel: { color: '#788699', fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: '#e9eef2', type: 'dashed' } },
      axisLabel: { color: '#98a2b1', fontSize: 10 },
    },
    series: SERIES.map(def => ({
      name: def.name,
      type: 'bar' as const,
      stack: def.stack,
      barMaxWidth: 15,
      itemStyle: { color: def.color, borderRadius: [2, 2, 0, 0] as number[] },
      emphasis: { focus: 'series' as const },
      data: days.map(day => def.pick(day)),
    })),
  }), [days, rangeLabel])

  return (
    <ReactECharts
      option={option}
      style={{ width: '100%', height: '100%' }}
      opts={{ renderer: 'canvas' }}
      notMerge
    />
  )
}
