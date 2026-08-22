/**
 * PipelineOverviewBar — 数据流水线列表页头部运行概况。
 *
 * 布局对齐同域「数据任务池」页（SyncTasksTab）：单行五张紧凑 KPI 统计卡
 * （grid-cols-2 → sm:3 → xl:5），KpiCard 样式与任务池逐类对齐；所有视口
 * 宽度下均保持此单一形态（不再随 ≥2xl 断点切换右侧栏布局），表格始终
 * 全宽展示。
 *
 * 数据来自列表接口 paginated 响应里的 overview 字段（全量口径、不受筛选
 * 影响，见 backend pipeline_overview）。
 */
import type { ReactNode } from 'react'
import { GitBranch, CheckCircle2, Activity, AlertCircle, Waves } from 'lucide-react'
import type { PipelineOverview } from '@/api/v2/pipelines'

/** 头部 KPI 行：单行五张紧凑统计卡，样式对齐数据任务池页。 */
export default function PipelineOverviewBar({ overview }: { overview: PipelineOverview }) {
  const source = overview.trend_7d ?? []
  const successTotal = source.reduce((sum, item) => sum + Math.max(item.runs - item.errors, 0), 0)
  const failureTotal = source.reduce((sum, item) => sum + Math.min(Math.max(item.errors, 0), Math.max(item.runs, 0)), 0)
  const total7d = successTotal + failureTotal
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
        <KpiCard label="近7日执行" value={total7d} note={`成功 ${successTotal} · 失败 ${failureTotal}`} icon={<Waves size={13} />} tone="cyan" />
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
