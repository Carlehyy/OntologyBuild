import { useEffect, useMemo, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { eventsApi } from '../../api/events'
import type { EventItem, EventStats } from '../../api/events'
import { Search, Plus, RefreshCcw, Activity, Code2, AlertOctagon, ChevronLeft, ChevronRight, Filter, PlusCircle, ArrowUpRight, Archive, ArchiveRestore, Paperclip, Pencil, Trash2 } from 'lucide-react'
import { ConfirmModal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import EventFormModal from './EventFormModal'
import EventAttachmentsModal from './EventAttachmentsModal'
import IngestKeysDrawer from './IngestKeysDrawer'

// 与「数据资产湖」一致的基础面板：白底、细边框、轻阴影。
const PANEL = 'rounded-xl border border-slate-200 bg-white shadow-sm/50'
const PALETTE = {
  blue: '#3B82F6', teal: '#5EEAD4', gold: '#FCD34D', orange: '#FDBA74',
  red: '#FB7185',
}
const PAGE_SIZE = 8
const STATUS_TABS = [
  { value: 'active', label: '活跃' },
  { value: 'archived', label: '已归档' },
  { value: 'all', label: '全部' },
] as const

function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// ─── 数据 hooks ──────────────────────────────────────────
function useStats() { return useQuery({ queryKey: ['events', 'stats'], queryFn: () => eventsApi.stats() }) }
function useList(params: { page: number; pageSize: number; q?: string; sourceType?: string; severity?: string; status?: string }) {
  return useQuery({ queryKey: ['events', 'list', params], queryFn: () => eventsApi.list(params) })
}

export default function EventRegistryPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [search, setSearch] = useState('')
  const [sourceType, setSourceType] = useState<string>('')
  const [severity, setSeverity] = useState<string>('')
  const [status, setStatus] = useState<string>('active')
  const [page, setPage] = useState(1)
  const [formOpen, setFormOpen] = useState(false)
  const [keysOpen, setKeysOpen] = useState(false)
  const [editing, setEditing] = useState<EventItem | null>(null)
  const [attachmentEventId, setAttachmentEventId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<EventItem | null>(null)
  const statusTabsRef = useRef<HTMLDivElement>(null)
  const [statusIndicator, setStatusIndicator] = useState({ left: 0, width: 0 })

  const statsQ = useStats()
  const stats: EventStats | undefined = statsQ.data
  const listQ = useList({
    page, pageSize: PAGE_SIZE,
    q: search || undefined,
    sourceType: sourceType || undefined,
    severity: severity || undefined,
    status: status || 'active',
  })

  useEffect(() => { setPage(1) }, [search, sourceType, severity, status])
  useEffect(() => {
    const container = statusTabsRef.current
    if (!container) return
    const updateIndicator = () => {
      const activeButton = container.querySelector(`[data-status-value="${status}"]`) as HTMLElement | null
      if (!activeButton) return
      const containerRect = container.getBoundingClientRect()
      const buttonRect = activeButton.getBoundingClientRect()
      setStatusIndicator({
        left: buttonRect.left - containerRect.left,
        width: buttonRect.width,
      })
    }
    updateIndicator()
    const resizeObserver = new ResizeObserver(updateIndicator)
    resizeObserver.observe(container)
    return () => resizeObserver.disconnect()
  }, [status])

  const totalPages = Math.max(1, Math.ceil((listQ.data?.total ?? 0) / PAGE_SIZE))
  const refresh = () => { statsQ.refetch(); listQ.refetch() }

  const statusMutation = useMutation({
    mutationFn: ({ id, status: nextStatus }: { id: string; status: 'active' | 'archived' }) =>
      eventsApi.changeStatus(id, nextStatus),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['events'] })
      toast({ tone: 'success', title: variables.status === 'archived' ? '事件已归档' : '事件已恢复' })
    },
    onError: (cause: any) => toast({
      tone: 'error',
      title: '事件状态更新失败',
      description: cause?.detail || cause?.message || '请稍后重试',
    }),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => eventsApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['events'] })
      setDeleteTarget(null)
      toast({ tone: 'success', title: '事件已删除' })
    },
    onError: (cause: any) => toast({
      tone: 'error',
      title: '事件删除失败',
      description: cause?.detail || cause?.message || '物理删除仅管理员可执行',
    }),
  })

  const apiCoverage = useMemo(() => {
    const total = stats?.total ?? 0
    return total ? Math.round((stats?.api ?? 0) / total * 100) : 0
  }, [stats])

  // 级别分布环形图
  const severityOption = useMemo(() => {
    const order = ['critical', 'high', 'medium', 'low', 'info'] as const
    const labels: Record<string, string> = { critical: '严重', high: '高级', medium: '中级', low: '低级', info: '信息' }
    const colors = [PALETTE.red, PALETTE.orange, PALETTE.gold, PALETTE.teal, PALETTE.blue]
    const counts = order.map(key => stats?.bySeverity?.[key] ?? 0)
    const total = counts.reduce((sum, value) => sum + value, 0)
    const data = order.map((key, index) => ({
      name: labels[key],
      value: counts[index],
      itemStyle: { color: colors[index] },
    }))
    return {
      animationDuration: 600,
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(255,255,255,0.96)', borderColor: 'rgba(148,163,184,0.2)', borderWidth: 1,
        textStyle: { color: '#475569', fontSize: 12 },
        extraCssText: 'backdrop-filter:blur(12px);border-radius:8px;box-shadow:0 4px 16px rgba(15,23,42,0.08);',
        formatter: '{b}: {c} ({d}%)',
      },
      series: [{
        name: '级别', type: 'pie', radius: ['62%', '86%'], center: ['50%', '50%'],
        itemStyle: { borderColor: '#fff', borderWidth: 1 },
        label: { show: false }, labelLine: { show: false },
        emphasis: { scale: true, scaleSize: 3 },
        data,
      }],
      _centerTotal: total,
    }
  }, [stats])

  // 7 日趋势：直接展示后端按上海自然日聚合的真实登记数据。
  const trendOption = useMemo(() => {
    const trend = stats?.trend7d ?? []
    const days = trend.map(item => {
      const [, month = '', day = ''] = item.date.split('-')
      return `${Number(month)}/${Number(day)}`
    })
    const values = (severity: string) => trend.map(item => item.bySeverity?.[severity] ?? 0)
    return {
      animationDuration: 800,
      grid: { top: 22, right: 12, bottom: 24, left: 32, containLabel: false },
      tooltip: {
        trigger: 'axis',
        confine: true,
        padding: [6, 8],
        backgroundColor: 'rgba(255,255,255,0.96)', borderColor: 'rgba(148,163,184,0.2)', borderWidth: 1,
        textStyle: { color: '#475569', fontSize: 11, lineHeight: 14 },
        extraCssText: 'backdrop-filter:blur(12px);border-radius:8px;box-shadow:0 4px 16px rgba(15,23,42,0.08);',
        axisPointer: { type: 'line', lineStyle: { color: 'rgba(148,163,184,0.3)', type: 'dashed' } },
        formatter: (rawParams: unknown) => {
          const params = (Array.isArray(rawParams) ? rawParams : [rawParams]) as Array<{
            axisValueLabel?: string
            marker?: string
            seriesName?: string
            value?: number | string
          }>
          const rows = params.map(item => `
            <div style="display:flex;align-items:center;gap:3px;min-width:58px">
              ${item.marker ?? ''}<span>${item.seriesName ?? ''}</span>
              <strong style="margin-left:auto;color:#334155">${item.value ?? 0}</strong>
            </div>
          `).join('')
          return `
            <div style="min-width:150px">
              <div style="margin-bottom:3px;font-weight:600;color:#334155">${params[0]?.axisValueLabel ?? ''}</div>
              <div style="display:grid;grid-template-columns:repeat(2,minmax(58px,1fr));gap:2px 10px">${rows}</div>
            </div>
          `
        },
      },
      xAxis: {
        type: 'category', data: days, boundaryGap: false,
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.2)' } },
        axisTick: { show: false },
        axisLabel: { color: '#94A3B8', fontSize: 11 },
      },
      yAxis: {
        type: 'value',
        axisLine: { show: false }, axisTick: { show: false },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.12)', type: 'dashed' } },
        axisLabel: { color: '#94A3B8', fontSize: 11 },
      },
      series: [
        { name: '严重', type: 'line', stack: 'total', smooth: true, showSymbol: false, data: values('critical'), itemStyle: { color: PALETTE.red }, areaStyle: { color: PALETTE.red, opacity: 0.25 } },
        { name: '高级', type: 'line', stack: 'total', smooth: true, showSymbol: false, data: values('high'), itemStyle: { color: PALETTE.orange }, areaStyle: { color: PALETTE.orange, opacity: 0.25 } },
        { name: '中级', type: 'line', stack: 'total', smooth: true, showSymbol: false, data: values('medium'), itemStyle: { color: PALETTE.gold }, areaStyle: { color: PALETTE.gold, opacity: 0.25 } },
        { name: '低级', type: 'line', stack: 'total', smooth: true, showSymbol: false, data: values('low'), itemStyle: { color: PALETTE.teal }, areaStyle: { color: PALETTE.teal, opacity: 0.25 } },
        { name: '信息', type: 'line', stack: 'total', smooth: true, showSymbol: false, data: values('info'), itemStyle: { color: PALETTE.blue }, areaStyle: { color: PALETTE.blue, opacity: 0.25 } },
      ],
    }
  }, [stats])

  return (
    <div className="flex h-full flex-col gap-3 overflow-hidden bg-[var(--color-bg-base)] p-6">
      {/* 顶部仅保留操作，不重复展示侧边栏已有的页面名称。 */}
      <div className={`${PANEL} shrink-0 px-4 py-3`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div ref={statusTabsRef} className="relative flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 p-1 text-sm">
              <div
                aria-hidden="true"
                data-status-indicator
                className="pointer-events-none absolute top-1 h-[calc(100%-8px)] rounded-md bg-emerald-600 shadow-sm transition-all duration-300 ease-out"
                style={{ left: `${statusIndicator.left}px`, width: `${statusIndicator.width}px` }}
              />
              {STATUS_TABS.map(tab => (
                <button
                  key={tab.value}
                  type="button"
                  data-status-value={tab.value}
                  onClick={() => setStatus(tab.value)}
                  aria-pressed={status === tab.value}
                  className={`relative z-10 rounded-md px-4 py-2 font-medium transition-colors duration-200 ${status === tab.value ? 'text-white' : 'text-slate-500 hover:text-emerald-700'}`}
                >
                  {tab.value === 'archived' && <Archive className="mr-1 inline h-3.5 w-3.5 -translate-y-px" />}{tab.label}
                </button>
              ))}
          </div>

          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            <button type="button" onClick={() => setKeysOpen(true)}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50 hover:text-slate-800">
              <Code2 className="h-4 w-4" />接入管理
              <span className="ml-0.5 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700">API</span>
            </button>
            <button type="button" onClick={() => { setEditing(null); setFormOpen(true) }}
              className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-emerald-700 active:bg-emerald-800">
              <Plus className="h-4 w-4" />登记事件
            </button>
          </div>
        </div>
      </div>

      {/* 总览沿用数据资产湖的白色指标卡，并将图表收纳在同一行。 */}
      <div className="grid shrink-0 grid-cols-1 gap-3 lg:grid-cols-12">
        <div className="grid grid-cols-2 gap-2 lg:col-span-4">
          <MetricCard label="事件总数" value={stats?.total ?? 0} sub={`活跃 ${stats?.active ?? 0} · 归档 ${stats?.archived ?? 0}`} />
          <MetricCard label="平台录入" value={stats?.platform ?? 0} sub="人工登记" />
          <MetricCard label="API 接入" value={stats?.api ?? 0} sub={`${apiCoverage}% 覆盖率`} />
          <MetricCard label="今日新增" value={stats?.today ?? 0} sub="实时更新" />
        </div>

        {/* 级别分布环 */}
        <div className={`${PANEL} flex min-h-[156px] items-center gap-5 overflow-hidden px-5 py-3 lg:col-span-4 xl:col-span-3`}>
            <div className="relative h-[108px] w-[108px] shrink-0">
              <div className="w-full h-full overflow-hidden rounded-full">
                <ReactECharts option={severityOption} style={{ width: '100%', height: '100%' }} opts={{ renderer: 'canvas' }} notMerge />
              </div>
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                <span className="text-xl font-semibold text-slate-800 tabular-nums leading-none">{severityOption._centerTotal as number}</span>
                <span className="mt-1 text-xs text-slate-400">合计</span>
              </div>
            </div>
            <div className="grid min-w-0 flex-1 grid-cols-1 gap-y-1">
              {(['critical', 'high', 'medium', 'low', 'info'] as const).map((k, i) => {
                const colors = [PALETTE.red, PALETTE.orange, PALETTE.gold, PALETTE.teal, PALETTE.blue]
                const labels: Record<string, string> = { critical: '严重', high: '高级', medium: '中级', low: '低级', info: '信息' }
                const v = stats?.bySeverity?.[k] ?? 0
                return (
                  <div key={k} className="flex items-center gap-1.5 min-w-0">
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: colors[i], boxShadow: `0 0 5px ${colors[i]}66` }} />
                    <span className="truncate text-xs text-slate-500">{labels[k]}</span>
                    <span className="ml-auto text-sm font-semibold tabular-nums text-slate-700">{v}</span>
                  </div>
                )
              })}
            </div>
          </div>

        {/* 7日趋势 */}
        <div className={`${PANEL} flex min-h-[156px] min-w-0 flex-col overflow-hidden px-4 py-3 lg:col-span-4 xl:col-span-5`}>
            <div className="flex items-center justify-between mb-1 shrink-0">
              <div className="flex items-center gap-1.5">
                <span className="text-sm font-medium text-slate-700">近 7 日事件趋势</span>
                <span className="text-xs text-slate-400">按级别堆叠</span>
              </div>
              <div className="flex items-center gap-1.5 text-xs text-slate-400">
                <LegendDot color={PALETTE.blue} label="信息" />
                <LegendDot color={PALETTE.teal} label="低级" />
                <LegendDot color={PALETTE.gold} label="中级" />
                <LegendDot color={PALETTE.orange} label="高级" />
                <LegendDot color={PALETTE.red} label="严重" />
              </div>
            </div>
            <div className="min-h-0 w-full flex-1 overflow-hidden" style={{ height: 114 }}>
              <ReactECharts option={trendOption} style={{ width: '100%', height: '100%' }} opts={{ renderer: 'svg' }} notMerge />
            </div>
        </div>
      </div>

      {/* 筛选栏 */}
      <div className={`${PANEL} flex shrink-0 flex-wrap items-center gap-2 px-4 py-3`}>
          <div className="relative min-w-[220px] max-w-[340px] flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索事件标题、编号、上报人..."
              className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none transition focus:border-emerald-400 focus:ring-2 focus:ring-emerald-100 placeholder:text-slate-400" />
          </div>
          <Select value={severity} onChange={setSeverity} options={[
            { v: '', l: '全部级别' }, { v: 'critical', l: '严重' }, { v: 'high', l: '高级' }, { v: 'medium', l: '中级' }, { v: 'low', l: '低级' }, { v: 'info', l: '信息' },
          ]} />
          <Select value={sourceType} onChange={setSourceType} options={[
            { v: '', l: '全部来源' }, { v: 'platform', l: '平台录入' }, { v: 'api', l: 'API 上报' }, { v: 'system', l: '系统生成' },
          ]} />
          <div className="ml-auto flex items-center gap-1">
            <span className="mr-1 text-sm text-slate-400">共 <span className="font-semibold tabular-nums text-slate-700">{listQ.data?.total ?? 0}</span> 条</span>
            <button type="button" onClick={refresh}
              className="rounded-md p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800" title="刷新" aria-label="刷新事件列表">
              <RefreshCcw className={`h-4 w-4 ${statsQ.isFetching || listQ.isFetching ? 'animate-spin' : ''}`} />
            </button>
          </div>
      </div>

      {/* 表格区 */}
      <div className={`${PANEL} flex min-h-0 flex-1 flex-col overflow-hidden`}>
          <div className="flex-1 min-h-0 overflow-auto thin-scroll">
            <table className="w-full min-w-[960px] table-fixed text-sm">
              <thead>
                <tr className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50 text-sm text-slate-600">
                  <th className="w-[22%] px-4 py-3 text-center font-medium">事件</th>
                  <th className="w-[13%] px-3 py-3 text-center font-medium">来源</th>
                  <th className="w-[9%] px-3 py-3 text-center font-medium">级别</th>
                  <th className="w-[19%] px-3 py-3 text-center font-medium">描述</th>
                  <th className="w-[11%] px-3 py-3 text-center font-medium">附件</th>
                  <th className="w-[12%] px-3 py-3 text-center font-medium">发生时间</th>
                  <th className="w-[14%] px-2 py-3 text-center font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {listQ.isLoading ? (
                  <tr><td colSpan={7} className="py-16 text-center text-sm text-slate-400">加载中...</td></tr>
                ) : listQ.data?.items?.length === 0 ? (
                  <tr><td colSpan={7} className="text-center py-16">
                    <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-slate-100">
                      <Filter className="w-5 h-5 text-slate-300" />
                    </div>
                    <p className="text-sm text-slate-400">暂无匹配事件</p>
                    <p className="mt-1 text-xs text-slate-300">尝试调整筛选条件，或登记新事件</p>
                    <button onClick={() => { setEditing(null); setFormOpen(true) }}
                      className="mt-3 inline-flex items-center gap-1 rounded-lg bg-emerald-50 px-3 py-1.5 text-sm font-medium text-emerald-700 transition-colors hover:bg-emerald-100">
                      <PlusCircle className="h-3.5 w-3.5" />立即登记
                    </button>
                  </td></tr>
                ) : (listQ.data?.items ?? []).map((r, i) => (
                  <tr key={r.id}
                    className={`group border-t border-slate-100 transition-colors hover:bg-slate-50 ${(r.severity === 'critical' || r.severity === 'high') ? 'bg-red-50/20' : ''}`}
                    style={{ animation: `rowIn 0.35s ease-out ${i * 30}ms both` }}>
                    <td className="px-4 py-3 text-center align-middle">
                      <div className="flex items-stretch justify-center gap-2">
                        {(r.severity === 'critical' || r.severity === 'high') && (
                          <span className="w-1 self-stretch rounded-full shrink-0" style={{ background: r.severity === 'critical' ? PALETTE.red : PALETTE.orange }} />
                        )}
                        <div className="min-w-0">
                          <div className="flex items-center justify-center gap-1 truncate font-medium text-slate-800">
                            {r.title}
                            {r.severity === 'critical' && <AlertOctagon className="h-3.5 w-3.5 shrink-0 text-red-400" />}
                          </div>
                          <div className="mt-0.5 font-mono text-xs text-slate-400">{r.eventNo}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3 text-center align-middle">
                      <div className="flex justify-center"><SourceTag sourceType={r.sourceType} reporter={r.reporterName} sourceLabel={r.sourceLabel} /></div>
                    </td>
                    <td className="px-3 py-3 text-center align-middle"><SeverityBadge sev={r.severity} /></td>
                    <td className="max-w-0 px-3 py-3 text-center align-middle text-slate-500">
                      <div className="truncate" title={r.description || undefined}>{r.description || <span className="italic text-slate-300">无描述</span>}</div>
                    </td>
                    <td className="px-3 py-3 text-center align-middle">
                      {r.attachmentCount && r.attachmentCount > 0 ? (
                        <button
                          type="button"
                          onClick={() => setAttachmentEventId(r.id)}
                          className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-sm font-medium text-emerald-700 transition-colors hover:bg-emerald-50 hover:text-emerald-800"
                          title="点击查看附件清单"
                        >
                          <Paperclip size={14} /> {r.attachmentCount} 个附件
                        </button>
                      ) : <span className="text-sm text-slate-300">—</span>}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-center align-middle text-sm tabular-nums text-slate-500">{fmt(r.occurredAt)}</td>
                    <td className="px-2 py-3 text-center align-middle">
                      <div className="inline-flex items-center justify-center gap-1">
                        <button onClick={() => { setEditing(r); setFormOpen(true) }}
                          className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-emerald-50 hover:text-emerald-700" title="编辑事件" aria-label={`编辑事件 ${r.title}`}>
                          <Pencil size={15} />
                        </button>
                        <button
                          onClick={() => statusMutation.mutate({ id: r.id, status: r.status === 'archived' ? 'active' : 'archived' })}
                          disabled={statusMutation.isPending}
                          className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-amber-50 hover:text-amber-700 disabled:opacity-40"
                          title={r.status === 'archived' ? '恢复事件' : '归档事件'}
                          aria-label={`${r.status === 'archived' ? '恢复' : '归档'}事件 ${r.title}`}
                        >
                          {r.status === 'archived' ? <ArchiveRestore size={15} /> : <Archive size={15} />}
                        </button>
                        <button
                          onClick={() => setDeleteTarget(r)}
                          className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600"
                          title="删除事件（仅管理员）"
                          aria-label={`删除事件 ${r.title}`}
                        >
                          <Trash2 size={15} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 分页 */}
          <div className="flex shrink-0 items-center justify-between border-t border-slate-100 bg-white px-4 py-2">
            <div className="text-sm tabular-nums text-slate-400">
              显示 {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, listQ.data?.total ?? 0)} / {listQ.data?.total ?? 0}
            </div>
            <div className="flex items-center gap-1">
              <button disabled={page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}
                className="flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition-colors hover:bg-slate-50 disabled:opacity-40">
                <ChevronLeft className="w-3.5 h-3.5" />
              </button>
              {Array.from({ length: Math.min(totalPages, 5) }).map((_, i) => {
                let p = i + 1
                if (totalPages > 5) { if (page > 3) p = Math.min(totalPages - 4, page - 2) + i }
                return (
                  <button key={p} onClick={() => setPage(p)}
                    className={`flex h-8 w-8 items-center justify-center rounded-lg text-sm font-medium transition-colors ${p === page ? 'bg-emerald-600 text-white shadow-sm' : 'border border-slate-200 bg-white text-slate-500 hover:bg-slate-50'}`}>
                    {p}
                  </button>
                )
              })}
              <button disabled={page >= totalPages} onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                className="flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition-colors hover:bg-slate-50 disabled:opacity-40">
                <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
      </div>

      {/* 移动端 FAB */}
      <button onClick={() => { setEditing(null); setFormOpen(true) }}
          className="fixed bottom-6 right-6 z-20 flex h-11 w-11 items-center justify-center rounded-full bg-emerald-600 text-white shadow-lg transition-colors hover:bg-emerald-700 md:hidden">
          <PlusCircle className="w-5 h-5" />
      </button>

      <EventFormModal open={formOpen} onClose={() => { setFormOpen(false); setEditing(null) }} editing={editing} />
      <EventAttachmentsModal
        open={Boolean(attachmentEventId)}
        eventId={attachmentEventId}
        onClose={() => setAttachmentEventId(null)}
      />
      <IngestKeysDrawer open={keysOpen} onClose={() => setKeysOpen(false)} />
      <ConfirmModal
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => { if (deleteTarget) deleteMutation.mutate(deleteTarget.id) }}
        title="删除事件"
        description={deleteTarget ? `确认永久删除事件“${deleteTarget.title}”？附件和审计记录也会一并删除，此操作无法恢复。` : undefined}
        confirmText="确认删除"
        variant="danger"
        loading={deleteMutation.isPending}
      />

      <style>{`
        @keyframes rowIn {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .thin-scroll::-webkit-scrollbar { width: 5px; height: 5px; }
        .thin-scroll::-webkit-scrollbar-thumb { background: rgba(148,163,184,0.3); border-radius: 5px; }
        .thin-scroll::-webkit-scrollbar-thumb:hover { background: rgba(148,163,184,0.5); }
        .thin-scroll::-webkit-scrollbar-track { background: transparent; }
      `}</style>
    </div>
  )
}

