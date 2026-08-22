/**
 * PipelineOverviewBar — 数据流水线列表页头部运行概况。
 *
 * 布局对齐同域「数据任务池」页（SyncTasksTab）：单行五张紧凑 KPI 统计卡
 * （grid-cols-2 → sm:3 → xl:5），KpiCard 样式与任务池逐类对齐；近 7 日执行
 * 堆叠趋势图独立成 PipelineTrendCard，由列表页放进内容区右侧栏（仅 ≥2xl
 * 展示，行为同任务池侧栏图表卡）。
 *
 * 数据来自列表接口 paginated 响应里的 overview 字段（全量口径、不受筛选
 * 影响，见 backend pipeline_overview）；echarts 为项目既有依赖。
 */
import { useMemo, type ReactNode } from 'react'
import ReactECharts from 'echarts-for-react'
import { GitBranch, CheckCircle2, Activity, AlertCircle, Waves } from 'lucide-react'
import type { PipelineOverview } from '@/api/v2/pipelines'

function useTrendData(overview: PipelineOverview) {
  return useMemo(() => {
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
}

/** 头部 KPI 行：单行五张紧凑统计卡，样式对齐数据任务池页。 */
export default function PipelineOverviewBar({ overview }: { overview: PipelineOverview }) {
  const trendData = useTrendData(overview)
  const hasTrend = Array.isArray(overview.trend_7d)

  return (
    <div
      data-testid="pipeline-overview-bar"
      className={`grid shrink-0 grid-cols-2 gap-2 sm:grid-cols-3 ${hasTrend ? 'xl:grid-cols-5' : 'xl:grid-cols-4'}`}
    >
      <KpiCard label="流水线总数" value={overview.total} note="不含已归档" icon={<GitBranch size={13} />} tone="slate" />
      <KpiCard label="已发布" value={overview.published} note="契约封版可挂接任务" icon={<CheckCircle2 size={13} />} tone="emerald" />
      <KpiCard label="已启用" value={overview.enabled} note="可被任务池调度" icon={<Activity size={13} />} tone="teal" />
      <KpiCard label="最近执行失败" value={overview.latest_failed} note="按各流水线最近一次运行" icon={<AlertCircle size={13} />} tone="rose" pulse={overview.latest_failed > 0} />
      {hasTrend && (
        <KpiCard label="近7日执行" value={trendData.total7d} note={`成功 ${trendData.successTotal} · 失败 ${trendData.failureTotal}`} icon={<Waves size={13} />} tone="cyan" />
      )}
    </div>
  )
}

/** 近 7 日执行堆叠趋势卡：由列表页放入内容区右侧栏（≥2xl），样式对齐任务池侧栏图表卡。 */
export function PipelineTrendCard({ overview }: { overview: PipelineOverview }) {
  const trendData = useTrendData(overview)

  // 与任务池页一致的近 7 日成功/失败堆叠趋势，每根柱体高度表示当日总执行次数
  const trendOption = useMemo(() => ({
    grid: { left: 24, right: 6, top: 14, bottom: 24 },
    xAxis: {
      type: 'category', data: trendData.days, boundaryGap: true,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: '#94A3B8', fontSize: 9, margin: 8 },
    },
    yAxis: {
      type: 'value', minInterval: 1,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: '#CBD5E1', fontSize: 9 },
      splitLine: { lineStyle: { color: '#F1F5F9' } },
    },
    tooltip: { trigger: 'axis', confine: true },
    series: [
      {
        name: '成功', type: 'bar', stack: 'executions', data: trendData.successes, barMaxWidth: 18,
        itemStyle: { color: '#10B981', borderRadius: [4, 4, 0, 0] },
        emphasis: { focus: 'series' },
      },
      {
        name: '失败', type: 'bar', stack: 'executions', data: trendData.failures, barMaxWidth: 18,
        itemStyle: { color: '#F87171', borderRadius: [4, 4, 0, 0] },
        emphasis: { focus: 'series' },
      },
    ],
  }), [trendData])

  return (
    <div data-testid="pipeline-trend-card" className="flex h-[clamp(208px,22vh,240px)] shrink-0 flex-col rounded-xl border border-slate-200 bg-white p-4 shadow-sm/50">
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
      {/* 零数据时保留坐标轴框架并叠加空态文案 */}
      <div className="relative min-h-0 flex-1 overflow-hidden">
        <ReactECharts option={trendOption} style={{ height: '100%', width: '100%' }} opts={{ renderer: 'svg' }} notMerge />
        {trendData.total7d === 0 && (
          <span className="pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2 text-center text-[11px] text-slate-300">
            近 7 日暂无流水线执行记录
          </span>
        )}
      </div>
    </div>
  )
}

function KpiCard({
  label, value, note, icon, tone, pulse,
}: {
  label: string
  value: number | string
  note: string
  icon: ReactNode
  tone: 'slate' | 'rose' | 'emerald' | 'teal' | 'cyan'
  pulse?: boolean
}) {
  const toneMap = {
    slate:   { text: 'text-slate-900',   iconBg: 'bg-slate-100 text-slate-500' },
    rose:    { text: 'text-rose-600',    iconBg: 'bg-rose-50 text-rose-500' },
    emerald: { text: 'text-emerald-600', iconBg: 'bg-emerald-50 text-emerald-500' },
    teal:    { text: 'text-teal-700',    iconBg: 'bg-teal-50 text-teal-600' },
    cyan:    { text: 'text-cyan-600',    iconBg: 'bg-cyan-50 text-cyan-500' },
  }[tone]
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm/50">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-[11px] font-medium text-slate-500">{label}</span>
        <span className={`relative grid h-6 w-6 shrink-0 place-items-center rounded-md ${toneMap.iconBg}`}>
          {icon}
          {pulse && (
            <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 animate-ping rounded-full bg-current opacity-60" />
          )}
        </span>
      </div>
      <p className={`mt-0.5 text-xl font-semibold leading-none tracking-tight tabular-nums ${toneMap.text}`}>{value}</p>
      <p className="mt-1 truncate text-[10px] text-slate-400" title={note}>{note}</p>
    </div>
  )
}
