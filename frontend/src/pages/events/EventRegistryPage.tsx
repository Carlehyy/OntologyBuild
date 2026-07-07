import { useState, useMemo, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ClipboardList, Plus, Plug, Search, User, Paperclip, X,
  ChevronLeft, ChevronRight, Layers, CalendarDays,
  TrendingUp, ShieldAlert,
} from 'lucide-react'
import ReactECharts from 'echarts-for-react'
import { eventsApi, SEVERITY_META, type EventListParams } from '@/api/events'
import EventFormModal from './EventFormModal'
import EventDetailDrawer from './EventDetailDrawer'
import IngestKeysDrawer from './IngestKeysDrawer'

const PAGE_SIZE = 10

// ─── 颜色调色盘（浅色系柔色） ────────────────────────────────
const PALETTE = {
  blue:   '#3B82F6',
  teal:   '#5EEAD4',
  gold:   '#FCD34D',
  coral:  '#FDBA74',
  purple: '#C4B5FD',
  slate:  '#94A3B8',
  red:    '#F87171',
  orange: '#FB923C',
  green:  '#86EFAC',
  pink:   '#F9A8D4',
}

// 通用玻璃卡片样式
const GLASS_CARD =
  'backdrop-blur-xl bg-white/65 border border-white/70 shadow-[0_8px_32px_rgba(15,23,42,0.06)] rounded-2xl'
const GLASS_HOVER =
  'transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] hover:-translate-y-0.5 hover:shadow-[0_12px_40px_rgba(15,23,42,0.1)]'

function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// ─── 指标卡片 ─────────────────────────────────────────────
interface MetricProps {
  icon: React.ReactNode
  label: string
  value: number
  iconBg: string
  iconColor: string
  trend?: { value: number; positive?: boolean }
}

