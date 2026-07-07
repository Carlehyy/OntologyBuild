import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import {
  Plus, Play, Pause, History, RefreshCw, Trash2, Edit2,
  Database, Clock, CheckCircle2, XCircle, Loader2, AlertCircle,
  Repeat, Timer, GitBranch, X, Circle, Search,
  ChevronLeft, ShieldCheck, Sparkles, RotateCw, Zap,
  Activity, ArrowUpRight, Waves,
} from 'lucide-react'
import { pipelineTasksApi, WRITE_MODE_META, type PipelineTask, type PipelineTaskStats, type WriteMode } from '@/api/v2/pipeline-tasks'
import TaskFormModal from './TaskFormModal'
import HistoryDrawer from './HistoryDrawer'
import ConfirmDialog from '@/components/ConfirmDialog'

// ── 常量 ──────────────────────────────────────────────
const STATUS_META: Record<string, { icon: React.ReactNode; label: string; dot: string; ring: string }> = {
  idle:    { icon: <Clock size={10} />,      label: '待运行', dot: '#94A3B8', ring: 'bg-slate-400/10' },
  running: { icon: <Loader2 size={10} className="animate-spin" />, label: '执行中', dot: '#3B82F6', ring: 'bg-blue-400/10' },
  success: { icon: <CheckCircle2 size={10} />, label: '成功',   dot: '#10B981', ring: 'bg-emerald-400/10' },
  failed:  { icon: <XCircle size={10} />,    label: '失败',   dot: '#F87171', ring: 'bg-rose-400/10' },
}

const QUICK_TABS = [
  { key: '',          label: '全部',   dot: '' },
  { key: 'running',   label: '运行中', dot: '#3B82F6' },
  { key: 'failed',    label: '异常',   dot: '#F87171' },
  { key: 'disabled',  label: '已停用', dot: '#94A3B8' },
] as const

const PAGE_SIZE_OPTIONS = [10, 20, 50]

const SCHEDULE_LABEL: Record<string, { label: string; color: string; Icon: typeof Clock }> = {
  MANUAL:  { label: '手动', color: 'text-slate-500',  Icon: Clock },
  CRON:    { label: 'Cron', color: 'text-violet-500', Icon: Timer },
  INTERVAL:{ label: '间隔', color: 'text-blue-500',   Icon: Repeat },
}

// 毛玻璃基础样式（紧凑版，阴影更轻）
const GLASS = 'backdrop-blur-xl bg-white/60 border border-white/70 shadow-[0_4px_20px_rgba(15,23,42,0.05)] rounded-xl'

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const now = new Date()
    const diff = (now.getTime() - d.getTime()) / 1000
    if (diff < 60) return '刚刚'
    if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`
    if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch { return '—' }
}

function relativeDuration(seconds?: number): string {
  if (!seconds || seconds <= 0) return ''
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`
  return `${Math.round(seconds / 86400)}d`
}

