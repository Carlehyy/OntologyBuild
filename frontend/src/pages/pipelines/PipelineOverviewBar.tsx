/**
 * PipelineOverviewBar — 数据流水线列表页头部运行概况。
 *
 * 数据来自列表接口 paginated 响应里的 overview 字段（全量口径、不受筛选
 * 影响，见 backend pipeline_overview）。布局对齐本体管理「治理推演」页的
 * KpiOverviewGrid：左侧 2×2 四张 KPI 统计卡，右侧一张加宽、通高的近 7 日
 * 执行堆叠趋势图；零数据时仍渲染坐标轴框架并叠加空态文案。echarts 为项目
 * 既有依赖。
 */
import { useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import { GitBranch, CheckCircle2, Activity, AlertCircle } from 'lucide-react'
import type { PipelineOverview } from '@/api/v2/pipelines'

export default function PipelineOverviewBar({ overview }: { overview: PipelineOverview }) {
  const trendData = useMemo(() => {
    const source = overview.trend_7d ?? []
    const days = source.map(item => {
      const [, month, day] = item.date.split('-')
      return `${Number(month)}/${Number(day)}`
    })
    const failures = source.map(item => Math.min(Math.max(item.errors, 0), Math.max(item.runs, 0)))
    const successes = source.map((item, index) => Math.max(item.runs - failures[index], 0))
    const successTotal = successes.reduce((a, b) => a + b, 0)
    const failureTotal = failures.reduce((a, b) => a + b, 0)
    return { days, successes, failures, successTotal, failureTotal, total7d: successTotal + failureTotal }
  }, [overview.trend_7d])

  // 近 7 日成功/失败堆叠趋势，每根柱体高度表示当日总执行次数
  const trendOption = useMemo(() => ({
    grid: { left: 30, right: 8, top: 18, bottom: 26 },
    xAxis: {
      type: 'category', data: trendData.days, boundaryGap: true,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: '#94A3B8', fontSize: 10, margin: 8 },
    },
    yAxis: {
      type: 'value', minInterval: 1,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: '#CBD5E1', fontSize: 10 },
      splitLine: { lineStyle: { color: '#F1F5F9' } },
    },
    tooltip: { trigger: 'axis', confine: true },
    series: [
      {
        name: '成功', type: 'bar', stack: 'executions', data: trendData.successes, barMaxWidth: 22,
        itemStyle: { color: '#10B981', borderRadius: [4, 4, 0, 0] },
        emphasis: { focus: 'series' },
      },
      {
        name: '失败', type: 'bar', stack: 'executions', data: trendData.failures, barMaxWidth: 22,
        itemStyle: { color: '#F87171', borderRadius: [4, 4, 0, 0] },
        emphasis: { focus: 'series' },
      },
    ],
  }), [trendData])

  const hasTrend = Array.isArray(overview.trend_7d)

  return (
    <div
      data-testid="pipeline-overview-bar"
      className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(430px,2fr)]"
    >
      {/* 左侧：四张 KPI 统计卡（2×2），口径与趋势图一致为全量 */}
      <div className="grid grid-cols-2 content-start gap-4">
        <KpiCard label="流水线总数" value={overview.total} note="不含已归档" icon={<GitBranch size={13} />} tone="slate" />
        <KpiCard label="已发布" value={overview.published} note="契约封版可挂接任务" icon={<CheckCircle2 size={13} />} tone="emerald" />
        <KpiCard label="已启用" value={overview.enabled} note="可被任务池调度" icon={<Activity size={13} />} tone="teal" />
        <KpiCard label="最近执行失败" value={overview.latest_failed} note="按各流水线最近一次运行" icon={<AlertCircle size={13} />} tone="rose" pulse={overview.latest_failed > 0} />
      </div>

      {/* 右侧：加宽通高的近 7 日执行趋势卡 */}
      {hasTrend && (
        <div
          data-testid="pipeline-trend-card"
          className="flex min-h-[208px] flex-col rounded-xl border border-slate-200 bg-white p-4 shadow-sm/50"
        >
          <div className="mb-1 flex shrink-0 items-center justify-between gap-3">
            <h3 className="flex items-center gap-2 text-xs font-semibold text-slate-700">
              <span className="h-1.5 w-1.5 rounded-full bg-teal-600" />
              近 7 日执行
            </h3>
            <div className="flex shrink-0 items-center gap-2.5 whitespace-nowrap text-[10px] text-slate-500" aria-label="近 7 日执行结果汇总">
              <span className="inline-flex items-center gap-1.5 tabular-nums">
                <span className="h-1.5 w-1.5 rounded-sm bg-emerald-500" aria-hidden="true" />
                成功 {trendData.successTotal}
              </span>
              <span className="inline-flex items-center gap-1.5 tabular-nums">
                <span className="h-1.5 w-1.5 rounded-sm bg-red-400" aria-hidden="true" />
                失败 {trendData.failureTotal}
              </span>
              <span className="h-3 w-px bg-slate-200" aria-hidden="true" />
              <span className="text-[11px] tabular-nums">{trendData.total7d} 次</span>
            </div>
          </div>
          {/* 零数据也保留坐标轴框架，空态文案叠加呈现（对齐治理推演心电图做法） */}
          <div className="relative min-h-0 flex-1">
            <ReactECharts option={trendOption} style={{ height: '100%', width: '100%' }} opts={{ renderer: 'svg' }} notMerge />
            {trendData.total7d === 0 && (
              <span className="pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2 text-center text-[11px] text-slate-300">
                近 7 日暂无流水线执行记录
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function KpiCard({
  label, value, note, icon, tone, pulse,
}: {
  label: string
  value: number | string
  note: string
  icon: React.ReactNode
  tone: 'slate' | 'rose' | 'emerald' | 'teal'
  pulse?: boolean
}) {
  const toneMap = {
    slate:   { text: 'text-slate-900',   iconBg: 'bg-slate-100 text-slate-500' },
    rose:    { text: 'text-rose-600',    iconBg: 'bg-rose-50 text-rose-500' },
    emerald: { text: 'text-emerald-600', iconBg: 'bg-emerald-50 text-emerald-500' },
    teal:    { text: 'text-teal-700',    iconBg: 'bg-teal-50 text-teal-600' },
  }[tone]
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm/50">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-slate-500">{label}</span>
        <span className={`relative grid h-6 w-6 shrink-0 place-items-center rounded-md ${toneMap.iconBg}`}>
          {icon}
          {pulse && <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 animate-pulse rounded-full bg-rose-500" />}
        </span>
      </div>
      <div className={`mt-1.5 text-xl font-semibold leading-none tabular-nums ${toneMap.text}`}>{value}</div>
      <div className="mt-1.5 truncate text-[11px] text-slate-400" title={note}>{note}</div>
    </div>
  )
}