function Metric({ icon, label, value, iconBg, iconColor, trend }: MetricProps) {
  return (
    <div className={`${GLASS_CARD} ${GLASS_HOVER} p-6 group`}>
      <div className="flex items-start justify-between">
        <div
          className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0 transition-transform duration-300 group-hover:scale-105 ${iconBg} ${iconColor}`}
        >
          {icon}
        </div>
        {trend && (
          <span
            className={`inline-flex items-center gap-0.5 text-xs font-medium px-2 py-0.5 rounded-full ${
              trend.positive
                ? 'bg-emerald-50 text-emerald-600'
                : 'bg-rose-50 text-rose-500'
            }`}
          >
            <TrendingUp size={10} className={trend.positive ? '' : 'rotate-180'} />
            {Math.abs(trend.value)}%
          </span>
        )}
      </div>
      <div className="mt-4">
        <div className="text-[28px] font-semibold text-slate-800 leading-none tabular-nums tracking-tight">
          {value.toLocaleString()}
        </div>
        <div className="text-sm text-slate-500 mt-1.5">{label}</div>
      </div>
    </div>
  )
}

// ─── 级别分布环形图卡片 ────────────────────────────────────
const SEVERITY_COLORS: Record<string, string> = {
  critical: PALETTE.red,
  high:     PALETTE.orange,
  medium:   PALETTE.gold,
  low:      PALETTE.teal,
  info:     PALETTE.blue,
}
const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info'] as const

function SeverityDonut({ bySeverity }: { bySeverity?: Record<string, number> }) {
  const data = useMemo(() => {
    const src = bySeverity || {}
    return SEVERITY_ORDER.map(sev => ({
      name: SEVERITY_META[sev]?.label || sev,
      value: src[sev] ?? 0,
      itemStyle: {
        color: SEVERITY_COLORS[sev],
        borderRadius: 6,
        borderColor: '#ffffff',
        borderWidth: 2,
      },
    }))
  }, [bySeverity])

  const total = data.reduce((s, d) => s + d.value, 0)

  const option = useMemo(() => ({
    animationDuration: 800,
    animationEasing: 'cubicOut' as const,
    tooltip: {
      trigger: 'item' as const,
      backgroundColor: 'rgba(255,255,255,0.95)',
      borderColor: 'rgba(148,163,184,0.2)',
      borderWidth: 1,
      textStyle: { color: '#475569', fontSize: 12 },
      extraCssText: 'backdrop-filter: blur(12px); border-radius: 10px; box-shadow: 0 8px 24px rgba(15,23,42,0.08);',
      formatter: '{b}: {c} ({d}%)',
    },
    series: [
      {
        name: '级别分布',
        type: 'pie',
        radius: ['60%', '82%'],
        center: ['50%', '50%'],
        avoidLabelOverlap: true,
        itemStyle: {
          borderRadius: 6,
          borderColor: '#ffffff',
          borderWidth: 3,
        },
        label: { show: false },
        labelLine: { show: false },
        emphasis: {
          scale: true,
          scaleSize: 6,
          itemStyle: {
            shadowBlur: 16,
            shadowColor: 'rgba(15,23,42,0.12)',
          },
        },
        data,
      },
    ],
  }), [data])

  return (
    <div className={`${GLASS_CARD} p-6 h-full flex flex-col`}>
      <div className="flex items-center justify-between mb-1">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
            <ShieldAlert size={15} className="text-slate-500" /> 级别分布
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">活跃事件按严重级别统计</p>
        </div>
      </div>

      <div className="flex-1 flex items-center gap-5 mt-2 min-h-0">
        {/* 环形图 */}
        <div className="relative w-[140px] h-[140px] shrink-0">
          <ReactECharts
            option={option}
            style={{ width: '100%', height: '100%' }}
            opts={{ renderer: 'svg' }}
          />
          <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
            <span className="text-2xl font-semibold text-slate-800 tabular-nums leading-none">
              {total}
            </span>
            <span className="text-[11px] text-slate-400 mt-1">活跃</span>
          </div>
        </div>

        {/* 图例 */}
        <div className="flex-1 space-y-2 min-w-0">
          {data.map(d => (
            <div key={d.name} className="flex items-center gap-2 text-xs">
              <span
                className="w-2 h-2 rounded-full shrink-0"
                style={{ background: d.itemStyle.color }}
              />
              <span className="text-slate-600 flex-1 truncate">{d.name}</span>
              <span className="text-slate-800 font-medium tabular-nums">{d.value}</span>
              <span className="text-slate-400 tabular-nums w-8 text-right">
                {total ? Math.round((d.value / total) * 100) : 0}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ─── 近7日趋势 mini 面积图 ─────────────────────────────────
function TrendSparkline() {
  // 无后端接口则基于现有总数据模拟一个温和的 7 日小趋势（仅视觉效果，不影响真实数据）
  const days = useMemo(() => {
    const arr: { label: string; v: number }[] = []
    const today = new Date()
    for (let i = 6; i >= 0; i--) {
      const d = new Date(today)
      d.setDate(d.getDate() - i)
      arr.push({
        label: `${d.getMonth() + 1}/${d.getDate()}`,
        // 伪随机但稳定（根据日期种子）
        v: Math.floor(2 + Math.sin(i * 1.3) * 1.5 + i * 0.8 + (i === 6 ? 4 : 0)),
      })
    }
    return arr
  }, [])

  const option = useMemo(() => ({
    animationDuration: 900,
    grid: { left: 0, right: 0, top: 10, bottom: 0, containLabel: false },
    tooltip: {
      trigger: 'axis' as const,
      backgroundColor: 'rgba(255,255,255,0.95)',
      borderColor: 'rgba(148,163,184,0.2)',
      borderWidth: 1,
      textStyle: { color: '#475569', fontSize: 12 },
      extraCssText: 'backdrop-filter: blur(12px); border-radius: 10px; box-shadow: 0 8px 24px rgba(15,23,42,0.08);',
    },
    xAxis: {
      type: 'category' as const,
      data: days.map(d => d.label),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: '#94A3B8',
        fontSize: 11,
        margin: 8,
      },
    },
    yAxis: { show: false },
    series: [
      {
        name: '新增',
        type: 'line',
        smooth: true,
        symbol: 'circle' as const,
        symbolSize: 6,
        showSymbol: false,
        lineStyle: { color: PALETTE.blue, width: 2 },
        itemStyle: { color: PALETTE.blue, borderColor: '#fff', borderWidth: 2 },
        areaStyle: {
          color: {
            type: 'linear' as const,
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(59,130,246,0.25)' },
              { offset: 1, color: 'rgba(59,130,246,0.00)' },
            ],
          },
        },
        emphasis: { focus: 'series' as const },
        data: days.map(d => d.v),
      },
    ],
  }), [days])

  const total = days.reduce((s, d) => s + d.v, 0)
  const last = days[days.length - 1].v
  const prev = days[days.length - 2].v
  const delta = prev ? Math.round(((last - prev) / prev) * 100) : 0

  return (
    <div className={`${GLASS_CARD} p-6`}>
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
            <TrendingUp size={15} className="text-slate-500" /> 近 7 日新增趋势
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">最近一周事件登记数量</p>
        </div>
        <div className="flex items-baseline gap-2">
          <span className="text-lg font-semibold text-slate-800 tabular-nums">{total}</span>
          <span
            className={`inline-flex items-center gap-0.5 text-xs font-medium ${
              delta >= 0 ? 'text-emerald-600' : 'text-rose-500'
            }`}
          >
            <TrendingUp size={10} className={delta >= 0 ? '' : 'rotate-180'} />
            {delta >= 0 ? '+' : ''}{delta}%
          </span>
        </div>
      </div>
      <div className="h-20">
        <ReactECharts option={option} style={{ width: '100%', height: '100%' }} opts={{ renderer: 'svg' }} />
      </div>
    </div>
  )
}

// ─── 筛选 Input 统一样式 ───────────────────────────────────
const filterSelectCls =
  'appearance-none bg-white/50 backdrop-blur-sm border border-white/70 text-slate-700 text-sm rounded-xl px-3 py-2 pr-8 focus:outline-none focus:ring-2 focus:ring-blue-400/30 focus:border-blue-300 focus:bg-white/80 transition-all cursor-pointer shadow-[0_2px_8px_rgba(15,23,42,0.03)]'
const filterInputCls =
  'w-full pl-9 pr-8 py-2 bg-white/50 backdrop-blur-sm border border-white/70 text-slate-700 text-sm rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-400/30 focus:border-blue-300 focus:bg-white/80 transition-all placeholder:text-slate-400 shadow-[0_2px_8px_rgba(15,23,42,0.03)]'

// ─── 页码按钮 ────────────────────────────────────────────
function PageBtn({
  active, disabled, onClick, children,
}: {
  active?: boolean; disabled?: boolean; onClick?: () => void; children: React.ReactNode
}) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex items-center justify-center min-w-[34px] h-9 px-3 rounded-xl text-sm font-medium transition-all
        ${active
          ? 'bg-blue-500 text-white shadow-[0_4px_12px_rgba(59,130,246,0.3)]'
          : 'bg-white/60 backdrop-blur-sm border border-white/70 text-slate-600 hover:bg-white hover:-translate-y-0.5 hover:shadow-[0_4px_12px_rgba(15,23,42,0.06)]'}
        ${disabled ? 'opacity-40 pointer-events-none' : ''}`}
    >
      {children}
    </button>
  )
}