// ─── 小型指标卡 ──────────────────────────────────────────
function MetricCard({ label, value, sub }: { label: string; value: number; sub?: string }) {
  return (
    <div className={`${PANEL} min-w-0 px-3 py-2.5`}>
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-0.5 text-2xl font-semibold tabular-nums text-slate-900">{value.toLocaleString()}</p>
      <p className="mt-0.5 truncate text-xs text-slate-400" title={sub}>{sub}</p>
    </div>
  )
}

// ─── 下拉选择 ────────────────────────────────────────────
function Select({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: { v: string; l: string }[] }) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      className="cursor-pointer appearance-none rounded-lg border border-slate-200 bg-white py-2 pl-3 pr-8 text-sm text-slate-600 outline-none transition focus:border-emerald-400 focus:ring-2 focus:ring-emerald-100"
      style={{ backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2.5' stroke-linecap='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E")`, backgroundRepeat: 'no-repeat', backgroundPosition: 'right 8px center' }}>
      {options.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
    </select>
  )
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return <span className="flex items-center gap-0.5"><span className="w-1.5 h-1.5 rounded-full" style={{ background: color }} />{label}</span>
}

// ─── 级别标签 ────────────────────────────────────────────
function SeverityBadge({ sev }: { sev: string }) {
  const map: Record<string, { bg: string; text: string; dot: string; label: string; glow: string }> = {
    critical: { bg: 'bg-red-50', text: 'text-red-600', dot: PALETTE.red, label: '严重', glow: 'rgba(251,113,133,0.4)' },
    high: { bg: 'bg-orange-50', text: 'text-orange-600', dot: PALETTE.orange, label: '高级', glow: 'rgba(253,186,116,0.4)' },
    medium: { bg: 'bg-amber-50', text: 'text-amber-600', dot: PALETTE.gold, label: '中级', glow: 'rgba(252,211,77,0.4)' },
    low: { bg: 'bg-teal-50', text: 'text-teal-600', dot: PALETTE.teal, label: '低级', glow: 'rgba(94,234,212,0.4)' },
    info: { bg: 'bg-blue-50', text: 'text-blue-600', dot: PALETTE.blue, label: '信息', glow: 'rgba(59,130,246,0.4)' },
  }
  const c = map[sev] ?? map.info
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-1 ${c.bg} ${c.text} text-xs font-medium whitespace-nowrap`}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: c.dot, boxShadow: `0 0 4px ${c.glow}` }} />{c.label}
    </span>
  )
}

// ─── 来源标签 ────────────────────────────────────────────
function SourceTag({ sourceType, reporter, sourceLabel }: { sourceType: string; reporter?: string | null; sourceLabel?: string | null }) {
  const name = reporter || sourceLabel || '—'
  const initial = name === '—' ? '?' : name.charAt(0).toUpperCase()
  if (sourceType === 'api') {
    return (
      <div className="flex items-center gap-1.5">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-emerald-50 text-emerald-600">
          <Code2 className="w-2.5 h-2.5" />
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-0.5 truncate text-sm text-slate-700">
            <span className="truncate max-w-[100px]">{name}</span>
            <ArrowUpRight className="w-2.5 h-2.5 text-slate-400 shrink-0" />
          </div>
          <div className="text-xs text-emerald-600">API 接入</div>
        </div>
      </div>
    )
  }
  if (sourceType === 'system') {
    return (
      <div className="flex items-center gap-1.5">
        <span className="w-5 h-5 rounded-md bg-slate-100 flex items-center justify-center text-slate-500 shrink-0">
          <Activity className="w-2.5 h-2.5" />
        </span>
        <div className="min-w-0">
          <div className="truncate text-sm text-slate-700">{name}</div>
          <div className="text-xs text-slate-400">系统生成</div>
        </div>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-1.5">
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-teal-100 text-xs font-semibold text-teal-700">{initial}</span>
      <div className="min-w-0">
        <div className="truncate text-sm text-slate-700">{name}</div>
        <div className="text-xs text-slate-400">平台录入</div>
      </div>
    </div>
  )
}
