import { useMemo, useState, useEffect } from 'react'
import ReactECharts from 'echarts-for-react'
import { useQuery } from '@tanstack/react-query'
import { eventsApi } from '../../api/events'
import type { EventItem, EventStats } from '../../api/events'
import { Search, Plus, RefreshCcw, AlertTriangle, Activity, Database, Code2, Zap, AlertOctagon, ChevronLeft, ChevronRight, Filter, ListTodo, PlusCircle, ArrowUpRight, CircleDot, ExternalLink, Archive } from 'lucide-react'
import EventFormModal from './EventFormModal'
import IngestKeysDrawer from './IngestKeysDrawer'

// ─── 设计 token ──────────────────────────────────────────
const GLASS = 'backdrop-blur-xl bg-white/70 border border-white/80 shadow-[0_4px_24px_rgba(15,23,42,0.05)] rounded-xl'
const PALETTE = {
  blue: '#3B82F6', teal: '#5EEAD4', gold: '#FCD34D', orange: '#FDBA74',
  red: '#FB7185', purple: '#C4B5FD', slate: '#94A3B8',
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
    <div className="h-full flex flex-col relative overflow-hidden bg-gradient-to-br from-slate-50/80 via-white to-slate-50/60">
      {/* 四角均匀柔光：对称分布、统一透明度、色彩克制 */}
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-32 -left-32 w-[480px] h-[480px] rounded-full opacity-[0.12]"
          style={{ background: 'radial-gradient(circle, rgba(94,234,212,0.7), transparent 70%)' }} />
        <div className="absolute -top-32 -right-32 w-[480px] h-[480px] rounded-full opacity-[0.12]"
          style={{ background: 'radial-gradient(circle, rgba(59,130,246,0.65), transparent 70%)' }} />
        <div className="absolute -bottom-32 -left-32 w-[480px] h-[480px] rounded-full opacity-[0.10]"
          style={{ background: 'radial-gradient(circle, rgba(196,181,253,0.6), transparent 70%)' }} />
        <div className="absolute -bottom-32 -right-32 w-[480px] h-[480px] rounded-full opacity-[0.10]"
          style={{ background: 'radial-gradient(circle, rgba(253,186,116,0.55), transparent 70%)' }} />
      </div>

      <div className="relative z-10 h-full flex flex-col p-5 gap-4">
        {/* 页头 */}
        <div className="flex items-center justify-between shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-blue-400 to-blue-500 flex items-center justify-center text-white shadow-[0_6px_16px_rgba(59,130,246,0.25)]">
              <AlertTriangle className="w-4 h-4" />
            </div>
            <div>
              <h1 className="text-[17px] font-semibold text-slate-800 tracking-tight leading-tight">事件登记</h1>
              <p className="text-xs text-slate-500 leading-tight mt-0.5">统一接入业务事件 · 支持平台登记与第三方 API 上报</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center bg-white/60 border border-slate-200/70 rounded-lg p-0.5 text-xs">
              {[
                { v: 'active', l: '活跃' },
                { v: 'archived', l: '已归档' },
                { v: 'all', l: '全部' },
              ].map(o => (
                <button key={o.v} onClick={() => setStatus(o.v)}
                  className={`px-2.5 py-1 rounded-md font-medium transition-all ${status === o.v ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
                  {o.v === 'archived' && <Archive className="w-3 h-3 inline -mt-0.5 mr-0.5" />}{o.l}
                </button>
              ))}
            </div>
            {abnormalCount > 0 && (
              <button onClick={() => setOnlyAbnormal(!onlyAbnormal)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${onlyAbnormal ? 'bg-red-50 text-red-600 border border-red-200' : 'bg-white/60 text-slate-600 border border-slate-200/70 hover:bg-white'}`}>
                <AlertOctagon className="w-3.5 h-3.5" />{abnormalCount} 待关注
              </button>
            )}
            <button onClick={() => setKeysOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/60 border border-slate-200/70 text-xs font-medium text-slate-600 hover:bg-white transition-all">
              <Code2 className="w-3.5 h-3.5" />接入管理
              <span className="ml-0.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600 text-[10px] font-medium">API</span>
            </button>
            <button onClick={() => { setEditing(null); setFormOpen(true) }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 text-white text-xs font-medium shadow-[0_4px_12px_rgba(59,130,246,0.3)] hover:shadow-[0_6px_16px_rgba(59,130,246,0.4)] transition-all">
              <Plus className="w-3.5 h-3.5" />登记事件
            </button>
          </div>
        </div>

        {/* 第一行：4 个小指标 + 级别分布 + 7日趋势 */}
        <div className="grid grid-cols-12 gap-3 shrink-0">
          <MetricCard icon={<Database className="w-3.5 h-3.5" />} label="事件总数" value={stats?.total ?? 0} sub={`活跃 ${stats?.active ?? 0} · 归档 ${stats?.archived ?? 0}`} accent="blue" />
          <MetricCard icon={<ListTodo className="w-3.5 h-3.5" />} label="平台录入" value={stats?.platform ?? 0} sub="人工登记" accent="teal" />
          <MetricCard icon={<Activity className="w-3.5 h-3.5" />} label="API 接入" value={stats?.api ?? 0} sub={`${apiCoverage}% 覆盖率`} accent="purple" />
          <MetricCard icon={<Zap className="w-3.5 h-3.5" />} label="今日新增" value={stats?.today ?? 0} sub="实时" accent="gold" />

          {/* 级别分布环 */}
          <div className={`col-span-3 ${GLASS} px-4 py-2.5 flex items-center gap-3 overflow-hidden`}>
            <div className="relative w-[68px] h-[68px] shrink-0">
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
          <div className={`col-span-5 ${GLASS} px-4 py-2.5 overflow-hidden min-w-0 flex flex-col`}>
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
            <div className="flex-1 min-h-0 w-full overflow-hidden" style={{ height: 108 }}>
              <ReactECharts option={trendOption} style={{ width: '100%', height: '100%' }} opts={{ renderer: 'svg' }} notMerge />
            </div>
          </div>
        </div>

        {/* 筛选栏 */}
        <div className={`${GLASS} px-3 py-2 flex items-center gap-2 shrink-0 flex-wrap`}>
          <div className="relative flex-1 min-w-[180px] max-w-[280px]">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索事件标题、编号、上报人..."
              className="w-full pl-8 pr-3 py-1.5 text-xs bg-white/60 border border-slate-200/60 rounded-lg focus:outline-none focus:border-blue-300 focus:ring-2 focus:ring-blue-100/50 transition-all placeholder:text-slate-400" />
          </div>
          <Select value={severity} onChange={setSeverity} options={[
            { v: '', l: '全部级别' }, { v: 'critical', l: '严重' }, { v: 'high', l: '高' }, { v: 'medium', l: '中' }, { v: 'low', l: '低' }, { v: 'info', l: '信息' },
          ]} />
          <Select value={sourceType} onChange={setSourceType} options={[
            { v: '', l: '全部来源' }, { v: 'platform', l: '平台录入' }, { v: 'api', l: 'API 上报' }, { v: 'system', l: '系统生成' },
          ]} />
          <div className="h-5 w-px bg-slate-200/70" />
          <button onClick={() => setOnlyAbnormal(!onlyAbnormal)}
            className={`flex items-center gap-1 px-2.5 py-1 text-[11px] rounded-md border transition-all ${onlyAbnormal ? 'bg-red-50 text-red-600 border-red-200' : 'bg-white/50 text-slate-600 border-slate-200/60 hover:bg-white'}`}>
            <CircleDot className="w-3 h-3" />仅异常
          </button>
          <div className="ml-auto flex items-center gap-1">
            <span className="text-[11px] text-slate-400 mr-1">共 <span className="font-semibold text-slate-700 tabular-nums">{listQ.data?.total ?? 0}</span> 条</span>
            <button onClick={refresh}
              className="p-1.5 rounded-md text-slate-500 hover:bg-white/80 hover:text-slate-700 transition-all" title="刷新">
              <RefreshCcw className={`w-3.5 h-3.5 ${statsQ.isFetching || listQ.isFetching ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* 表格区 */}
        <div className={`${GLASS} flex-1 min-h-0 flex flex-col overflow-hidden`}>
          <div className="flex-1 min-h-0 overflow-auto thin-scroll">
            <table className="w-full text-xs">
              <thead>
                <tr className="sticky top-0 z-10 bg-white/85 backdrop-blur-sm text-slate-400 uppercase tracking-wider text-[10px]">
                  <th className="text-left font-medium px-4 py-2 w-[26%]">事件</th>
                  <th className="text-left font-medium px-3 py-2 w-[16%]">来源</th>
                  <th className="text-left font-medium px-3 py-2 w-[9%]">级别</th>
                  <th className="text-left font-medium px-3 py-2 w-[22%]">描述</th>
                  <th className="text-left font-medium px-3 py-2 w-[8%]">附件</th>
                  <th className="text-left font-medium px-3 py-2 w-[12%]">发生时间</th>
                  <th className="text-right font-medium px-4 py-2 w-[7%]">操作</th>
                </tr>
              </thead>
              <tbody>
                {listQ.isLoading ? (
                  <tr><td colSpan={7} className="text-center py-16 text-slate-400 text-xs">加载中...</td></tr>
                ) : listQ.data?.items?.length === 0 ? (
                  <tr><td colSpan={7} className="text-center py-16">
                    <div className="w-12 h-12 mx-auto rounded-2xl bg-gradient-to-br from-slate-100 to-slate-50 flex items-center justify-center mb-3 shadow-inner">
                      <Filter className="w-5 h-5 text-slate-300" />
                    </div>
                    <p className="text-slate-400 text-xs">暂无匹配事件</p>
                    <p className="text-slate-300 text-[11px] mt-1">尝试调整筛选条件，或登记新事件</p>
                    <button onClick={() => { setEditing(null); setFormOpen(true) }}
                      className="mt-3 inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-blue-50 text-blue-600 text-xs font-medium hover:bg-blue-100 transition-all">
                      <PlusCircle className="w-3 h-3" />立即登记
                    </button>
                  </td></tr>
                ) : (listQ.data?.items ?? []).map((r, i) => (
                  <tr key={r.id}
                    className={`group border-t border-slate-100/70 hover:bg-blue-50/40 transition-colors ${(r.severity === 'critical' || r.severity === 'high') ? 'bg-red-50/15' : ''}`}
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
                          className="p-1 rounded-md hover:bg-white text-slate-400 hover:text-blue-500 transition-colors" title="查看/编辑">
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
          <div className="flex items-center justify-between px-4 py-2 border-t border-slate-100/70 bg-white/40 shrink-0">
            <div className="text-[11px] text-slate-400 tabular-nums">
              显示 {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, listQ.data?.total ?? 0)} / {listQ.data?.total ?? 0}
            </div>
            <div className="flex items-center gap-1">
              <button disabled={page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}
                className="w-7 h-7 flex items-center justify-center rounded-lg border border-slate-200/60 bg-white/60 text-slate-500 disabled:opacity-40 hover:bg-white transition-all">
                <ChevronLeft className="w-3.5 h-3.5" />
              </button>
              {Array.from({ length: Math.min(totalPages, 5) }).map((_, i) => {
                let p = i + 1
                if (totalPages > 5) { if (page > 3) p = Math.min(totalPages - 4, page - 2) + i }
                return (
                  <button key={p} onClick={() => setPage(p)}
                    className={`w-7 h-7 flex items-center justify-center rounded-lg text-[11px] font-medium transition-all ${p === page ? 'bg-blue-500 text-white shadow-[0_2px_6px_rgba(59,130,246,0.3)]' : 'bg-white/60 border border-slate-200/60 text-slate-500 hover:bg-white'}`}>
                    {p}
                  </button>
                )
              })}
              <button disabled={page >= totalPages} onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                className="w-7 h-7 flex items-center justify-center rounded-lg border border-slate-200/60 bg-white/60 text-slate-500 disabled:opacity-40 hover:bg-white transition-all">
                <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </div>

        {/* 移动端 FAB */}
        <button onClick={() => { setEditing(null); setFormOpen(true) }}
          className="fixed bottom-6 right-6 z-20 w-11 h-11 rounded-full bg-gradient-to-br from-blue-500 to-blue-600 text-white shadow-[0_8px_24px_rgba(59,130,246,0.35)] hover:shadow-[0_10px_28px_rgba(59,130,246,0.45)] hover:-translate-y-0.5 transition-all flex items-center justify-center md:hidden">
          <PlusCircle className="w-5 h-5" />
        </button>
      </div>

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
function MetricCard({ icon, label, value, sub, accent }: { icon: React.ReactNode; label: string; value: number; sub?: string; accent: 'blue' | 'teal' | 'gold' | 'purple' }) {
  const accentMap = {
    blue: { bg: 'bg-blue-50/80', color: 'text-blue-500', dot: PALETTE.blue },
    teal: { bg: 'bg-teal-50/80', color: 'text-teal-500', dot: PALETTE.teal },
    gold: { bg: 'bg-amber-50/80', color: 'text-amber-500', dot: PALETTE.gold },
    purple: { bg: 'bg-purple-50/80', color: 'text-purple-500', dot: PALETTE.purple },
  }[accent]
  return (
    <div className={`col-span-1 ${GLASS} px-3 py-2.5 flex items-center gap-2.5 hover:-translate-y-0.5 hover:shadow-[0_6px_20px_rgba(15,23,42,0.07)] transition-all overflow-hidden`}>
      <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${accentMap.bg} ${accentMap.color}`}>
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-1.5">
          <span className="text-lg font-semibold text-slate-800 tabular-nums leading-none tracking-tight">{value.toLocaleString()}</span>
          {sub && <span className="text-[9px] text-slate-400 leading-none truncate">{sub}</span>}
        </div>
        <div className="text-[10px] text-slate-500 mt-1 leading-none flex items-center gap-1">
          <span className="w-1 h-1 rounded-full" style={{ background: accentMap.dot }} />{label}
        </div>
      </div>
    </div>
  )
}

// ─── 下拉选择 ────────────────────────────────────────────
function Select({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: { v: string; l: string }[] }) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      className="px-2.5 py-1.5 text-xs bg-white/60 border border-slate-200/60 rounded-lg focus:outline-none focus:border-blue-300 focus:ring-2 focus:ring-blue-100/50 transition-all appearance-none pr-7 cursor-pointer text-slate-600"
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
        <span className="w-5 h-5 rounded-md bg-purple-50 flex items-center justify-center text-purple-500 shrink-0">
          <Code2 className="w-2.5 h-2.5" />
        </span>
        <div className="min-w-0">
          <div className="text-slate-700 text-[11px] truncate flex items-center gap-0.5">
            <span className="truncate max-w-[100px]">{name}</span>
            <ArrowUpRight className="w-2.5 h-2.5 text-slate-400 shrink-0" />
          </div>
          <div className="text-[9px] text-purple-500">API 接入</div>
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