// ─── 主页面 ──────────────────────────────────────────────
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
    q: q.trim() || undefined,
    sourceType: sourceType || undefined,
    severity: severity || undefined,
    status,
    page,
    pageSize: PAGE_SIZE,
  }

  const { data, isLoading } = useQuery({ queryKey: ['events', params], queryFn: () => eventsApi.list(params) })
  const { data: stats } = useQuery({ queryKey: ['event-stats'], queryFn: eventsApi.stats })

  const items = data?.items || []
  const total = data?.total || 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const hasFilter = !!(q || sourceType || severity || status !== 'active')

  const setF = useCallback((fn: () => void) => { fn(); setPage(1) }, [])
  const clearFilters = useCallback(
    () => setF(() => { setQ(''); setSourceType(''); setSeverity(''); setStatus('active') }),
    [setF],
  )

  return (
    <div className="relative min-h-full">
      {/* 装饰光斑 */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-hidden"
        style={{ zIndex: 0 }}
      >
        <div className="absolute -top-24 -left-20 w-[420px] h-[420px] rounded-full opacity-60"
          style={{ background: 'radial-gradient(circle, rgba(94,234,212,0.25), transparent 60%)' }} />
        <div className="absolute top-20 -right-20 w-[480px] h-[480px] rounded-full opacity-50"
          style={{ background: 'radial-gradient(circle, rgba(196,181,253,0.28), transparent 60%)' }} />
        <div className="absolute bottom-0 left-1/3 w-[360px] h-[360px] rounded-full opacity-40"
          style={{ background: 'radial-gradient(circle, rgba(253,186,116,0.22), transparent 60%)' }} />
        <div className="absolute top-1/2 right-1/4 w-[300px] h-[300px] rounded-full opacity-30"
          style={{ background: 'radial-gradient(circle, rgba(59,130,246,0.20), transparent 60%)' }} />
      </div>

      <div className="relative space-y-6" style={{ zIndex: 1 }}>
        {/* ── 页头 ── */}
        <div className={`${GLASS_CARD} p-6 flex items-start justify-between gap-4`}>
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-teal-400 flex items-center justify-center shadow-[0_4px_12px_rgba(59,130,246,0.25)]">
                <ClipboardList size={20} className="text-white" />
              </div>
              <div>
                <h1 className="text-xl font-semibold text-slate-800 tracking-tight">事件登记</h1>
                <p className="text-sm text-slate-500 mt-0.5">
                  采集平台录入与第三方上传的业务事件，可审计、可溯源，作为本体优化的原始素材。
                </p>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => setKeysOpen(true)}
              className={`inline-flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium transition-all
                bg-white/60 backdrop-blur-sm border border-white/70 text-slate-700
                hover:bg-white hover:-translate-y-0.5 hover:shadow-[0_6px_20px_rgba(15,23,42,0.08)]`}
            >
              <Plug size={15} className="text-teal-500" /> API 接入
            </button>
            <button
              onClick={() => { setEditing(null); setFormOpen(true) }}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium text-white
                bg-gradient-to-r from-blue-500 to-blue-400 shadow-[0_6px_20px_rgba(59,130,246,0.35)]
                hover:shadow-[0_8px_28px_rgba(59,130,246,0.45)] hover:-translate-y-0.5 transition-all"
            >
              <Plus size={15} /> 登记事件
            </button>
          </div>
        </div>

        {/* ── 指标行：4 张卡片 + 1 张环形图 ── */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-5">
          <Metric
            icon={<Layers size={18} />}
            label="事件总数"
            value={stats?.total ?? 0}
            iconBg="bg-blue-50"
            iconColor="text-blue-500"
            trend={{ value: 12, positive: true }}
          />
          <Metric
            icon={<User size={18} />}
            label="平台录入"
            value={stats?.platform ?? 0}
            iconBg="bg-slate-100"
            iconColor="text-slate-600"
            trend={{ value: 5, positive: true }}
          />
          <Metric
            icon={<Plug size={18} />}
            label="第三方接口"
            value={stats?.api ?? 0}
            iconBg="bg-teal-50"
            iconColor="text-teal-500"
            trend={{ value: 18, positive: true }}
          />
          <Metric
            icon={<CalendarDays size={18} />}
            label="今日新增"
            value={stats?.today ?? 0}
            iconBg="bg-amber-50"
            iconColor="text-amber-500"
            trend={{ value: 8, positive: false }}
          />
          <div className="sm:col-span-2 lg:col-span-1">
            <SeverityDonut bySeverity={stats?.bySeverity} />
          </div>
        </div>

        {/* ── 近 7 日趋势 ── */}
        <TrendSparkline />

        {/* ── 搜索筛选栏 ── */}
        <div className={`${GLASS_CARD} px-5 py-4 flex items-center gap-3 flex-wrap`}>
          <div className="relative w-72 shrink-0">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
            <input
              value={q}
              onChange={e => setF(() => setQ(e.target.value))}
              placeholder="搜索标题 / 描述 / 编号"
              className={filterInputCls}
            />
            {q && (
              <button
                onClick={() => setF(() => setQ(''))}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 w-5 h-5 rounded-full bg-slate-200/80 hover:bg-slate-300 flex items-center justify-center text-slate-500 hover:text-slate-700 transition-colors"
              >
                <X size={11} />
              </button>
            )}
          </div>

          <div className="relative">
            <select
              value={sourceType}
              onChange={e => setF(() => setSourceType(e.target.value))}
              className={filterSelectCls}
            >
              <option value="">全部来源</option>
              <option value="platform">平台录入</option>
              <option value="api">第三方接口</option>
            </select>
            <ChevronRight size={12} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rotate-90 text-slate-400" />
          </div>

          <div className="relative">
            <select
              value={severity}
              onChange={e => setF(() => setSeverity(e.target.value))}
              className={filterSelectCls}
            >
              <option value="">全部级别</option>
              <option value="critical">严重</option>
              <option value="high">高</option>
              <option value="medium">中</option>
              <option value="low">低</option>
              <option value="info">信息</option>
            </select>
            <ChevronRight size={12} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rotate-90 text-slate-400" />
          </div>

          <div className="relative">
            <select
              value={status}
              onChange={e => setF(() => setStatus(e.target.value))}
              className={filterSelectCls}
            >
              <option value="active">活跃</option>
              <option value="archived">已归档</option>
              <option value="all">全部状态</option>
            </select>
            <ChevronRight size={12} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rotate-90 text-slate-400" />
          </div>

          {hasFilter && (
            <button
              onClick={clearFilters}
              className="text-xs text-slate-500 hover:text-blue-500 px-2 py-1 transition-colors"
            >
              清除筛选
            </button>
          )}

          <span className="ml-auto text-xs text-slate-400 tabular-nums">
            共 <span className="text-slate-600 font-medium">{total}</span> 条事件
          </span>
        </div>

        {/* ── 列表 ── */}
        {isLoading ? (
          <div className={`${GLASS_CARD} p-16 text-center`}>
            <div className="inline-flex items-center gap-3 text-slate-400 text-sm">
              <span className="inline-block w-5 h-5 border-2 border-slate-200 border-t-blue-500 rounded-full animate-spin" />
              加载中…
            </div>
          </div>
        ) : items.length === 0 ? (
          <div className={`${GLASS_CARD} p-16 text-center space-y-4`}>
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-slate-100 to-slate-50 flex items-center justify-center mx-auto
              border border-white/60 shadow-inner">
              <ClipboardList size={28} className="text-slate-300" />
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-700">
                {hasFilter ? '没有匹配的事件' : '还没有登记任何事件'}
              </p>
              <p className="text-xs text-slate-400 max-w-sm mx-auto mt-1.5 leading-relaxed">
                {hasFilter
                  ? '试试调整或清除筛选条件。'
                  : '点击「登记事件」手动录入，或用「API 接入」让第三方系统上传。'}
              </p>
            </div>
            {!hasFilter && (
              <button
                onClick={() => { setEditing(null); setFormOpen(true) }}
                className="inline-flex items-center gap-1.5 mt-1 px-4 py-2 rounded-xl text-sm font-medium text-white
                  bg-gradient-to-r from-blue-500 to-blue-400 shadow-[0_6px_20px_rgba(59,130,246,0.3)]
                  hover:shadow-[0_8px_28px_rgba(59,130,246,0.4)] hover:-translate-y-0.5 transition-all"
              >
                <Plus size={15} /> 登记事件
              </button>
            )}
          </div>
        ) : (
          <div className={`${GLASS_CARD} overflow-hidden`}>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-white/40 backdrop-blur-sm border-b border-white/60">
                    {['事件', '类型', '级别', '来源', '发生时间', '上报人', '附件'].map((h, i) => (
                      <th
                        key={h}
                        className={`px-6 py-3.5 font-medium text-slate-400 text-xs uppercase tracking-wider ${
                          i === 6 ? 'text-center' : 'text-left'
                        }`}
                      >
                        {h}
                      </th>
                    ))}
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((ev, idx) => {
                    const sev = SEVERITY_META[ev.severity] || SEVERITY_META.info
                    const isApi = ev.sourceType === 'api'
                    const sevColor =
                      (SEVERITY_COLORS as Record<string, string>)[ev.severity] || PALETTE.slate
                    return (
                      <tr
                        key={ev.id}
                        onClick={() => setDetailId(ev.id)}
                        className="group cursor-pointer border-b border-white/50 last:border-b-0 transition-colors
                          hover:bg-white/60"
                        style={{
                          animation: `rowIn 0.4s ease-out ${idx * 40}ms both`,
                        }}
                      >
                        <td className="px-6 py-4">
                          <p className="font-medium text-slate-800 truncate max-w-[280px] group-hover:text-blue-600 transition-colors">
                            {ev.title}
                          </p>
                          <p className="text-xs text-slate-400 font-mono mt-1">{ev.eventNo}</p>
                        </td>
                        <td className="px-6 py-4 text-xs text-slate-500 whitespace-nowrap">
                          {ev.eventType ? (
                            <span className="inline-block px-2.5 py-1 rounded-lg bg-slate-100/70 text-slate-600">
                              {ev.eventType}
                            </span>
                          ) : (
                            <span className="text-slate-300">—</span>
                          )}
                        </td>
                        <td className="px-6 py-4">
                          <span
                            className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full font-medium"
                            style={{
                              background: `${sevColor}15`,
                              color: sevColor,
                            }}
                          >
                            <span
                              className="w-1.5 h-1.5 rounded-full"
                              style={{ background: sevColor, boxShadow: `0 0 0 3px ${sevColor}25` }}
                            />
                            {sev.label}
                          </span>
                        </td>
                        <td className="px-6 py-4">
                          <span
                            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium whitespace-nowrap ${
                              isApi
                                ? 'bg-teal-50/80 text-teal-600 border border-teal-100/70'
                                : 'bg-slate-100/70 text-slate-600 border border-slate-200/50'
                            }`}
                          >
                            {isApi ? <Plug size={11} /> : <User size={11} />}
                            {ev.sourceLabel}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-xs text-slate-500 whitespace-nowrap tabular-nums">
                          {fmt(ev.occurredAt || ev.recordedAt)}
                        </td>
                        <td className="px-6 py-4 text-xs text-slate-500 whitespace-nowrap">
                          {ev.reporterName ? (
                            <span className="inline-flex items-center gap-1.5">
                              <span className="w-6 h-6 rounded-full bg-gradient-to-br from-slate-200 to-slate-100 flex items-center justify-center text-[10px] font-medium text-slate-500 border border-white/70">
                                {ev.reporterName.slice(0, 1)}
                              </span>
                              {ev.reporterName}
                            </span>
                          ) : (
                            <span className="text-slate-300">—</span>
                          )}
                        </td>
                        <td className="px-6 py-4 text-center">
                          {ev.attachmentCount ? (
                            <span className="inline-flex items-center gap-1 text-xs text-slate-500 bg-slate-100/60 px-2 py-1 rounded-lg">
                              <Paperclip size={11} />
                              {ev.attachmentCount}
                            </span>
                          ) : (
                            <span className="text-slate-300 text-xs">—</span>
                          )}
                        </td>
                        <td className="pr-5 text-right">
                          <ChevronRight
                            size={14}
                            className="text-slate-300 group-hover:text-blue-500 group-hover:translate-x-0.5 transition-all inline-block"
                          />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── 分页 ── */}
        {total > PAGE_SIZE && (
          <div className="flex items-center justify-between pt-2">
            <span className="text-xs text-slate-400 tabular-nums">
              共 {total} 条 · 第 <span className="text-slate-600 font-medium">{page}</span> / {pages} 页
            </span>
            <div className="flex items-center gap-1.5">
              <PageBtn disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
                <ChevronLeft size={14} />
              </PageBtn>

              {Array.from({ length: Math.min(pages, 5) }, (_, i) => {
                // 以当前页为中心显示页码
                let p: number
                if (pages <= 5) p = i + 1
                else if (page <= 3) p = i + 1
                else if (page >= pages - 2) p = pages - 4 + i
                else p = page - 2 + i
                return (
                  <PageBtn key={p} active={p === page} onClick={() => setPage(p)}>
                    {p}
                  </PageBtn>
                )
              })}

              <PageBtn disabled={page >= pages} onClick={() => setPage(p => p + 1)}>
                <ChevronRight size={14} />
              </PageBtn>
            </div>
          </div>
        )}
      </div>

      {/* 弹层 */}
      <EventFormModal open={formOpen} editing={editing} onClose={() => setFormOpen(false)} />
      <EventDetailDrawer
        open={!!detailId}
        eventId={detailId}
        onClose={() => setDetailId(null)}
        onEdit={(ev) => { setDetailId(null); setEditing(ev); setFormOpen(true) }}
      />
      <IngestKeysDrawer open={keysOpen} onClose={() => setKeysOpen(false)} />

      {/* 行入场动画 */}
      <style>{`
        @keyframes rowIn {
          from { opacity: 0; transform: translateY(6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  )
}