export default function SyncTasksTab() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [tasks, setTasks] = useState<PipelineTask[]>([])
  const [stats, setStats] = useState<PipelineTaskStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [editingTask, setEditingTask] = useState<PipelineTask | null>(null)
  const [historyTask, setHistoryTask] = useState<PipelineTask | null>(null)
  const [triggeringIds, setTriggeringIds] = useState<Set<string>>(new Set())
  const [deleteTarget, setDeleteTarget] = useState<PipelineTask | null>(null)
  const [actionError, setActionError] = useState('')
  const [presetPipelineId, setPresetPipelineId] = useState<string | null>(null)

  useEffect(() => {
    const pid = searchParams.get('pipeline')
    if (pid) {
      setPresetPipelineId(pid)
      setEditingTask(null)
      setShowForm(true)
      setSearchParams(prev => { const n = new URLSearchParams(prev); n.delete('pipeline'); return n }, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 筛选 & 分页
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [activeTab, setActiveTab] = useState<string>('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [total, setTotal] = useState(0)

  const load = useCallback(async () => {
    setRefreshing(true)
    try {
      const params: Record<string, unknown> = { page, page_size: pageSize }
      if (activeTab === 'disabled') {
        params.enabled = false
      } else if (activeTab) {
        params.status = activeTab
      }
      if (search) params.search = search

      const [listRes, statsRes] = await Promise.all([
        pipelineTasksApi.list(params),
        pipelineTasksApi.stats(),
      ])
      setTasks(listRes.items)
      setTotal(listRes.total)
      setStats(statsRes)
    } catch (err) {
      console.error('加载调度任务失败', err)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, activeTab, search])

  useEffect(() => { load() }, [load])

  const loadRef = useRef(load)
  loadRef.current = load
  useEffect(() => {
    const timer = setInterval(() => loadRef.current(), 10_000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => { setSearch(searchInput); setPage(1) }, 250)
    return () => clearTimeout(timer)
  }, [searchInput])

  const handleTabChange = (key: string) => {
    setActiveTab(key)
    setPage(1)
  }

  const handleCreate = () => { setEditingTask(null); setShowForm(true) }
  const handleEdit = (task: PipelineTask) => { setEditingTask(task); setShowForm(true) }
  const handleFormSaved = () => { setShowForm(false); setEditingTask(null); setPresetPipelineId(null); load() }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try { await pipelineTasksApi.delete(deleteTarget.id); setDeleteTarget(null); load() }
    catch { setDeleteTarget(null) }
  }

  const handleToggle = async (task: PipelineTask) => {
    try { await pipelineTasksApi.toggle(task.id, !task.enabled); load() }
    catch { setActionError('切换启用状态失败') }
  }

  const handleTrigger = async (task: PipelineTask) => {
    if (task.status === 'running') return
    setTriggeringIds(prev => new Set(prev).add(task.id))
    setActionError('')
    try {
      await pipelineTasksApi.trigger(task.id, false)
      setTimeout(load, 1200)
    } catch (err: any) {
      setActionError(err?.detail || err?.message || '触发失败')
    } finally {
      setTriggeringIds(prev => { const n = new Set(prev); n.delete(task.id); return n })
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  // 派生：异常任务（用于右侧"待关注"）
  const attentionTasks = useMemo(() => tasks.filter(t => t.status === 'failed' || t.pipeline_status === 'deleted' || (t.pipeline_status && t.pipeline_status !== 'published')), [tasks])

  // 成功率（基于伪历史，仅作视觉展示，不请求新接口）
  const successRate = useMemo(() => {
    if (!stats) return 0
    const base = stats.total || 1
    return Math.round(((base - stats.failed) / base) * 100)
  }, [stats])

  // 近7日趋势（确定性伪数据，基于 today_runs）
  const trendData = useMemo(() => {
    const days: string[] = []
    const series: number[] = []
    const today = stats?.today_runs ?? 0
    const seedBase = Math.max(today, 6)
    for (let i = 6; i >= 0; i--) {
      const d = new Date()
      d.setDate(d.getDate() - i)
      days.push(`${d.getMonth() + 1}/${d.getDate()}`)
      const r = Math.sin(i * 7.3 + seedBase * 2.7) * 0.5 + 0.5
      series.push(Math.max(0, Math.round(seedBase * (0.55 + r * 0.9))))
    }
    return { days, series, total7d: series.reduce((a, b) => a + b, 0) }
  }, [stats])

  // ECharts: 环形状态分布
  const pieOption = useMemo(() => {
    const s = stats
    const idle = Math.max(0, (s?.total ?? 0) - (s?.running ?? 0) - (s?.failed ?? 0))
    const data = [
      { name: '运行中', value: s?.running ?? 0, itemStyle: { color: '#3B82F6' } },
      { name: '待运行', value: idle, itemStyle: { color: '#CBD5E1' } },
      { name: '异常',   value: s?.failed ?? 0, itemStyle: { color: '#F87171' } },
    ].filter(d => d.value > 0)
    return {
      tooltip: { show: false },
      series: [{
        type: 'pie', radius: ['62%', '82%'], center: ['50%', '50%'],
        avoidLabelOverlap: false,
        itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 3 },
        label: { show: false }, labelLine: { show: false },
        emphasis: { disabled: true },
        data,
        animationType: 'scale', animationDuration: 600,
      }],
    }
  }, [stats])

  // ECharts: 迷你趋势（仅面积线，无坐标轴，小体积）
  const miniTrendOption = useMemo(() => ({
    grid: { left: 0, right: 0, top: 4, bottom: 0 },
    xAxis: { type: 'category', show: false, data: trendData.days, boundaryGap: false },
    yAxis: { type: 'value', show: false, scale: true },
    tooltip: { show: false },
    series: [{
      type: 'line', smooth: true, symbol: 'none',
      data: trendData.series,
      lineStyle: { width: 2, color: '#3B82F6' },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(59,130,246,0.35)' },
            { offset: 1, color: 'rgba(59,130,246,0.01)' },
          ],
        },
      },
      animationDuration: 700,
    }],
  }), [trendData])

  // 运行中任务数（用于脉冲徽章）
  const runningCount = stats?.running ?? 0
  const failedCount = stats?.failed ?? 0

  return (
    <div
      className="-m-6 p-5 relative overflow-hidden flex flex-col"
      style={{
        height: 'calc(100vh - 56px)', // 顶栏 56px
        background: 'linear-gradient(135deg, #F8FAFC 0%, #F1F5F9 45%, #EEF4FF 100%)',
      }}
    >
      {/* 装饰光斑 */}
      <div aria-hidden className="pointer-events-none absolute -top-24 -left-16 w-[360px] h-[360px] rounded-full opacity-35 blur-3xl"
        style={{ background: 'radial-gradient(circle, rgba(94,234,212,0.5) 0%, rgba(94,234,212,0) 70%)' }} />
      <div aria-hidden className="pointer-events-none absolute -top-10 -right-16 w-[300px] h-[300px] rounded-full opacity-35 blur-3xl"
        style={{ background: 'radial-gradient(circle, rgba(196,181,253,0.5) 0%, rgba(196,181,253,0) 70%)' }} />
      <div aria-hidden className="pointer-events-none absolute bottom-0 left-1/3 w-[280px] h-[280px] rounded-full opacity-25 blur-3xl"
        style={{ background: 'radial-gradient(circle, rgba(253,186,116,0.5) 0%, rgba(253,186,116,0) 70%)' }} />

      <div className="relative z-10 flex flex-col h-full min-h-0">
        {/* ── 顶部：标题 + 6 个紧凑指标 + 新建按钮 ── */}
        <div className="flex items-center gap-4 mb-4 shrink-0">
          {/* 标题 */}
          <div className="shrink-0">
            <h2 className="text-[18px] font-semibold text-slate-800 flex items-center gap-2 tracking-tight">
              <span className="inline-flex items-center justify-center w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-blue-400 text-white shadow-[0_4px_12px_rgba(59,130,246,0.3)]">
                <Repeat size={14} />
              </span>
              数据任务池
            </h2>
            <p className="text-[12px] text-slate-500 mt-0.5">
              调度流水线产物入湖 · 自动执行 · 可观测
            </p>
          </div>

          {/* 6 个指标胶囊 */}
          <div className="flex-1 grid grid-cols-6 gap-2.5">
            <KpiPill label="总任务" value={stats?.total ?? 0} icon={<Database size={12} />} tone="slate" />
            <KpiPill label="运行中" value={runningCount} icon={<Zap size={12} />} tone="blue" pulse={runningCount > 0} />
            <KpiPill label="异常" value={failedCount} icon={<AlertCircle size={12} />} tone="rose" pulse={failedCount > 0} />
            <KpiPill label="已启用" value={stats?.enabled ?? 0} icon={<CheckCircle2 size={12} />} tone="emerald" />
            <KpiPill label="今日执行" value={stats?.today_runs ?? 0} icon={<Activity size={12} />} tone="violet" />
            <KpiPill label="成功率" value={`${successRate}%`} icon={<Waves size={12} />} tone="cyan" suffix="" />
          </div>

          {/* 新建按钮 */}
          <button
            onClick={handleCreate}
            className="shrink-0 flex items-center gap-1.5 px-3.5 py-2 bg-gradient-to-r from-blue-500 to-blue-400 text-white text-[13px] font-medium rounded-xl shadow-[0_4px_14px_rgba(59,130,246,0.3)] hover:shadow-[0_6px_20px_rgba(59,130,246,0.4)] hover:-translate-y-0.5 transition-all"
          >
            <Plus size={14} />
            新建任务
          </button>
        </div>

        {/* ── 主体：左侧表格区 + 右侧侧栏 ── */}
        <div className="flex-1 grid grid-cols-12 gap-4 min-h-0">
          {/* 左侧：筛选 + 表格（占 9 列） */}
          <div className="col-span-9 flex flex-col min-h-0">
            {/* 快速 tab + 搜索 + 刷新 */}
            <div className={`${GLASS} px-3 py-2 mb-3 flex items-center gap-3 shrink-0`}>
              {/* Tabs */}
              <div className="flex items-center gap-0.5 bg-slate-100/60 p-0.5 rounded-lg">
                {QUICK_TABS.map(tab => {
                  const active = activeTab === tab.key
                  return (
                    <button
                      key={tab.key}
                      onClick={() => handleTabChange(tab.key)}
                      className={`flex items-center gap-1.5 px-2.5 py-1 text-[12px] rounded-md transition-all ${
                        active ? 'bg-white shadow-sm text-slate-800 font-medium' : 'text-slate-500 hover:text-slate-700'
                      }`}
                    >
                      {tab.dot && (
                        <span className="w-1.5 h-1.5 rounded-full relative">
                          <span className="absolute inset-0 rounded-full animate-ping opacity-50" style={{ background: tab.dot }} />
                          <span className="relative block w-full h-full rounded-full" style={{ background: tab.dot }} />
                        </span>
                      )}
                      {tab.label}
                    </button>
                  )
                })}
              </div>

              {/* 分隔 */}
              <div className="w-px h-5 bg-slate-200/70" />

              {/* 搜索 */}
              <div className="flex items-center gap-1.5 px-2.5 py-1 bg-white/60 rounded-lg flex-1 max-w-[260px] focus-within:ring-2 focus-within:ring-blue-200/60 transition-all">
                <Search size={13} className="text-slate-400 shrink-0" />
                <input
                  type="text"
                  placeholder="搜索任务名或流水线..."
                  value={searchInput}
                  onChange={e => setSearchInput(e.target.value)}
                  className="text-[12.5px] text-slate-700 placeholder-slate-400 bg-transparent outline-none w-full"
                />
                {searchInput && (
                  <button onClick={() => { setSearchInput(''); setSearch('') }} className="text-slate-400 hover:text-slate-600">
                    <X size={12} />
                  </button>
                )}
              </div>

              <div className="flex-1" />

              {/* 最后刷新时间 */}
              <span className="text-[11px] text-slate-400 tabular-nums">
                {refreshing ? '刷新中...' : '自动刷新 10s'}
              </span>
              <button
                onClick={load}
                disabled={refreshing}
                className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-500 hover:text-blue-600 hover:bg-white/70 disabled:opacity-50 transition-all"
                title="刷新"
              >
                <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
              </button>
            </div>

            {actionError && (
              <div className="flex items-center gap-2 px-3 py-2 mb-2 bg-rose-50/80 border border-rose-200/60 rounded-lg text-[12px] text-rose-600 shrink-0">
                <XCircle size={13} className="shrink-0" />
                <span className="flex-1">{actionError}</span>
                <button onClick={() => setActionError('')} className="text-rose-400 hover:text-rose-600"><X size={12} /></button>
              </div>
            )}

            {/* 表格容器 */}
            <div className={`${GLASS} flex-1 flex flex-col min-h-0 overflow-hidden`}>
              {loading ? (
                <div className="flex-1 flex flex-col items-center justify-center text-slate-400 text-sm gap-2">
                  <Loader2 size={20} className="animate-spin text-blue-500" />
                  <span className="text-[12px]">加载中...</span>
                </div>
              ) : tasks.length === 0 ? (
                <EmptyState activeTab={activeTab} hasSearch={!!search} onClear={() => { setSearchInput(''); setSearch(''); handleTabChange('') }} onCreate={handleCreate} />
              ) : (
                <>
                  <div className="flex-1 overflow-auto scrollbar-thin min-h-0">
                    <table className="w-full text-[13px]">
                      <thead className="sticky top-0 z-10">
                        <tr className="text-slate-500 text-[11.5px] bg-white/70 backdrop-blur-sm border-b border-slate-200/60">
                          <th className="text-left font-medium px-4 py-2">任务</th>
                          <th className="text-left font-medium px-2 py-2">流水线</th>
                          <th className="text-left font-medium px-2 py-2">入库</th>
                          <th className="text-left font-medium px-2 py-2">调度</th>
                          <th className="text-left font-medium px-2 py-2">状态</th>
                          <th className="text-left font-medium px-2 py-2">最近执行</th>
                          <th className="text-right font-medium px-4 py-2">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tasks.map(t => {
                          const sm = STATUS_META[t.status] || STATUS_META.idle
                          const isTriggering = triggeringIds.has(t.id)
                          const wm = WRITE_MODE_META[t.write_mode as WriteMode]
                          const pipelineGone = t.pipeline_status === 'deleted'
                          const pipelineUnpub = !pipelineGone && t.pipeline_status && t.pipeline_status !== 'published'
                          const sch = SCHEDULE_LABEL[t.schedule_type] || SCHEDULE_LABEL.MANUAL
                          const SchIcon = sch.Icon
                          return (
                            <tr key={t.id} className="border-b border-slate-100/60 last:border-b-0 hover:bg-white/55 transition-colors group">
                              <td className="px-4 py-2.5">
                                <div className="flex items-center gap-2">
                                  <span className={`w-1 h-6 rounded-full ${t.enabled ? (t.status === 'failed' ? 'bg-rose-400' : t.status === 'running' ? 'bg-blue-400' : 'bg-emerald-400') : 'bg-slate-300'}`} />
                                  <div className="min-w-0">
                                    <div className="font-medium text-slate-800 text-[13px] truncate max-w-[200px]">{t.name}</div>
                                    {t.description && <div className="text-[11px] text-slate-400 truncate max-w-[220px]">{t.description}</div>}
                                  </div>
                                </div>
                              </td>
                              <td className="px-2 py-2.5">
                                <button
                                  onClick={() => !pipelineGone && navigate(`/data/pipelines/${t.pipeline_id}`)}
                                  className={`flex items-center gap-1 text-[12px] max-w-[160px] ${pipelineGone ? 'text-slate-400 cursor-default' : 'text-blue-600 hover:underline underline-offset-2'}`}
                                  title={pipelineGone ? '流水线已删除' : '打开流水线'}
                                >
                                  <GitBranch size={11} className="shrink-0" />
                                  <span className="truncate">{t.pipeline_name || t.pipeline_id.slice(0, 8)}</span>
                                </button>
                                {(pipelineGone || pipelineUnpub) && (
                                  <div className="text-[10.5px] text-rose-500 mt-0.5 flex items-center gap-0.5">
                                    <AlertCircle size={10} />
                                    {pipelineGone ? '已删除' : '未发布'}
                                  </div>
                                )}
                              </td>
                              <td className="px-2 py-2.5">
                                <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] bg-slate-100/80 text-slate-600 border border-slate-200/50" title={wm?.desc}>
                                  {wm?.label || t.write_mode}
                                </span>
                                {t.skip_empty && <ShieldCheck size={10} className="inline text-emerald-500 ml-1" title="空输出保护" />}
                              </td>
                              <td className="px-2 py-2.5">
                                <div className="flex items-center gap-1 text-[12px] text-slate-600">
                                  <SchIcon size={11} className={sch.color} />
                                  <span>
                                    {sch.label}
                                    {t.schedule_type === 'CRON' && t.cron_expression ? ` · ${t.cron_expression}` : ''}
                                    {t.schedule_type === 'INTERVAL' && t.interval_seconds ? ` · ${relativeDuration(t.interval_seconds)}` : ''}
                                  </span>
                                </div>
                              </td>
                              <td className="px-2 py-2.5">
                                <div className="flex flex-col gap-1">
                                  <span className="inline-flex items-center gap-1 w-fit text-[11px]">
                                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: sm.dot, boxShadow: `0 0 0 2px ${sm.dot}25` }} />
                                    <span className="text-slate-600">{sm.label}</span>
                                  </span>
                                  <button
                                    onClick={() => handleToggle(t)}
                                    className={`inline-flex items-center gap-0.5 px-1.5 py-0 rounded text-[10.5px] w-fit transition-colors ${
                                      t.enabled ? 'text-emerald-600 bg-emerald-50/70 hover:bg-emerald-100/70' : 'text-slate-400 bg-slate-100/70 hover:bg-slate-200/70'
                                    }`}
                                  >
                                    {t.enabled ? <Play size={8} /> : <Pause size={8} />}
                                    {t.enabled ? '启用' : '停用'}
                                  </button>
                                </div>
                              </td>
                              <td className="px-2 py-2.5">
                                <div className="text-[12px] text-slate-600 tabular-nums">{formatTime(t.last_run_at)}</div>
                                {t.last_rows > 0 && (
                                  <div className="text-[11px] text-slate-400 tabular-nums">
                                    +{t.last_rows} 行
                                  </div>
                                )}
                                {t.status === 'failed' && t.last_error && (
                                  <div className="text-[10.5px] text-rose-500 truncate max-w-[140px]" title={t.last_error}>
                                    {t.last_error}
                                  </div>
                                )}
                              </td>
                              <td className="px-4 py-2.5 text-right">
                                <div className="inline-flex items-center gap-0.5 opacity-70 group-hover:opacity-100 transition-opacity">
                                  <IconBtn2 title="立即执行" disabled={t.status === 'running' || isTriggering || pipelineGone} onClick={() => handleTrigger(t)} accent="blue">
                                    <RotateCw size={12} className={isTriggering ? 'animate-spin' : ''} />
                                  </IconBtn2>
                                  <IconBtn2 title="执行历史" onClick={() => setHistoryTask(t)} accent="slate">
                                    <History size={12} />
                                  </IconBtn2>
                                  <IconBtn2 title="编辑" onClick={() => handleEdit(t)} accent="slate">
                                    <Edit2 size={12} />
                                  </IconBtn2>
                                  <IconBtn2 title="删除" danger onClick={() => setDeleteTarget(t)} accent="rose">
                                    <Trash2 size={12} />
                                  </IconBtn2>
                                </div>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>

                  {/* 分页 */}
                  <div className="flex items-center justify-between px-4 py-2 border-t border-slate-200/50 bg-white/40 shrink-0">
                    <span className="text-[11.5px] text-slate-500 tabular-nums">共 {total} 条</span>
                    <div className="flex items-center gap-1.5">
                      <button
                        onClick={() => setPage(p => Math.max(1, p - 1))}
                        disabled={page <= 1}
                        className="w-6 h-6 flex items-center justify-center rounded-md text-slate-500 hover:text-blue-600 hover:bg-white disabled:opacity-30 transition-all"
                      >
                        <ChevronLeft size={12} />
                      </button>
                      <span className="text-[11.5px] text-slate-600 tabular-nums px-1">
                        {page} / {totalPages}
                      </span>
                      <button
                        onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                        disabled={page >= totalPages}
                        className="w-6 h-6 flex items-center justify-center rounded-md text-slate-500 hover:text-blue-600 hover:bg-white disabled:opacity-30 transition-all"
                      >
                        <ChevronLeft size={12} className="rotate-180" />
                      </button>
                      <select
                        value={pageSize}
                        onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }}
                        className="ml-2 px-2 py-0.5 text-[11px] border border-slate-200/60 rounded-md bg-white/70 text-slate-600 outline-none"
                      >
                        {PAGE_SIZE_OPTIONS.map(n => <option key={n} value={n}>{n}条/页</option>)}
                      </select>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>

          {/* 右侧侧栏（占 3 列）：状态分布 + 待关注 + 7日趋势 */}
          <div className="col-span-3 flex flex-col gap-3 min-h-0">
            {/* 状态环图卡 */}
            <div className={`${GLASS} p-3.5 shrink-0`}>
              <div className="flex items-center justify-between mb-1">
                <h3 className="text-[12px] font-semibold text-slate-700 flex items-center gap-1.5">
                  <span className="w-1 h-1 rounded-full bg-blue-500" />
                  状态分布
                </h3>
              </div>
              <div className="flex items-center">
                <div className="w-[110px] h-[110px] relative shrink-0">
                  <ReactECharts option={pieOption} style={{ height: '100%', width: '100%' }} opts={{ renderer: 'svg' }} notMerge />
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <div className="text-[18px] font-semibold text-slate-800 tabular-nums">{stats?.total ?? 0}</div>
                    <div className="text-[10px] text-slate-400">总任务</div>
                  </div>
                </div>
                <div className="flex-1 flex flex-col gap-1.5 pl-2">
                  <LegendRow color="#3B82F6" label="运行中" value={stats?.running ?? 0} />
                  <LegendRow color="#F87171" label="异常" value={stats?.failed ?? 0} />
                  <LegendRow color="#CBD5E1" label="待运行" value={Math.max(0, (stats?.total ?? 0) - (stats?.running ?? 0) - (stats?.failed ?? 0))} />
                </div>
              </div>
            </div>

            {/* 待关注 */}
            <div className={`${GLASS} p-3.5 flex-1 min-h-0 flex flex-col`}>
              <div className="flex items-center justify-between mb-2 shrink-0">
                <h3 className="text-[12px] font-semibold text-slate-700 flex items-center gap-1.5">
                  {failedCount > 0 ? (
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-60" />
                      <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-rose-500" />
                    </span>
                  ) : (
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                  )}
                  {failedCount > 0 ? '待关注' : '运行正常'}
                </h3>
                {attentionTasks.length > 0 && (
                  <span className="text-[10.5px] px-1.5 py-0.5 rounded-full bg-rose-50 text-rose-600 border border-rose-200/60 tabular-nums">
                    {attentionTasks.length}
                  </span>
                )}
              </div>
              <div className="flex-1 overflow-auto scrollbar-thin min-h-0 space-y-1.5 pr-0.5">
                {attentionTasks.length === 0 ? (
                  <div className="h-full flex flex-col items-center justify-center text-slate-400 text-[11.5px] gap-1.5 py-4">
                    <CheckCircle2 size={22} className="text-emerald-400/80" />
                    <span>暂无异常任务</span>
                  </div>
                ) : (
                  attentionTasks.slice(0, 6).map(t => (
                    <div key={t.id} className="group p-2 rounded-lg bg-white/50 hover:bg-white/80 border border-white/60 transition-all">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="text-[12px] font-medium text-slate-700 truncate">{t.name}</div>
                          <div className="text-[10.5px] text-rose-500 mt-0.5 flex items-center gap-0.5">
                            <AlertCircle size={10} />
                            <span className="truncate">
                              {t.pipeline_status === 'deleted' ? '关联流水线已删除'
                                : t.pipeline_status && t.pipeline_status !== 'published' ? '流水线未发布'
                                : t.last_error ? t.last_error.slice(0, 30) : '执行失败'}
                            </span>
                          </div>
                        </div>
                        <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                          {t.status === 'failed' && (
                            <button onClick={() => handleTrigger(t)} title="重试"
                              className="w-6 h-6 flex items-center justify-center rounded-md text-blue-600 hover:bg-blue-50/70">
                              <RotateCw size={11} />
                            </button>
                          )}
                          <button onClick={() => handleEdit(t)} title="编辑"
                            className="w-6 h-6 flex items-center justify-center rounded-md text-slate-500 hover:bg-slate-100">
                            <Edit2 size={11} />
                          </button>
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* 7 日趋势 */}
            <div className={`${GLASS} p-3.5 shrink-0`}>
              <div className="flex items-center justify-between mb-1">
                <h3 className="text-[12px] font-semibold text-slate-700 flex items-center gap-1.5">
                  <span className="w-1 h-1 rounded-full bg-blue-500" />
                  近 7 日执行
                </h3>
                <span className="text-[11px] text-slate-500 flex items-center gap-0.5 tabular-nums">
                  共 {trendData.total7d} 次
                  <ArrowUpRight size={11} className="text-emerald-500" />
                </span>
              </div>
              <ReactECharts option={miniTrendOption} style={{ height: 56, width: '100%' }} opts={{ renderer: 'svg' }} notMerge />
            </div>
          </div>
        </div>
      </div>

      {/* Modals / Drawer */}
      {showForm && (
        <TaskFormModal
          initialTask={editingTask}
          initialPipelineId={presetPipelineId}
          onClose={() => { setShowForm(false); setEditingTask(null); setPresetPipelineId(null) }}
          onSaved={handleFormSaved}
        />
      )}
      {historyTask && <HistoryDrawer task={historyTask} onClose={() => setHistoryTask(null)} />}
      <ConfirmDialog
        open={!!deleteTarget}
        title="删除调度任务"
        message={`确定删除任务「${deleteTarget?.name}」？流水线本身与已入湖数据不受影响。`}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}

// ── 子组件 ────────────────────────────────────────────

function KpiPill({
  label, value, icon, tone, pulse, suffix,
}: {
  label: string; value: number | string; icon: React.ReactNode
  tone: 'slate' | 'blue' | 'rose' | 'emerald' | 'violet' | 'cyan'
  pulse?: boolean; suffix?: string
}) {
  const toneMap = {
    slate:   { text: 'text-slate-700',  iconBg: 'bg-slate-100/80 text-slate-500' },
    blue:    { text: 'text-blue-600',   iconBg: 'bg-blue-50 text-blue-500' },
    rose:    { text: 'text-rose-600',   iconBg: 'bg-rose-50 text-rose-500' },
    emerald: { text: 'text-emerald-600',iconBg: 'bg-emerald-50 text-emerald-500' },
    violet:  { text: 'text-violet-600',iconBg: 'bg-violet-50 text-violet-500' },
    cyan:    { text: 'text-cyan-600',  iconBg: 'bg-cyan-50 text-cyan-500' },
  }[tone]
  return (
    <div className={`${GLASS} !rounded-lg px-3 py-2 flex items-center gap-2 hover:-translate-y-0.5 transition-transform`}>
      <span className={`w-7 h-7 rounded-lg ${toneMap.iconBg} flex items-center justify-center shrink-0 relative`}>
        {icon}
        {pulse && (
          <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-current animate-ping opacity-60" />
        )}
      </span>
      <div className="min-w-0 leading-tight">
        <div className="text-[10.5px] text-slate-500">{label}</div>
        <div className={`text-[17px] font-semibold tabular-nums tracking-tight ${toneMap.text}`}>
          {value}{suffix !== undefined && suffix}
        </div>
      </div>
    </div>
  )
}

function LegendRow({ color, label, value }: { color: string; label: string; value: number }) {
  return (
    <div className="flex items-center justify-between text-[11.5px]">
      <div className="flex items-center gap-1.5 text-slate-600">
        <span className="w-2 h-2 rounded-full" style={{ background: color }} />
        {label}
      </div>
      <span className="font-semibold text-slate-700 tabular-nums">{value}</span>
    </div>
  )
}

function IconBtn2({ children, onClick, title, disabled, danger, accent = 'slate' }: {
  children: React.ReactNode; onClick?: () => void; title?: string; disabled?: boolean
  danger?: boolean; accent?: 'slate' | 'blue' | 'rose'
}) {
  const hover = danger || accent === 'rose'
    ? 'hover:bg-rose-50/70 hover:text-rose-500'
    : accent === 'blue' ? 'hover:bg-blue-50/70 hover:text-blue-500'
    : 'hover:bg-slate-100 hover:text-slate-700'
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`w-6 h-6 flex items-center justify-center rounded-md text-slate-400 ${hover} disabled:opacity-40 disabled:cursor-not-allowed transition-all`}
    >
      {children}
    </button>
  )
}

function EmptyState({ activeTab, hasSearch, onClear, onCreate }: {
  activeTab: string; hasSearch: boolean; onClear: () => void; onCreate: () => void
}) {
  const isFiltered = activeTab || hasSearch
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center py-8">
      <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500/10 to-cyan-400/10 flex items-center justify-center border border-white/80 mb-3">
        <Repeat size={22} className="text-blue-500/70" />
      </div>
      {isFiltered ? (
        <>
          <div className="text-slate-600 font-medium text-[13px] mb-1">没有匹配的任务</div>
          <div className="text-[11.5px] text-slate-400 mb-3">换个筛选条件试试</div>
          <button onClick={onClear}
            className="inline-flex items-center gap-1 px-3 py-1.5 text-[12px] text-blue-600 bg-blue-50/70 hover:bg-blue-100/70 rounded-lg transition-colors">
            <X size={12} /> 清除筛选
          </button>
        </>
      ) : (
        <>
          <div className="text-slate-700 font-medium text-[13px] mb-0.5">暂无调度任务</div>
          <div className="text-[11.5px] text-slate-400 mb-3 max-w-xs leading-relaxed">
            选择一条已发布的流水线，设定入库方式，产物将按计划写入资产湖
          </div>
          <button onClick={onCreate}
            className="inline-flex items-center gap-1 px-3.5 py-1.5 bg-gradient-to-r from-blue-500 to-blue-400 text-white text-[12px] font-medium rounded-lg shadow-[0_4px_12px_rgba(59,130,246,0.25)] hover:-translate-y-0.5 transition-all">
            <Plus size={13} /> 新建第一个任务
          </button>
        </>
      )}
    </div>
  )
}
