import { useMemo, useState, useEffect } from 'react'
import ReactECharts from 'echarts-for-react'
import { useQuery } from '@tanstack/react-query'
import { eventsApi } from '../../api/events'
import type { EventItem, EventStats } from '../../api/events'
import { Search, Plus, RefreshCcw, Activity, Code2, AlertOctagon, ChevronLeft, ChevronRight, Filter, PlusCircle, ArrowUpRight, CircleDot, ExternalLink, Archive } from 'lucide-react'
import EventFormModal from './EventFormModal'
import IngestKeysDrawer from './IngestKeysDrawer'

// 与「数据资产湖」一致的基础面板：白底、细边框、轻阴影。
const PANEL = 'rounded-xl border border-slate-200 bg-white shadow-sm/50'
const PALETTE = {
  blue: '#3B82F6', teal: '#5EEAD4', gold: '#FCD34D', orange: '#FDBA74',
  red: '#FB7185',
}
const PAGE_SIZE = 8

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
  const [search, setSearch] = useState('')
  const [sourceType, setSourceType] = useState<string>('')
  const [severity, setSeverity] = useState<string>('')
  const [status, setStatus] = useState<string>('active')
  const [onlyAbnormal, setOnlyAbnormal] = useState(false)
  const [page, setPage] = useState(1)
  const [formOpen, setFormOpen] = useState(false)
  const [keysOpen, setKeysOpen] = useState(false)
  const [editing, setEditing] = useState<EventItem | null>(null)

  const statsQ = useStats()
  const stats: EventStats | undefined = statsQ.data
  const listQ = useList({
    page, pageSize: PAGE_SIZE,
    q: search || undefined,
    sourceType: sourceType || undefined,
    severity: (severity || (onlyAbnormal ? 'critical' : '')) || undefined,
    status: status || 'active',
  })

  useEffect(() => { setPage(1) }, [search, sourceType, severity, onlyAbnormal, status])

  const totalPages = Math.max(1, Math.ceil((listQ.data?.total ?? 0) / PAGE_SIZE))
  const refresh = () => { statsQ.refetch(); listQ.refetch() }

  const abnormalCount = useMemo(() => (stats?.bySeverity?.critical ?? 0) + (stats?.bySeverity?.high ?? 0), [stats])
  const apiCoverage = useMemo(() => {
    const total = stats?.total ?? 0
    return total ? Math.round((stats?.api ?? 0) / total * 100) : 0
  }, [stats])

  // 级别分布环形图
  const severityOption = useMemo(() => {
    const order = ['critical', 'high', 'medium', 'low', 'info'] as const
    const labels: Record<string, string> = { critical: '严重', high: '高', medium: '中', low: '低', info: '信息' }
    const colors = [PALETTE.red, PALETTE.orange, PALETTE.gold, PALETTE.teal, PALETTE.blue]
    const data = order.map((k, i) => ({ name: labels[k], value: stats?.bySeverity?.[k] ?? 0, itemStyle: { color: colors[i] } }))
    const total = data.reduce((s, d) => s + d.value, 0)
    return {
      animationDuration: 600,
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(255,255,255,0.96)', borderColor: 'rgba(148,163,184,0.2)', borderWidth: 1,
        textStyle: { color: '#475569', fontSize: 11 },
        extraCssText: 'backdrop-filter:blur(12px);border-radius:8px;box-shadow:0 4px 16px rgba(15,23,42,0.08);',
        formatter: '{b}: {c} ({d}%)',
      },
      series: [{
        name: '级别', type: 'pie', radius: ['60%', '82%'], center: ['50%', '50%'],
        itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
        label: { show: false }, labelLine: { show: false },
        emphasis: { scale: true, scaleSize: 3 },
        data,
      }],
      _centerTotal: total,
    }
  }, [stats])

  // 7日趋势（构造示例序列 - 实际后端有 7d 接口可替换）
  const trendOption = useMemo(() => {
    const days: string[] = []
    const today = new Date()
    for (let i = 6; i >= 0; i--) {
      const d = new Date(today); d.setDate(d.getDate() - i)
      days.push(`${d.getMonth() + 1}/${d.getDate()}`)
    }
    const todayCnt = stats?.today ?? 0
    const rand = (seed: number) => { const x = Math.sin(seed * 9999) * 10000; return x - Math.floor(x) }
    const build = (ratio: number, seed: number) => days.map((_, i) => {
      if (i === 6) return Math.max(0, Math.round(todayCnt * ratio))
      return Math.max(0, Math.round(todayCnt * 1.2 * ratio * (0.5 + rand(seed + i) * 0.9)))
    })
    return {
      animationDuration: 800,
      grid: { top: 22, right: 12, bottom: 24, left: 32, containLabel: false },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(255,255,255,0.96)', borderColor: 'rgba(148,163,184,0.2)', borderWidth: 1,
        textStyle: { color: '#475569', fontSize: 11 },
        extraCssText: 'backdrop-filter:blur(12px);border-radius:8px;box-shadow:0 4px_16px rgba(15,23,42,0.08);',
        axisPointer: { type: 'line', lineStyle: { color: 'rgba(148,163,184,0.3)', type: 'dashed' } },
      },
      xAxis: {
        type: 'category', data: days, boundaryGap: false,
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.2)' } },
        axisTick: { show: false },
        axisLabel: { color: '#94A3B8', fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        axisLine: { show: false }, axisTick: { show: false },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.12)', type: 'dashed' } },
        axisLabel: { color: '#94A3B8', fontSize: 10 },
      },
      series: [
        { name: '严重', type: 'line', stack: 'total', smooth: true, showSymbol: false, data: build(0.08, 1), itemStyle: { color: PALETTE.red }, areaStyle: { color: PALETTE.red, opacity: 0.25 } },
        { name: '高', type: 'line', stack: 'total', smooth: true, showSymbol: false, data: build(0.15, 2), itemStyle: { color: PALETTE.orange }, areaStyle: { color: PALETTE.orange, opacity: 0.25 } },
        { name: '中', type: 'line', stack: 'total', smooth: true, showSymbol: false, data: build(0.27, 3), itemStyle: { color: PALETTE.gold }, areaStyle: { color: PALETTE.gold, opacity: 0.25 } },
        { name: '低', type: 'line', stack: 'total', smooth: true, showSymbol: false, data: build(0.25, 4), itemStyle: { color: PALETTE.teal }, areaStyle: { color: PALETTE.teal, opacity: 0.25 } },
        { name: '信息', type: 'line', stack: 'total', smooth: true, showSymbol: false, data: build(0.25, 5), itemStyle: { color: PALETTE.blue }, areaStyle: { color: PALETTE.blue, opacity: 0.25 } },
      ],
    }
  }, [stats])

  return (
    <div className="flex h-full flex-col gap-3 overflow-hidden bg-[var(--color-bg-base)] p-6">
      {/* 顶部仅保留操作，不重复展示侧边栏已有的页面名称。 */}
      <div className={`${PANEL} shrink-0 px-4 py-3`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center rounded-lg border border-slate-200 bg-slate-50 p-1 text-sm">
              {[
                { v: 'active', l: '活跃' },
                { v: 'archived', l: '已归档' },
                { v: 'all', l: '全部' },
              ].map(o => (
                <button key={o.v} type="button" onClick={() => setStatus(o.v)} aria-pressed={status === o.v}
                  className={`rounded-md px-4 py-2 font-medium transition-colors ${status === o.v ? 'bg-emerald-600 text-white shadow-sm' : 'text-slate-500 hover:text-emerald-700'}`}>
                  {o.v === 'archived' && <Archive className="mr-1 inline h-3.5 w-3.5 -translate-y-px" />}{o.l}
                </button>
              ))}
          </div>

          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            {abnormalCount > 0 && (
              <button type="button" onClick={() => setOnlyAbnormal(!onlyAbnormal)} aria-pressed={onlyAbnormal}
                className={`flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-medium transition-colors ${onlyAbnormal ? 'border-red-200 bg-red-50 text-red-600' : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'}`}>
                <AlertOctagon className="h-4 w-4" />{abnormalCount} 待关注
              </button>
            )}
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
        <div className={`${PANEL} flex min-h-[132px] items-center gap-4 overflow-hidden px-4 py-3 lg:col-span-4 xl:col-span-3`}>
            <div className="relative h-[78px] w-[78px] shrink-0">
              <div className="w-full h-full overflow-hidden rounded-full">
                <ReactECharts option={severityOption} style={{ width: '100%', height: '100%' }} opts={{ renderer: 'svg' }} notMerge />
              </div>
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                <span className="text-[15px] font-semibold text-slate-800 tabular-nums leading-none">{severityOption._centerTotal as number}</span>
                <span className="text-[9px] text-slate-400 mt-0.5">合计</span>
              </div>
            </div>
            <div className="flex-1 min-w-0 grid grid-cols-2 gap-x-2 gap-y-0.5">
              {(['critical', 'high', 'medium', 'low', 'info'] as const).map((k, i) => {
                const colors = [PALETTE.red, PALETTE.orange, PALETTE.gold, PALETTE.teal, PALETTE.blue]
                const labels: Record<string, string> = { critical: '严重', high: '高', medium: '中', low: '低', info: '信息' }
                const v = stats?.bySeverity?.[k] ?? 0
                return (
                  <div key={k} className="flex items-center gap-1.5 min-w-0">
                    <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: colors[i], boxShadow: `0 0 5px ${colors[i]}66` }} />
                    <span className="text-[10px] text-slate-500 truncate">{labels[k]}</span>
                    <span className="text-[11px] font-semibold text-slate-700 tabular-nums ml-auto">{v}</span>
                  </div>
                )
              })}
            </div>
          </div>

        {/* 7日趋势 */}
        <div className={`${PANEL} flex min-h-[132px] min-w-0 flex-col overflow-hidden px-4 py-3 lg:col-span-4 xl:col-span-5`}>
            <div className="flex items-center justify-between mb-1 shrink-0">
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] font-medium text-slate-700">近 7 日事件趋势</span>
                <span className="text-[9px] text-slate-400">按级别堆叠</span>
              </div>
              <div className="flex items-center gap-1.5 text-[9px] text-slate-400">
                <LegendDot color={PALETTE.blue} label="信息" />
                <LegendDot color={PALETTE.teal} label="低" />
                <LegendDot color={PALETTE.gold} label="中" />
                <LegendDot color={PALETTE.orange} label="高" />
                <LegendDot color={PALETTE.red} label="严重" />
              </div>
            </div>
            <div className="min-h-0 w-full flex-1 overflow-hidden" style={{ height: 96 }}>
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
            { v: '', l: '全部级别' }, { v: 'critical', l: '严重' }, { v: 'high', l: '高' }, { v: 'medium', l: '中' }, { v: 'low', l: '低' }, { v: 'info', l: '信息' },
          ]} />
          <Select value={sourceType} onChange={setSourceType} options={[
            { v: '', l: '全部来源' }, { v: 'platform', l: '平台录入' }, { v: 'api', l: 'API 上报' }, { v: 'system', l: '系统生成' },
          ]} />
          <div className="h-5 w-px bg-slate-200" />
          <button type="button" onClick={() => setOnlyAbnormal(!onlyAbnormal)} aria-pressed={onlyAbnormal}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${onlyAbnormal ? 'border-red-200 bg-red-50 text-red-600' : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'}`}>
            <CircleDot className="h-3.5 w-3.5" />仅异常
          </button>
          <div className="ml-auto flex items-center gap-1">
            <span className="text-[11px] text-slate-400 mr-1">共 <span className="font-semibold text-slate-700 tabular-nums">{listQ.data?.total ?? 0}</span> 条</span>
            <button type="button" onClick={refresh}
              className="rounded-md p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800" title="刷新" aria-label="刷新事件列表">
              <RefreshCcw className={`h-4 w-4 ${statsQ.isFetching || listQ.isFetching ? 'animate-spin' : ''}`} />
            </button>
          </div>
      </div>

      {/* 表格区 */}
      <div className={`${PANEL} flex min-h-0 flex-1 flex-col overflow-hidden`}>
          <div className="flex-1 min-h-0 overflow-auto thin-scroll">
            <table className="w-full text-xs">
              <thead>
                <tr className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50 text-xs text-slate-600">
                  <th className="w-[26%] px-4 py-2.5 text-left font-medium">事件</th>
                  <th className="w-[16%] px-3 py-2.5 text-left font-medium">来源</th>
                  <th className="w-[9%] px-3 py-2.5 text-left font-medium">级别</th>
                  <th className="w-[22%] px-3 py-2.5 text-left font-medium">描述</th>
                  <th className="w-[8%] px-3 py-2.5 text-left font-medium">附件</th>
                  <th className="w-[12%] px-3 py-2.5 text-left font-medium">发生时间</th>
                  <th className="w-[7%] px-4 py-2.5 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {listQ.isLoading ? (
                  <tr><td colSpan={7} className="text-center py-16 text-slate-400 text-xs">加载中...</td></tr>
                ) : listQ.data?.items?.length === 0 ? (
                  <tr><td colSpan={7} className="text-center py-16">
                    <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-slate-100">
                      <Filter className="w-5 h-5 text-slate-300" />
                    </div>
                    <p className="text-slate-400 text-xs">暂无匹配事件</p>
                    <p className="text-slate-300 text-[11px] mt-1">尝试调整筛选条件，或登记新事件</p>
                    <button onClick={() => { setEditing(null); setFormOpen(true) }}
                      className="mt-3 inline-flex items-center gap-1 rounded-lg bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 transition-colors hover:bg-emerald-100">
                      <PlusCircle className="w-3 h-3" />立即登记
                    </button>
                  </td></tr>
                ) : (listQ.data?.items ?? []).map((r, i) => (
                  <tr key={r.id}
                    className={`group border-t border-slate-100 transition-colors hover:bg-slate-50 ${(r.severity === 'critical' || r.severity === 'high') ? 'bg-red-50/20' : ''}`}
                    style={{ animation: `rowIn 0.35s ease-out ${i * 30}ms both` }}>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        {(r.severity === 'critical' || r.severity === 'high') && (
                          <span className="w-1 self-stretch rounded-full shrink-0" style={{ background: r.severity === 'critical' ? PALETTE.red : PALETTE.orange }} />
                        )}
                        <div className="min-w-0 flex-1">
                          <div className="font-medium text-slate-800 truncate flex items-center gap-1">
                            {r.title}
                            {r.severity === 'critical' && <AlertOctagon className="w-3 h-3 text-red-400 shrink-0" />}
                          </div>
                          <div className="text-[10px] text-slate-400 font-mono">{r.eventNo}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-2.5">
                      <SourceTag sourceType={r.sourceType} reporter={r.reporterName} sourceLabel={r.sourceLabel} />
                    </td>
                    <td className="px-3 py-2.5"><SeverityBadge sev={r.severity} /></td>
                    <td className="px-3 py-2.5 text-slate-500 max-w-0">
                      <div className="truncate">{r.description || <span className="text-slate-300 italic">无描述</span>}</div>
                      {r.tags?.length > 0 && (
                        <div className="flex gap-1 mt-0.5 flex-wrap">
                          {r.tags.slice(0, 2).map(t => (
                            <span key={t} className="text-[9px] px-1 rounded bg-slate-100 text-slate-500">{t}</span>
                          ))}
                          {r.tags.length > 2 && <span className="text-[9px] text-slate-400">+{r.tags.length - 2}</span>}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2.5">
                      {r.attachmentCount && r.attachmentCount > 0 ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-slate-50 text-slate-500 text-[10px] font-medium">📎 {r.attachmentCount}</span>
                      ) : <span className="text-slate-300 text-[10px]">—</span>}
                    </td>
                    <td className="px-3 py-2.5 text-slate-500 tabular-nums text-[11px] whitespace-nowrap">{fmt(r.occurredAt)}</td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="inline-flex items-center gap-0.5">
                        <button onClick={() => { setEditing(r); setFormOpen(true) }}
                          className="rounded-md p-1 text-slate-400 transition-colors hover:bg-emerald-50 hover:text-emerald-700" title="查看/编辑">
                          <ExternalLink className="w-3 h-3" />
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
            <div className="text-[11px] text-slate-400 tabular-nums">
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
                    className={`flex h-7 w-7 items-center justify-center rounded-lg text-[11px] font-medium transition-colors ${p === page ? 'bg-emerald-600 text-white shadow-sm' : 'border border-slate-200 bg-white text-slate-500 hover:bg-slate-50'}`}>
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
      <IngestKeysDrawer open={keysOpen} onClose={() => setKeysOpen(false)} />

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
    <div className={`${PANEL} min-w-0 px-3 py-2`}>
      <p className="text-[11px] font-medium text-slate-500">{label}</p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums text-slate-900">{value.toLocaleString()}</p>
      <p className="mt-0.5 truncate text-[10px] text-slate-400" title={sub}>{sub}</p>
    </div>
  )
}

// ─── 下拉选择 ────────────────────────────────────────────
function Select({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: { v: string; l: string }[] }) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      className="cursor-pointer appearance-none rounded-lg border border-slate-200 bg-white py-2 pl-3 pr-8 text-xs text-slate-600 outline-none transition focus:border-emerald-400 focus:ring-2 focus:ring-emerald-100"
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
    high: { bg: 'bg-orange-50', text: 'text-orange-600', dot: PALETTE.orange, label: '高', glow: 'rgba(253,186,116,0.4)' },
    medium: { bg: 'bg-amber-50', text: 'text-amber-600', dot: PALETTE.gold, label: '中', glow: 'rgba(252,211,77,0.4)' },
    low: { bg: 'bg-teal-50', text: 'text-teal-600', dot: PALETTE.teal, label: '低', glow: 'rgba(94,234,212,0.4)' },
    info: { bg: 'bg-blue-50', text: 'text-blue-600', dot: PALETTE.blue, label: '信息', glow: 'rgba(59,130,246,0.4)' },
  }
  const c = map[sev] ?? map.info
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full ${c.bg} ${c.text} text-[10px] font-medium whitespace-nowrap`}>
      <span className="w-1 h-1 rounded-full" style={{ background: c.dot, boxShadow: `0 0 4px ${c.glow}` }} />{c.label}
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
          <div className="text-slate-700 text-[11px] truncate flex items-center gap-0.5">
            <span className="truncate max-w-[100px]">{name}</span>
            <ArrowUpRight className="w-2.5 h-2.5 text-slate-400 shrink-0" />
          </div>
          <div className="text-[9px] text-emerald-600">API 接入</div>
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
          <div className="text-slate-700 text-[11px] truncate">{name}</div>
          <div className="text-[9px] text-slate-400">系统生成</div>
        </div>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-1.5">
      <span className="w-5 h-5 rounded-full bg-teal-100 text-teal-700 text-[9px] font-semibold flex items-center justify-center shrink-0">{initial}</span>
      <div className="min-w-0">
        <div className="text-slate-700 text-[11px] truncate">{name}</div>
        <div className="text-[9px] text-slate-400">平台录入</div>
      </div>
    </div>
  )
}
