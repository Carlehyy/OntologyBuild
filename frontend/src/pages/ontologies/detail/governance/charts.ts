/* 治理页 echarts 纯 option 构建:与组件解耦,可在 node:test 中验证。
   - buildDailyComboOption:近 7 日执行心电图(时间柱状&折线组合,
     执行成功/失败堆叠柱 + 哨兵命中平滑面积线,借鉴 area-time-axis)
   - buildMiniBarOption / buildMiniLineOption:KPI 卡近 7 日迷你图(无轴火花线)
   - buildKpiSparkSeries:四个 KPI 卡的近 7 日序列装配(全部来自现有只读数据) */
import type { EChartsOption } from 'echarts'
import type { DailySparkDatum } from './storyModel.ts'

const formatDay = (date: string) => {
  const value = new Date(`${date}T00:00:00`)
  if (Number.isNaN(value.getTime())) return date
  return value.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

/** 近 7 日执行心电图:执行成功/失败堆叠柱 + 哨兵命中平滑面积线(时间轴)。 */
export function buildDailyComboOption(daily: DailySparkDatum[]): EChartsOption {
  const days = daily.map(day => formatDay(day.date))
  return {
    aria: {
      enabled: true,
      description: '近 7 日执行心电图:每日动作执行成功与失败堆叠柱状,哨兵命中折线面积图',
    },
    animationDuration: 500,
    animationEasing: 'cubicOut',
    grid: { left: 4, right: 8, top: 30, bottom: 2, containLabel: true },
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
      data: days,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: 'rgba(148,163,184,0.4)' } },
      axisLabel: { color: '#8b96a5', fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: 'rgba(148,163,184,0.25)', type: 'dashed' } },
      axisLabel: { color: '#98a2b1', fontSize: 10 },
    },
    series: [
      {
        name: '执行成功',
        type: 'bar',
        stack: 'run',
        barMaxWidth: 14,
        itemStyle: { color: '#10b981', borderRadius: [2, 2, 0, 0] },
        data: daily.map(day => day.runSuccess),
      },
      {
        name: '失败',
        type: 'bar',
        stack: 'run',
        barMaxWidth: 14,
        itemStyle: { color: '#f87171', borderRadius: [2, 2, 0, 0] },
        data: daily.map(day => day.runFailed),
      },
      {
        name: '哨兵命中',
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { color: '#f43f5e', width: 2 },
        itemStyle: { color: '#f43f5e' },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(244,63,94,0.18)' },
              { offset: 1, color: 'rgba(244,63,94,0.01)' },
            ],
          },
        },
        data: daily.map(day => day.fired + day.firedError),
      },
    ],
  }
}

/** KPI 卡迷你柱状火花线:无轴无网格,只看 7 天起伏。 */
export function buildMiniBarOption(values: number[], color: string): EChartsOption {
  return {
    animation: false,
    grid: { left: 0, right: 0, top: 2, bottom: 0 },
    xAxis: { type: 'category', show: false, data: values.map((_, index) => index) },
    yAxis: { type: 'value', show: false, min: 0 },
    tooltip: { show: false },
    series: [{
      type: 'bar',
      barMaxWidth: 8,
      itemStyle: { color, borderRadius: [1.5, 1.5, 0, 0], opacity: 0.85 },
      data: values,
    }],
  }
}

/** KPI 卡迷你折线火花线(批准率等比例类指标),空值断点不连。 */
export function buildMiniLineOption(values: Array<number | null>, color: string): EChartsOption {
  return {
    animation: false,
    grid: { left: 0, right: 0, top: 3, bottom: 1 },
    xAxis: { type: 'category', show: false, data: values.map((_, index) => index) },
    yAxis: { type: 'value', show: false, min: 0, max: 1 },
    tooltip: { show: false },
    series: [{
      type: 'line',
      smooth: true,
      symbol: 'none',
      connectNulls: false,
      lineStyle: { color, width: 1.6 },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: `${color}2e` },
            { offset: 1, color: `${color}05` },
          ],
        },
      },
      data: values,
    }],
  }
}

export interface KpiSparkSeries {
  /** 待审批卡:每日人工决策处理量(批准+拒绝) */
  decisions: number[]
  /** 决策批准率卡:每日批准率(无决策为 null,断点) */
  approvalRate: Array<number | null>
  /** 哨兵在线卡:每日哨兵命中 */
  sentinelHits: number[]
  /** 自治动作卡:每日动作执行成功 */
  actionSuccess: number[]
}

/** 四个 KPI 卡的近 7 日序列:心电图的 7 天为基准日轴,
   决策类序列从执行日志按日归桶(仅统计非 dryRun 日志;
   日期键与后端 UTC 口径一致,直接取 ISO 时间前 10 位)。 */
export function buildKpiSparkSeries(input: {
  daily7d: DailySparkDatum[]
  logs: Array<{
    status?: string | null
    executedAt?: string | null
    dryRun?: boolean | null
  }>
}): KpiSparkSeries {
  const dates = input.daily7d.map(day => day.date)
  const dayIndex = new Map(dates.map((date, index) => [date, index]))
  const approved = new Array(dates.length).fill(0) as number[]
  const rejected = new Array(dates.length).fill(0) as number[]
  for (const log of input.logs) {
    if (log.dryRun || !log.executedAt) continue
    const key = log.executedAt.slice(0, 10)
    const index = dayIndex.get(key)
    if (index === undefined) continue
    if (log.status === 'approved') approved[index] += 1
    else if (log.status === 'rejected') rejected[index] += 1
  }
  return {
    decisions: approved.map((count, index) => count + rejected[index]),
    approvalRate: approved.map((count, index) => {
      const total = count + rejected[index]
      return total > 0 ? count / total : null
    }),
    sentinelHits: input.daily7d.map(day => day.fired + day.firedError),
    actionSuccess: input.daily7d.map(day => day.runSuccess),
  }
}
