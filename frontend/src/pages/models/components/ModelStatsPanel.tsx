import { useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import type { DailyStats } from '../hooks/useMockModels'
import { TrendingUp, Activity, Zap, AlertTriangle } from 'lucide-react'

interface ModelStatsPanelProps {
  dailyStats: DailyStats[]
  modelName?: string
}

export default function ModelStatsPanel({ dailyStats, modelName }: ModelStatsPanelProps) {
  // 计算汇总指标
  const summary = useMemo(() => {
    const totalCalls = dailyStats.reduce((sum, d) => sum + d.callCount, 0)
    const totalSuccess = dailyStats.reduce((sum, d) => sum + d.successCount, 0)
    const totalErrors = dailyStats.reduce((sum, d) => sum + d.errorCount, 0)
    const avgLatency = dailyStats.length > 0
      ? Math.floor(dailyStats.reduce((sum, d) => sum + d.avgLatency, 0) / dailyStats.length)
      : 0
    const availability = totalCalls > 0 ? ((totalSuccess / totalCalls) * 100).toFixed(2) : '0.00'
    return { totalCalls, totalSuccess, totalErrors, avgLatency, availability }
  }, [dailyStats])

  // 可用率趋势图配置（参考 helpaio.com 的柱状图风格）
  const availabilityChartOption = useMemo(() => {
    const dates = dailyStats.map((d) => d.date.slice(5)) // MM-DD
    const rates = dailyStats.map((d) =>
      d.callCount > 0 ? parseFloat(((d.successCount / d.callCount) * 100).toFixed(2)) : 0
    )

    return {
      grid: { top: 30, right: 20, bottom: 30, left: 50 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisLabel: { color: '#94a3b8', fontSize: 11 },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        min: 80,
        max: 100,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#f1f5f9', type: 'dashed' as const } },
        axisLabel: { color: '#94a3b8', fontSize: 11, formatter: '{value}%' },
      },
      tooltip: {
        trigger: 'axis' as const,
        backgroundColor: 'rgba(255,255,255,0.96)',
        borderColor: '#e2e8f0',
        borderWidth: 1,
        textStyle: { color: '#334155', fontSize: 12 },
        formatter: (params: any) => {
          const p = params[0]
          return `<div style="font-weight:600;margin-bottom:4px">${p.name}</div>
                  <div style="display:flex;align-items:center;gap:6px">
                    <span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${p.color}"></span>
                    <span>可用率: <strong>${p.value}%</strong></span>
                  </div>`
        },
      },
      series: [
        {
          type: 'bar',
          data: rates.map((v) => ({
            value: v,
            itemStyle: {
              color: v >= 95 ? '#10b981' : v >= 90 ? '#f59e0b' : '#ef4444',
              borderRadius: [3, 3, 0, 0],
            },
          })),
          barWidth: '60%',
          emphasis: {
            itemStyle: {
              shadowBlur: 6,
              shadowColor: 'rgba(0,0,0,0.1)',
            },
          },
        },
      ],
      animationDuration: 800,
      animationEasing: 'cubicOut' as const,
    }
  }, [dailyStats])

  // 调用量趋势图
  const callVolumeChartOption = useMemo(() => {
    const dates = dailyStats.map((d) => d.date.slice(5))
    const successData = dailyStats.map((d) => d.successCount)
    const errorData = dailyStats.map((d) => d.errorCount)

    return {
      grid: { top: 30, right: 20, bottom: 30, left: 50 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisLabel: { color: '#94a3b8', fontSize: 11 },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#f1f5f9', type: 'dashed' as const } },
        axisLabel: { color: '#94a3b8', fontSize: 11 },
      },
      tooltip: {
        trigger: 'axis' as const,
        backgroundColor: 'rgba(255,255,255,0.96)',
        borderColor: '#e2e8f0',
        borderWidth: 1,
        textStyle: { color: '#334155', fontSize: 12 },
      },
      legend: {
        data: ['成功', '失败'],
        top: 0,
        right: 0,
        textStyle: { color: '#64748b', fontSize: 11 },
        itemWidth: 10,
        itemHeight: 6,
        itemGap: 16,
      },
      series: [
        {
          name: '成功',
          type: 'bar',
          stack: 'total',
          data: successData,
          itemStyle: { color: '#10b981', borderRadius: [0, 0, 0, 0] },
          barWidth: '60%',
        },
        {
          name: '失败',
          type: 'bar',
          stack: 'total',
          data: errorData,
          itemStyle: { color: '#ef4444', borderRadius: [3, 3, 0, 0] },
          barWidth: '60%',
        },
      ],
      animationDuration: 800,
      animationEasing: 'cubicOut' as const,
    }
  }, [dailyStats])

  // 延迟趋势图
  const latencyChartOption = useMemo(() => {
    const dates = dailyStats.map((d) => d.date.slice(5))
    const latencies = dailyStats.map((d) => d.avgLatency)

    return {
      grid: { top: 30, right: 20, bottom: 30, left: 60 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisLabel: { color: '#94a3b8', fontSize: 11 },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#f1f5f9', type: 'dashed' as const } },
        axisLabel: { color: '#94a3b8', fontSize: 11, formatter: '{value}ms' },
      },
      tooltip: {
        trigger: 'axis' as const,
        backgroundColor: 'rgba(255,255,255,0.96)',
        borderColor: '#e2e8f0',
        borderWidth: 1,
        textStyle: { color: '#334155', fontSize: 12 },
        formatter: (params: any) => {
          const p = params[0]
          return `<div style="font-weight:600;margin-bottom:4px">${p.name}</div>
                  <div>平均延迟: <strong>${p.value}ms</strong></div>`
        },
      },
      series: [
        {
          type: 'line',
          data: latencies,
          smooth: true,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: '#0c8aff', width: 2.5 },
          itemStyle: { color: '#0c8aff', borderWidth: 2, borderColor: '#fff' },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(12,138,255,0.15)' },
                { offset: 1, color: 'rgba(12,138,255,0.02)' },
              ],
            },
          },
        },
      ],
      animationDuration: 800,
      animationEasing: 'cubicOut' as const,
    }
  }, [dailyStats])

  const statCards = [
    {
      label: '总调用次数',
      value: summary.totalCalls.toLocaleString(),
      icon: Activity,
      color: 'text-blue-500',
      bg: 'bg-blue-50',
    },
    {
      label: '平均可用率',
      value: `${summary.availability}%`,
      icon: TrendingUp,
      color: summary.availability >= '95' ? 'text-emerald-500' : 'text-amber-500',
      bg: summary.availability >= '95' ? 'bg-emerald-50' : 'bg-amber-50',
    },
    {
      label: '平均延迟',
      value: `${summary.avgLatency}ms`,
      icon: Zap,
      color: summary.avgLatency < 2000 ? 'text-emerald-500' : summary.avgLatency < 4000 ? 'text-amber-500' : 'text-red-500',
      bg: summary.avgLatency < 2000 ? 'bg-emerald-50' : summary.avgLatency < 4000 ? 'bg-amber-50' : 'bg-red-50',
    },
    {
      label: '失败次数',
      value: summary.totalErrors.toLocaleString(),
      icon: AlertTriangle,
      color: 'text-red-500',
      bg: 'bg-red-50',
    },
  ]

  return (
    <div className="space-y-5">
      {/* 统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {statCards.map((card) => {
          const Icon = card.icon
          return (
            <div
              key={card.label}
              className="bg-white rounded-xl border border-slate-200 p-4 hover:shadow-md transition-shadow duration-200"
            >
              <div className="flex items-center gap-2 mb-2">
                <div className={`w-8 h-8 rounded-lg ${card.bg} flex items-center justify-center`}>
                  <Icon size={16} className={card.color} />
                </div>
                <span className="text-xs text-slate-500 font-medium">{card.label}</span>
              </div>
              <p className="text-xl font-bold text-slate-800 tracking-tight">{card.value}</p>
            </div>
          )
        })}
      </div>

      {/* 图表区域 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* 可用率趋势 */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 hover:shadow-md transition-shadow duration-200">
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-sm font-semibold text-slate-700">可用率趋势</h4>
            <span className="text-[10px] text-slate-400 bg-slate-50 px-2 py-0.5 rounded-full">30天</span>
          </div>
          <ReactECharts
            option={availabilityChartOption}
            style={{ height: 200 }}
            opts={{ renderer: 'canvas' }}
          />
        </div>

        {/* 调用量趋势 */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 hover:shadow-md transition-shadow duration-200">
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-sm font-semibold text-slate-700">调用量分布</h4>
            <span className="text-[10px] text-slate-400 bg-slate-50 px-2 py-0.5 rounded-full">30天</span>
          </div>
          <ReactECharts
            option={callVolumeChartOption}
            style={{ height: 200 }}
            opts={{ renderer: 'canvas' }}
          />
        </div>

        {/* 延迟趋势 */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 hover:shadow-md transition-shadow duration-200">
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-sm font-semibold text-slate-700">延迟趋势</h4>
            <span className="text-[10px] text-slate-400 bg-slate-50 px-2 py-0.5 rounded-full">30天</span>
          </div>
          <ReactECharts
            option={latencyChartOption}
            style={{ height: 200 }}
            opts={{ renderer: 'canvas' }}
          />
        </div>
      </div>
    </div>
  )
}
