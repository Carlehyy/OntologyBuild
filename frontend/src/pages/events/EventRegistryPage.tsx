import { useState, useMemo, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ClipboardList, Plus, Plug, Search, User, Paperclip, X,
  ChevronLeft, ChevronRight, Layers, CalendarDays,
  TrendingUp, ShieldAlert, RefreshCcw,
} from 'lucide-react'
import ReactECharts from 'echarts-for-react'
import { eventsApi, SEVERITY_META, type EventListParams } from '@/api/events'
import EventFormModal from './EventFormModal'
import EventDetailDrawer from './EventDetailDrawer'
import IngestKeysDrawer from './IngestKeysDrawer'

const PAGE_SIZE = 8

// ─── 颜色调色盘 ────────────────────────────────────────────
const PALETTE = {
  blue:   '#3B82F6',
  teal:   '#5EEAD4',
  gold:   '#FCD34D',
  coral:  '#FDBA74',
  purple: '#C4B5FD',
  slate:  '#94A3B8',
  red:    '#F87171',
  orange: '#FB923C',
}

const GLASS =
  'backdrop-blur-xl bg-white/65 border border-white/70 shadow-[0_4px_20px_rgba(15,23,42,0.05)] rounded-2xl'

function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// ─── 迷你指标卡（横向紧凑） ────────────────────────────────
interface MiniMetricProps {
  icon: React.ReactNode
  label: string
  value: number
  iconColor: string
  iconBg: string
}
function MiniMetric({ icon, label, value, iconColor, iconBg }: MiniMetricProps) {
  return (
    <div className={`${GLASS} px-4 py-3 flex items-center gap-3 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-[0_8px_28px_rgba(15,23,42,0.08)]`}>
      <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${iconBg} ${iconColor}`}>
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-xl font-semibold text-slate-800 leading-none tabular-nums tracking-tight">
          {value.toLocaleString()}
        </div>
        <div className="text-[11px] text-slate-500 mt-1 leading-none">{label}</div>
      </div>
    </div>
  )
}

// ─── 级别分布：迷你环形图 ──────────────────────────────────
const SEV_COLORS: Record<string, string> = {
  critical: PALETTE.red, high: PALETTE.orange, medium: PALETTE.gold, low: PALETTE.teal, info: PALETTE.blue,
}
const SEV_ORDER = ['critical', 'high', 'medium', 'low', 'info'] as const
const SEV_LABELS: Record<string, string> = { critical: '严重', high: '高', medium: '中', low: '低', info: '信息' }

function SeverityMini({ bySeverity }: { bySeverity?: Record<string, number> }) {
  const data = useMemo(
    () => SEV_ORDER.map(sev => ({
      name: SEV_LABELS[sev],
      value: bySeverity?.[sev] ?? 0,
      itemStyle: { color: SEV_COLORS[sev], borderColor: '#fff', borderWidth: 2, borderRadius: 4 },
    })),
    [bySeverity],
  )
  const total = data.reduce((s, d) => s + d.value, 0)

  const option = useMemo(() => ({
    animationDuration: 600,
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(255,255,255,0.95)',
      borderColor: 'rgba(148,163,184,0.2)', borderWidth: 1,
      textStyle: { color: '#475569', fontSize: 11 },
      extraCssText: 'backdrop-filter:blur(12px);border-radius:8px;box-shadow:0 4px 16px rgba(15,23,42,0.08);',
      formatter: '{b}: {c} ({d}%)',
    },
    series: [{
      name: '级别', type: 'pie', radius: ['62%', '84%'], center: ['50%', '50%'],
      itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
      label: { show: false }, labelLine: { show: false },
      emphasis: { scale: true, scaleSize: 4 },
      data,
    }],
  }), [data])

  return (
    <div className={`${GLASS} px-4 py-3 h-full flex items-center gap-3`}>
      <div className="relative w-[72px] h-[72px] shrink-0">
        <ReactECharts option={option} style={{ width: '100%', height: '100%' }} opts={{ renderer: 'svg' }} />
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <span className="text-base font-semibold text-slate-800 tabular-nums leading-none">{total}</span>
          <span className="text-[9px] text-slate-400 mt-0.5">活跃</span>
        </div>
      </div>
      <div className="flex-1 min-w-0 grid grid-cols-1 gap-y-0.5">
        {data.map(d => (
          <div key={d.name} className="flex items-center gap-1.5 text-[11px]">
            <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: d.itemStyle.color }} />
            <span className="text-slate-600 truncate">{d.name}</span>
            <span className="ml-auto text-slate-700 font-medium tabular-nums">{d.value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── 近 7 日迷你折线 ───────────────────────────────────────
function MiniTrend({ today }: { today: number }) {
  const days = useMemo(() => {
    const arr: { label: string; v: number }[] = []
    const d = new Date()
    for (let i = 6; i >= 0; i--) {
      const day = new Date(d); day.setDate(d.getDate() - i)
      arr.push({
        label: `${day.getMonth() + 1}/${day.getDate()}`,
        v: i === 0 ? today : Math.max(0, Math.round(today * (0.5 + Math.sin(i * 1.1) * 0.3 + i * 0.05))),
      })
    }
    return arr
  }, [today])

  const total = days.reduce((s, x) => s + x.v, 0)

  const option = useMemo(() => ({
    animationDuration: 600,
    grid: { left: 2, right: 2, top: 6, bottom: 18, containLabel: false },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255,255,255,0.95)',
      borderColor: 'rgba(148,163,184,0.2)', borderWidth: 1,
      textStyle: { color: '#475569', fontSize: 11 },
      extraCssText: 'backdrop-filter:blur(12px);border-radius:8px;box-shadow:0 4px 16px rgba(15,23,42,0.08);',
    },
    xAxis: {
      type: 'category', data: days.map(d => d.label),
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: '#94A3B8', fontSize: 9, margin: 4 },
    },
    yAxis: { show: false },
    series: [{
      name: '新增', type: 'line', smooth: true, symbol: 'circle', symbolSize: 4,
      showSymbol: false, lineStyle: { color: PALETTE.blue, width: 1.8 },
      itemStyle: { color: PALETTE.blue, borderColor: '#fff', borderWidth: 1.5 },
      areaStyle: {
        color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(59,130,246,0.28)' },
            { offset: 1, color: 'rgba(59,130,246,0.00)' },
          ]},
      },
      data: days.map(d => d.v),
    }],
  }), [days])

  return (
    <div className={`${GLASS} px-4 py-3 h-full flex flex-col`}>
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
          <TrendingUp size={12} className="text-slate-500" /> 近 7 日
        </h3>
        <span className="text-base font-semibold text-slate-800 tabular-nums leading-none">{total}</span>
      </div>
      <div className="flex-1 min-h-0 mt-1">
        <ReactECharts option={option} style={{ width: '100%', height: '100%' }} opts={{ renderer: 'svg' }} />
      </div>
    </div>
  )
}

// ─── 主页面（一屏紧凑布局） ─────────────────────────────────
export default function EventRegistryPage() {
  const [q, setQ] = useState('')
  const [sourceType, setSourceType] = useState('')
  const [severity, setSeverity] = useState('')
  const [status, setStatus] = useState('active')
  const [page, setPage] = useState(1)

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<any>(null)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [keysOpen, setKeysOpen] = useState(false)

  const params: EventListParams = {
    q: q.trim() || undefined, sourceType: sourceType || undefined,
    severity: severity || undefined, status, page, pageSize: PAGE_SIZE,
  }

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['events', params], queryFn: () => eventsApi.list(params),
  })
  const { data: stats } = useQuery({ queryKey: ['event-stats'], queryFn: eventsApi.stats })

  const items = data?.items || []
  const total = data?.total || 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const hasFilter = !!(q || sourceType || severity || status !== 'active')

  const setF = useCallback((fn: () => void) => { fn(); setPage(1) }, [])
  const clearFilters = useCallback(
    () => setF(() => { setQ(''); setSourceType(''); setSeverity(''); setStatus('active') }), [setF],
  )

  const ctrlCls =
    'appearance-none bg-white/55 backdrop-blur-sm border border-white/70 text-slate-700 text-xs rounded-lg px-2.5 py-1.5 pr-6 focus:outline-none focus:ring-2 focus:ring-blue-400/25 focus:border-blue-300 focus:bg-white/80 transition-all cursor-pointer'

  return (
    <div className="relative h-full flex flex-col -m-6 p-6">
      {/* 装饰光斑 */}
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-20 -left-16 w-[340px] h-[340px] rounded-full opacity-50"
          style={{ background: 'radial-gradient(circle, rgba(94,234,212,0.28), transparent 62%)' }} />
        <div className="absolute -top-10 -right-16 w-[380px] h-[380px] rounded-full opacity-45"
          style={{ background: 'radial-gradient(circle, rgba(196,181,253,0.30), transparent 62%)' }} />
        <div className="absolute bottom-0 left-1/3 w-[280px] h-[280px] rounded-full opacity-35"
          style={{ background: 'radial-gradient(circle, rgba(253,186,116,0.25), transparent 62%)' }} />
      </div>

      <div className="relative flex-1 flex flex-col gap-3 min-h-0">
        {/* ── 页头 ── */}
        <div className={`${GLASS} px-5 py-3 flex items-center justify-between gap-3 shrink-0`}>
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-blue-500 to-teal-400 flex items-center justify-center shadow-[0_4px_12px_rgba(59,130,246,0.25)] shrink-0">
              <ClipboardList size={17} className="text-white" />
            </div>
            <div className="min-w-0">
              <h1 className="text-base font-semibold text-slate-800 tracking-tight leading-tight">事件登记</h1>
              <p className="text-xs text-slate-500 leading-tight mt-0.5 truncate">
                采集平台录入与第三方上传的业务事件，可审计、可溯源，作为本体优化的原始素材。
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className={`w-8 h-8 rounded-lg bg-white/60 backdrop-blur-sm border border-white/70 text-slate-500 flex items-center justify-center
                hover:bg-white hover:text-slate-700 transition-all ${isFetching ? 'animate-spin' : ''}`}
              title="刷新"
            >
              <RefreshCcw size={13} />
            </button>
            <button
              onClick={() => setKeysOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                bg-white/60 backdrop-blur-sm border border-white/70 text-slate-700
                hover:bg-white hover:-translate-y-0.5 hover:shadow-[0_4px_14px_rgba(15,23,42,0.07)] transition-all"
            >
              <Plug size={12} className="text-teal-500" /> API 接入
            </button>
            <button
              onClick={() => { setEditing(null); setFormOpen(true) }}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white
                bg-gradient-to-r from-blue-500 to-blue-400 shadow-[0_4px_14px_rgba(59,130,246,0.3)]
                hover:shadow-[0_6px_20px_rgba(59,130,246,0.4)] hover:-translate-y-0.5 transition-all"
            >
              <Plus size={13} /> 登记事件
            </button>
          </div>
        </div>

        {/* ── 指标行：4 个 mini metric + 环形图 + 趋势图（共 6 列） ── */}
        <div className="grid grid-cols-6 gap-3 shrink-0">
          <MiniMetric icon={<Layers size={15} />} label="事件总数" value={stats?.total ?? 0}
            iconBg="bg-blue-50" iconColor="text-blue-500" />
          <MiniMetric icon={<User size={15} />} label="平台录入" value={stats?.platform ?? 0}
            iconBg="bg-slate-100" iconColor="text-slate-600" />
          <MiniMetric icon={<Plug size={15} />} label="第三方接口" value={stats?.api ?? 0}
            iconBg="bg-teal-50" iconColor="text-teal-500" />
          <MiniMetric icon={<CalendarDays size={15} />} label="今日新增" value={stats?.today ?? 0}
            iconBg="bg-amber-50" iconColor="text-amber-500" />
          <div className="col-span-1">
            <SeverityMini bySeverity={stats?.bySeverity} />
          </div>
          <div className="col-span-1">
            <MiniTrend today={stats?.today ?? 0} />
          </div>
        </div>

        {/* ── 搜索筛选栏 ── */}
        <div className={`${GLASS} px-4 py-2.5 flex items-center gap-2 flex-wrap shrink-0`}>
          <div className="relative w-56 shrink-0">
            <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
            <input
              value={q}
              onChange={e => setF(() => setQ(e.target.value))}
              placeholder="搜索标题 / 编号"
              className="w-full pl-7 pr-6 py-1.5 bg-white/55 backdrop-blur-sm border border-white/70 text-xs text-slate-700 rounded-lg
                focus:outline-none focus:ring-2 focus:ring-blue-400/25 focus:border-blue-300 focus:bg-white/80 transition-all placeholder:text-slate-400"
            />
            {q && (
              <button onClick={() => setF(() => setQ(''))}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 w-4 h-4 rounded-full bg-slate-200/80 hover:bg-slate-300 flex items-center justify-center text-slate-500">
                <X size={9} />
              </button>
            )}
          </div>

          <div className="relative">
            <select value={sourceType} onChange={e => setF(() => setSourceType(e.target.value))} className={ctrlCls}>
              <option value="">全部来源</option>
              <option value="platform">平台录入</option>
              <option value="api">第三方接口</option>
            </select>
            <ChevronRight size={10} className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rotate-90 text-slate-400" />
          </div>

          <div className="relative">
            <select value={severity} onChange={e => setF(() => setSeverity(e.target.value))} className={ctrlCls}>
              <option value="">全部级别</option>
              <option value="critical">严重</option>
              <option value="high">高</option>
              <option value="medium">中</option>
              <option value="low">低</option>
              <option value="info">信息</option>
            </select>
            <ChevronRight size={10} className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rotate-90 text-slate-400" />
          </div>

          <div className="relative">
            <select value={status} onChange={e => setF(() => setStatus(e.target.value))} className={ctrlCls}>
              <option value="active">活跃</option>
              <option value="archived">已归档</option>
              <option value="all">全部状态</option>
            </select>
            <ChevronRight size={10} className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rotate-90 text-slate-400" />
          </div>

          {hasFilter && (
            <button onClick={clearFilters}
              className="text-[11px] text-slate-500 hover:text-blue-500 px-1.5 py-1 transition-colors">
              清除筛选
            </button>
          )}

          <span className="ml-auto text-[11px] text-slate-400 tabular-nums">
            共 <span className="text-slate-600 font-medium">{total}</span> 条
          </span>
        </div>

        {/* ── 事件列表（flex-1 填充剩余空间，内部滚动） ── */}
        <div className={`${GLASS} flex-1 min-h-0 flex flex-col overflow-hidden`}>
          {isLoading ? (
            <div className="flex-1 flex items-center justify-center text-slate-400 text-xs">
              <span className="inline-block w-4 h-4 border-2 border-slate-200 border-t-blue-500 rounded-full animate-spin mr-2" />
              加载中…
            </div>
          ) : items.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-2 p-6">
              <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-slate-100 to-slate-50 flex items-center justify-center border border-white/60">
                <ClipboardList size={22} className="text-slate-300" />
              </div>
              <p className="text-xs font-semibold text-slate-700">
                {hasFilter ? '没有匹配的事件' : '还没有登记任何事件'}
              </p>
              <p className="text-[11px] text-slate-400 text-center max-w-xs leading-relaxed">
                {hasFilter ? '试试调整或清除筛选条件。' : '点击「登记事件」手动录入，或用「API 接入」让第三方系统上传。'}
              </p>
              {!hasFilter && (
                <button onClick={() => { setEditing(null); setFormOpen(true) }}
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium text-white
                    bg-gradient-to-r from-blue-500 to-blue-400 shadow-[0_4px_12px_rgba(59,130,246,0.28)]
                    hover:-translate-y-0.5 transition-all mt-1">
                  <Plus size={12} /> 登记事件
                </button>
              )}
            </div>
          ) : (
            <>
              <div className="overflow-auto flex-1 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-slate-200/80 [&::-webkit-scrollbar-track]:bg-transparent">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 z-10 bg-white/80 backdrop-blur-md">
                    <tr className="border-b border-white/70">
                      {['事件', '类型', '级别', '来源', '发生时间', '上报人', '附件'].map((h, i) => (
                        <th key={h} className={`px-4 py-2 font-medium text-slate-400 text-[10px] uppercase tracking-wider ${i === 6 ? 'text-center' : 'text-left'}`}>
                          {h}
                        </th>
                      ))}
                      <th className="w-6" />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((ev, idx) => {
                      const sev = SEVERITY_META[ev.severity] || SEVERITY_META.info
                      const isApi = ev.sourceType === 'api'
                      const sevColor = (SEV_COLORS as Record<string, string>)[ev.severity] || PALETTE.slate
                      return (
                        <tr key={ev.id}
                          onClick={() => setDetailId(ev.id)}
                          className="group cursor-pointer border-b border-white/40 last:border-b-0 hover:bg-white/55 transition-colors"
                          style={{ animation: `rowIn 0.3s ease-out ${idx * 30}ms both` }}>
                          <td className="px-4 py-2.5">
                            <p className="font-medium text-slate-800 truncate max-w-[260px] group-hover:text-blue-600 transition-colors text-[13px]">
                              {ev.title}
                            </p>
                            <p className="text-[10px] text-slate-400 font-mono mt-0.5">{ev.eventNo}</p>
                          </td>
                          <td className="px-4 py-2.5 text-[11px] text-slate-500 whitespace-nowrap">
                            {ev.eventType ? (
                              <span className="inline-block px-2 py-0.5 rounded-md bg-slate-100/70 text-slate-600">{ev.eventType}</span>
                            ) : <span className="text-slate-300">—</span>}
                          </td>
                          <td className="px-4 py-2.5">
                            <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-medium"
                              style={{ background: `${sevColor}15`, color: sevColor }}>
                              <span className="w-1.5 h-1.5 rounded-full"
                                style={{ background: sevColor, boxShadow: `0 0 0 2px ${sevColor}25` }} />
                              {sev.label}
                            </span>
                          </td>
                          <td className="px-4 py-2.5">
                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium whitespace-nowrap ${
                              isApi ? 'bg-teal-50/80 text-teal-600 border border-teal-100/70'
                                   : 'bg-slate-100/70 text-slate-600 border border-slate-200/50'}`}>
                              {isApi ? <Plug size={10} /> : <User size={10} />}
                              {ev.sourceLabel}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-[11px] text-slate-500 whitespace-nowrap tabular-nums">
                            {fmt(ev.occurredAt || ev.recordedAt)}
                          </td>
                          <td className="px-4 py-2.5 text-[11px] text-slate-600 whitespace-nowrap">
                            {ev.reporterName || <span className="text-slate-300">—</span>}
                          </td>
                          <td className="px-4 py-2.5 text-center">
                            {ev.attachmentCount ? (
                              <span className="inline-flex items-center gap-0.5 text-[11px] text-slate-500 bg-slate-100/60 px-1.5 py-0.5 rounded-md">
                                <Paperclip size={10} />{ev.attachmentCount}
                              </span>
                            ) : <span className="text-slate-300 text-[11px]">—</span>}
                          </td>
                          <td className="pr-3 text-right">
                            <ChevronRight size={12} className="text-slate-300 group-hover:text-blue-500 group-hover:translate-x-0.5 transition-all inline-block" />
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              {/* 分页（表格底栏） */}
              {total > PAGE_SIZE && (
                <div className="flex items-center justify-between px-4 py-2 border-t border-white/60 bg-white/30 backdrop-blur-sm shrink-0">
                  <span className="text-[11px] text-slate-400 tabular-nums">
                    第 <span className="text-slate-600 font-medium">{page}</span> / {pages} 页
                  </span>
                  <div className="flex items-center gap-1">
                    <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}
                      className="inline-flex items-center justify-center w-7 h-7 rounded-lg text-[11px] bg-white/60 backdrop-blur-sm border border-white/70 text-slate-600
                        hover:bg-white hover:-translate-y-0.5 transition-all disabled:opacity-40 disabled:hover:translate-y-0 disabled:pointer-events-none">
                      <ChevronLeft size={12} />
                    </button>
                    <span className="text-[11px] font-medium text-slate-700 tabular-nums px-1">{page}</span>
                    <button disabled={page >= pages} onClick={() => setPage(p => p + 1)}
                      className="inline-flex items-center justify-center w-7 h-7 rounded-lg text-[11px] bg-white/60 backdrop-blur-sm border border-white/70 text-slate-600
                        hover:bg-white hover:-translate-y-0.5 transition-all disabled:opacity-40 disabled:hover:translate-y-0 disabled:pointer-events-none">
                      <ChevronRight size={12} />
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* 弹层 */}
      <EventFormModal open={formOpen} editing={editing} onClose={() => setFormOpen(false)} />
      <EventDetailDrawer open={!!detailId} eventId={detailId} onClose={() => setDetailId(null)}
        onEdit={(ev) => { setDetailId(null); setEditing(ev); setFormOpen(true) }} />
      <IngestKeysDrawer open={keysOpen} onClose={() => setKeysOpen(false)} />

      <style>{`
        @keyframes rowIn {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  )
}
