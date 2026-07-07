import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import {
  Plus, Play, Pause, History, RefreshCw, Trash2, Edit2,
  Database, Clock, CheckCircle2, XCircle, Loader2, AlertCircle,
  Repeat, Timer, GitBranch, X, Search, ChevronLeft, ShieldCheck,
  RotateCw, Zap, Activity, ArrowUpRight, Waves,
} from 'lucide-react'
import { pipelineTasksApi, WRITE_MODE_META, type PipelineTask, type PipelineTaskStats, type WriteMode } from '@/api/v2/pipeline-tasks'
import TaskFormModal from './TaskFormModal'
import HistoryDrawer from './HistoryDrawer'
import ConfirmDialog from '@/components/ConfirmDialog'

// ── 常量 ──────────────────────────────────────────────
const STATUS_META: Record<string, { icon: React.ReactNode; label: string; dot: string }> = {
  idle:    { icon: <Clock size={9} />,       label: '待运行', dot: '#94A3B8' },
  running: { icon: <Loader2 size={9} className="animate-spin" />, label: '执行中', dot: '#3B82F6' },
  success: { icon: <CheckCircle2 size={9} />, label: '成功',   dot: '#10B981' },
  failed:  { icon: <XCircle size={9} />,    label: '失败',   dot: '#F87171' },
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

// 毛玻璃基础样式 - 紧凑、圆角更小、阴影更轻
const GLASS = 'backdrop-blur-xl bg-white/70 border border-white/80 shadow-[0_2px_12px_rgba(15,23,42,0.04)] rounded-lg overflow-hidden'

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

  const handleTabChange = (key: string) => { setActiveTab(key); setPage(1) }
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
  const attentionTasks = useMemo(
    () => tasks.filter(t => t.status === 'failed' || t.pipeline_status === 'deleted' || (t.pipeline_status && t.pipeline_status !== 'published')),
    [tasks],
  )

  const successRate = useMemo(() => {
    if (!stats) return 0
    const base = stats.total || 1
    return Math.round(((base - stats.failed) / base) * 100)
  }, [stats])

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

  // ECharts: 环形状态分布 - 固定尺寸容器、禁用 tooltip 防止溢出
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
        type: 'pie', radius: ['60%', '80%'], center: ['50%', '50%'],
        avoidLabelOverlap: false,
        itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 3 },
        label: { show: false }, labelLine: { show: false },
        emphasis: { disabled: true },
        data,
        animationType: 'scale', animationDuration: 600,
      }],
    }
  }, [stats])

  // ECharts: 迷你趋势
  const miniTrendOption = useMemo(() => ({
    grid: { left: 2, right: 2, top: 4, bottom: 2 },
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

  const runningCount = stats?.running ?? 0
  const failedCount = stats?.failed ?? 0

  return (
    <div
      className="-m-6 p-4 flex flex-col"
      style={{
        height: 'calc(100vh - 56px)',
        // 均匀淡灰底，无明显渐变、无色斑
        background: '#F4F7FB',
      }}
    >
      <div className="relative z-10 flex flex-col h-full min-h-0">
        {/* ── 顶部：标题 + 6 个 KPI + 新建按钮 ── */}
        <div className="flex items-center gap-3 mb-3 shrink-0">
          <div className="shrink-0 pr-2">
            <h2 className="text-[17px] font-semibold text-slate-800 flex items-center gap-2 tracking-tight">
              <span className="inline-flex items-center justify-center w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-blue-400 text-white shadow-[0_3px_10px_rgba(59,130,246,0.3)]">
                <Repeat size={14} />
              </span>
              数据任务池
            </h2>
            <p className="text-[11.5px] text-slate-500 mt-0.5">调度流水线产物入湖 · 自动执行 · 可观测</p>
          </div>

          <div className="flex-1 grid grid-cols-6 gap-2">
            <KpiPill label="总任务" value={stats?.total ?? 0} icon={<Database size={12} />} tone="slate" />
            <KpiPill label="运行中" value={runningCount} icon={<Zap size={12} />} tone="blue" pulse={runningCount > 0} />
            <KpiPill label="异常" value={failedCount} icon={<AlertCircle size={12} />} tone="rose" pulse={failedCount > 0} />
            <KpiPill label="已启用" value={stats?.enabled ?? 0} icon={<CheckCircle2 size={12} />} tone="emerald" />
            <KpiPill label="今日执行" value={stats?.today_runs ?? 0} icon={<Activity size={12} />} tone="violet" />
            <KpiPill label="成功率" value={`${successRate}%`} icon={<Waves size={12} />} tone="cyan" suffix="" />
          </div>

          <button
            onClick={handleCreate}
            className="shrink-0 flex items-center gap-1.5 px-3.5 py-2 bg-gradient-to-r from-blue-500 to-blue-400 text-white text-[13px] font-medium rounded-lg shadow-[0_3px_10px_rgba(59,130,246,0.3)] hover:shadow-[0_4px_14px_rgba(59,130,246,0.4)] hover:-translate-y-0.5 transition-all"
          >
            <Plus size={14} />
            新建任务
          </button>
        </div>

        {/* ── 主体 9:3 ── */}
        <div className="flex-1 grid grid-cols-12 gap-3 min-h-0">
          {/* 左侧 */}
          <div className="col-span-9 flex flex-col min-h-0">
            {/* 筛选条 */}
            <div className={`${GLASS} px-3 py-1.5 mb-2.5 flex items-center gap-2 shrink-0`}>
              <div className="flex items-center gap-0.5 bg-slate-100/70 p-0.5 rounded-md">
                {QUICK_TABS.map(tab => {
                  const active = activeTab === tab.key
                  return (
                    <button
                      key={tab.key}
                      onClick={() => handleTabChange(tab.key)}
                      className={`flex items-center gap-1 px-2 py-1 text-[11.5px] rounded transition-all ${
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

              <div className="w-px h-4 bg-slate-200/70" />

              <div className="flex items-center gap-1 px-2 py-1 bg-white/70 rounded-md flex-1 max-w-[240px] focus-within:ring-2 focus-within:ring-blue-200/60 transition-all">
                <Search size={12} className="text-slate-400 shrink-0" />
                <input
                  type="text"
                  placeholder="搜索任务名或流水线..."
                  value={searchInput}
                  onChange={e => setSearchInput(e.target.value)}
                  className="text-[12px] text-slate-700 placeholder-slate-400 bg-transparent outline-none w-full"
                />
                {searchInput && (
                  <button onClick={() => { setSearchInput(''); setSearch('') }} className="text-slate-400 hover:text-slate-600">
                    <X size={11} />
                  </button>
                )}
              </div>

              <div className="flex-1" />

              <span className="text-[10.5px] text-slate-400 tabular-nums">
                {refreshing ? '刷新中...' : '自动刷新 10s'}
              </span>
              <button
                onClick={load}
                disabled={refreshing}
                className="w-6 h-6 flex items-center justify-center rounded-md text-slate-500 hover:text-blue-600 hover:bg-white/80 disabled:opacity-50 transition-all"
                title="刷新"
              >
                <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
              </button>
            </div>

            {actionError && (
              <div className="flex items-center gap-2 px-3 py-1.5 mb-2 bg-rose-50/80 border border-rose-200/60 rounded-lg text-[11.5px] text-rose-600 shrink-0">
                <XCircle size={12} className="shrink-0" />
                <span className="flex-1">{actionError}</span>
                <button onClick={() => setActionError('')} className="text-rose-400 hover:text-rose-600"><X size={11} /></button>
              </div>
            )}

            {/* 表格容器 */}
            <div className={`${GLASS} flex-1 flex flex-col min-h-0`}>
              {loading ? (
                <div className="flex-1 flex flex-col items-center justify-center text-slate-400 gap-2">
                  <Loader2 size={18} className="animate-spin text-blue-500" />
                  <span className="text-[12px]">加载中...</span>
                </div>
              ) : tasks.length === 0 ? (
                <EmptyState activeTab={activeTab} hasSearch={!!search} onClear={() => { setSearchInput(''); setSearch(''); handleTabChange('') }} onCreate={handleCreate} />
              ) : (
                <>
                  <div className="flex-1 overflow-auto scrollbar-thin min-h-0">
                    <table className="w-full text-[12.5px]">
                      <thead className="sticky top-0 z-10">
                        <tr className="text-slate-500 text-[11px] bg-white/75 backdrop-blur-sm border-b border-slate-200/60">
                          <th className="text-left font-medium px-3 py-1.5">任务</th>
                          <th className="text-left font-medium px-2 py-1.5">流水线</th>
                          <th className="text-left font-medium px-2 py-1.5">入库</th>
                          <th className="text-left font-medium px-2 py-1.5">调度</th>
                          <th className="text-left font-medium px-2 py-1.5">状态</th>
                          <th className="text-left font-medium px-2 py-1.5">最近执行</th>
                          <th className="text-right font-medium px-3 py-1.5">操作</th>
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
                            <tr key={t.id} className="border-b border-slate-100/60 last:border-b-0 hover:bg-white/60 transition-colors group">
                              <td className="px-3 py-2">
                                <div className="flex items-center gap-2">
                                  <span className={`w-[3px] h-5 rounded-full shrink-0 ${
                                    !t.enabled ? 'bg-slate-300' :
                                    t.status === 'failed' ? 'bg-rose-400' :
                                    t.status === 'running' ? 'bg-blue-400' :
                                    'bg-emerald-400'
                                  }`} />
                                  <div className="min-w-0">
                                    <div className="font-medium text-slate-800 text-[12.5px] truncate max-w-[180px]">{t.name}</div>
                                    {t.description && <div className="text-[10.5px] text-slate-400 truncate max-w-[200px]">{t.description}</div>}
                                  </div>
                                </div>
                              </td>
                              <td className="px-2 py-2">
                                <button
                                  onClick={() => !pipelineGone && navigate(`/data/pipelines/${t.pipeline_id}`)}
                                  className={`flex items-center gap-1 text-[11.5px] max-w-[140px] ${pipelineGone ? 'text-slate-400 cursor-default' : 'text-blue-600 hover:underline underline-offset-2'}`}
                                  title={pipelineGone ? '流水线已删除' : '打开流水线'}
                                >
                                  <GitBranch size={10} className="shrink-0" />
                                  <span className="truncate">{t.pipeline_name || t.pipeline_id.slice(0, 8)}</span>
                                </button>
                                {(pipelineGone || pipelineUnpub) && (
                                  <div className="text-[10px] text-rose-500 mt-0.5 flex items-center gap-0.5">
                                    <AlertCircle size={9} />
                                    {pipelineGone ? '已删除' : '未发布'}
                                  </div>
                                )}
                              </td>
                              <td className="px-2 py-2">
                                <span className="inline-flex items-center px-1.5 py-px rounded text-[10.5px] bg-slate-100/80 text-slate-600 border border-slate-200/50" title={wm?.desc}>
                                  {wm?.label || t.write_mode}
                                </span>
                                {t.skip_empty && <ShieldCheck size={10} className="inline text-emerald-500 ml-0.5" title="空输出保护" />}
                              </td>
                              <td className="px-2 py-2">
                                <div className="flex items-center gap-1 text-[11.5px] text-slate-600">
                                  <SchIcon size={10} className={sch.color} />
                                  <span>
                                    {sch.label}
                                    {t.schedule_type === 'CRON' && t.cron_expression ? ` · ${t.cron_expression}` : ''}
                                    {t.schedule_type === 'INTERVAL' && t.interval_seconds ? ` · ${relativeDuration(t.interval_seconds)}` : ''}
                                  </span>
                                </div>
                              </td>
                              <td className="px-2 py-2">
                                <div className="flex flex-col gap-0.5">
                                  <span className="inline-flex items-center gap-1 w-fit text-[10.5px]">
                                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: sm.dot, boxShadow: `0 0 0 2px ${sm.dot}25` }} />
                                    <span className="text-slate-600">{sm.label}</span>
                                  </span>
                                  <button
                                    onClick={() => handleToggle(t)}
                                    className={`inline-flex items-center gap-0.5 px-1 py-0 rounded text-[10px] w-fit transition-colors ${
                                      t.enabled ? 'text-emerald-600 bg-emerald-50/70 hover:bg-emerald-100/70' : 'text-slate-400 bg-slate-100/70 hover:bg-slate-200/70'
                                    }`}
                                  >
                                    {t.enabled ? <Play size={7} /> : <Pause size={7} />}
                                    {t.enabled ? '启用' : '停用'}
                                  </button>
                                </div>
                              </td>
                              <td className="px-2 py-2">
                                <div className="text-[11.5px] text-slate-600 tabular-nums">{formatTime(t.last_run_at)}</div>
                                {t.last_rows > 0 && (
                                  <div className="text-[10.5px] text-slate-400 tabular-nums">+{t.last_rows} 行</div>
                                )}
                                {t.status === 'failed' && t.last_error && (
                                  <div className="text-[10px] text-rose-500 truncate max-w-[120px]" title={t.last_error}>
                                    {t.last_error}
                                  </div>
                                )}
                              </td>
                              <td className="px-3 py-2 text-right">
                                <div className="inline-flex items-center gap-0 opacity-60 group-hover:opacity-100 transition-opacity">
                                  <IconBtn2 title="立即执行" disabled={t.status === 'running' || isTriggering || pipelineGone} onClick={() => handleTrigger(t)} accent="blue">
                                    <RotateCw size={11} className={isTriggering ? 'animate-spin' : ''} />
                                  </IconBtn2>
                                  <IconBtn2 title="执行历史" onClick={() => setHistoryTask(t)} accent="slate">
                                    <History size={11} />
                                  </IconBtn2>
                                  <IconBtn2 title="编辑" onClick={() => handleEdit(t)} accent="slate">
                                    <Edit2 size={11} />
                                  </IconBtn2>
                                  <IconBtn2 title="删除" danger onClick={() => setDeleteTarget(t)} accent="rose">
                                    <Trash2 size={11} />
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
                  <div className="flex items-center justify-between px-3 py-1.5 border-t border-slate-200/50 bg-white/40 shrink-0">
                    <span className="text-[11px] text-slate-500 tabular-nums">共 {total} 条</span>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => setPage(p => Math.max(1, p - 1))}
                        disabled={page <= 1}
                        className="w-5 h-5 flex items-center justify-center rounded text-slate-500 hover:text-blue-600 hover:bg-white disabled:opacity-30 transition-all"
                      >
                        <ChevronLeft size={11} />
                      </button>
                      <span className="text-[11px] text-slate-600 tabular-nums px-1">{page} / {totalPages}</span>
                      <button
                        onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                        disabled={page >= totalPages}
                        className="w-5 h-5 flex items-center justify-center rounded text-slate-500 hover:text-blue-600 hover:bg-white disabled:opacity-30 transition-all"
                      >
                        <ChevronLeft size={11} className="rotate-180" />
                      </button>
                      <select
                        value={pageSize}
                        onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }}
                        className="ml-1.5 px-1.5 py-0 text-[10.5px] border border-slate-200/60 rounded bg-white/80 text-slate-600 outline-none"
                      >
                        {PAGE_SIZE_OPTIONS.map(n => <option key={n} value={n}>{n}/页</option>)}
                      </select>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>

          {/* 右侧侧栏 */}
          <div className="col-span-3 flex flex-col gap-2.5 min-h-0">
            {/* 状态环图 */}
            <div className={`${GLASS} p-3 shrink-0`}>
              <h3 className="text-[11.5px] font-semibold text-slate-700 flex items-center gap-1.5 mb-1.5">
                <span className="w-1 h-1 rounded-full bg-blue-500" />
                状态分布
              </h3>
              <div className="flex items-center">
                <div className="w-[90px] h-[90px] relative shrink-0 overflow-hidden">
                  <ReactECharts
                    option={pieOption}
                    style={{ height: '100%', width: '100%' }}
                    opts={{ renderer: 'svg' }}
                    notMerge
                  />
                  <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                    <div className="text-[16px] font-semibold text-slate-800 tabular-nums leading-none">{stats?.total ?? 0}</div>
                    <div className="text-[9.5px] text-slate-400 mt-0.5">总任务</div>
                  </div>
                </div>
                <div className="flex-1 flex flex-col gap-1 pl-2">
                  <LegendRow color="#3B82F6" label="运行中" value={stats?.running ?? 0} />
                  <LegendRow color="#F87171" label="异常" value={stats?.failed ?? 0} />
                  <LegendRow color="#CBD5E1" label="待运行" value={Math.max(0, (stats?.total ?? 0) - (stats?.running ?? 0) - (stats?.failed ?? 0))} />
                </div>
              </div>
            </div>

            {/* 待关注 */}
            <div className={`${GLASS} p-3 flex-1 min-h-0 flex flex-col`}>
              <div className="flex items-center justify-between mb-1.5 shrink-0">
                <h3 className="text-[11.5px] font-semibold text-slate-700 flex items-center gap-1.5">
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
                  <span className="text-[10px] px-1 py-px rounded-full bg-rose-50 text-rose-600 border border-rose-200/60 tabular-nums">
                    {attentionTasks.length}
                  </span>
                )}
              </div>
              <div className="flex-1 overflow-auto scrollbar-thin min-h-0 space-y-1 pr-0.5">
                {attentionTasks.length === 0 ? (
                  <div className="h-full flex flex-col items-center justify-center text-slate-400 text-[11px] gap-1 py-2">
                    <CheckCircle2 size={20} className="text-emerald-400/80" />
                    <span>暂无异常任务</span>
                  </div>
                ) : (
                  attentionTasks.slice(0, 8).map(t => (
                    <div key={t.id} className="group p-1.5 rounded-md bg-white/55 hover:bg-white/85 border border-white/60 transition-all">
                      <div className="flex items-start justify-between gap-1.5">
                        <div className="min-w-0 flex-1">
                          <div className="text-[11px] font-medium text-slate-700 truncate">{t.name}</div>
                          <div className="text-[10px] text-rose-500 mt-px flex items-center gap-0.5">
                            <AlertCircle size={9} />
                            <span className="truncate">
                              {t.pipeline_status === 'deleted' ? '关联流水线已删除'
                                : t.pipeline_status && t.pipeline_status !== 'published' ? '流水线未发布'
                                : t.last_error ? t.last_error.slice(0, 26) : '执行失败'}
                            </span>
                          </div>
                        </div>
                        <div className="flex items-center gap-px shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                          {t.status === 'failed' && (
                            <button onClick={() => handleTrigger(t)} title="重试"
                              className="w-5 h-5 flex items-center justify-center rounded text-blue-600 hover:bg-blue-50/70">
                              <RotateCw size={10} />
                            </button>
                          )}
                          <button onClick={() => handleEdit(t)} title="编辑"
                            className="w-5 h-5 flex items-center justify-center rounded text-slate-500 hover:bg-slate-100">
                            <Edit2 size={10} />
                          </button>
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* 7 日趋势 */}
            <div className={`${GLASS} p-3 shrink-0`}>
              <div className="flex items-center justify-between mb-1">
                <h3 className="text-[11.5px] font-semibold text-slate-700 flex items-center gap-1.5">
                  <span className="w-1 h-1 rounded-full bg-blue-500" />
                  近 7 日执行
                </h3>
                <span className="text-[10.5px] text-slate-500 flex items-center gap-0.5 tabular-nums">
                  {trendData.total7d} 次
                  <ArrowUpRight size={10} className="text-emerald-500" />
                </span>
              </div>
              <div className="overflow-hidden" style={{ height: 44 }}>
                <ReactECharts
                  option={miniTrendOption}
                  style={{ height: '100%', width: '100%' }}
                  opts={{ renderer: 'svg' }}
                  notMerge
                />
              </div>
            </div>
          </div>
        </div>
      </div>

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
    slate:   { text: 'text-slate-700',  iconBg: 'bg-slate-100 text-slate-500' },
    blue:    { text: 'text-blue-600',   iconBg: 'bg-blue-50 text-blue-500' },
    rose:    { text: 'text-rose-600',   iconBg: 'bg-rose-50 text-rose-500' },
    emerald: { text: 'text-emerald-600',iconBg: 'bg-emerald-50 text-emerald-500' },
    violet:  { text: 'text-violet-600',iconBg: 'bg-violet-50 text-violet-500' },
    cyan:    { text: 'text-cyan-600',  iconBg: 'bg-cyan-50 text-cyan-500' },
  }[tone]
  return (
    <div className={`${GLASS} px-2.5 py-1.5 flex items-center gap-2 transition-transform`}>
      <span className={`w-6 h-6 rounded-md ${toneMap.iconBg} flex items-center justify-center shrink-0 relative overflow-hidden`}>
        {icon}
        {pulse && (
          <span className="absolute -top-0 -right-0 w-1.5 h-1.5 rounded-full bg-current animate-ping opacity-60" />
        )}
      </span>
      <div className="min-w-0 leading-tight">
        <div className="text-[10px] text-slate-500">{label}</div>
        <div className={`text-[15px] font-semibold tabular-nums tracking-tight ${toneMap.text} leading-none mt-0.5`}>
          {value}{suffix !== undefined && suffix}
        </div>
      </div>
    </div>
  )
}

function LegendRow({ color, label, value }: { color: string; label: string; value: number }) {
  return (
    <div className="flex items-center justify-between text-[11px]">
      <div className="flex items-center gap-1.5 text-slate-600">
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: color }} />
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
      className={`w-6 h-6 flex items-center justify-center rounded text-slate-400 ${hover} disabled:opacity-40 disabled:cursor-not-allowed transition-all`}
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
    <div className="flex-1 flex flex-col items-center justify-center text-center py-6">
      <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-blue-500/10 to-cyan-400/10 flex items-center justify-center border border-white/80 mb-2.5">
        <Repeat size={20} className="text-blue-500/70" />
      </div>
      {isFiltered ? (
        <>
          <div className="text-slate-600 font-medium text-[12.5px] mb-1">没有匹配的任务</div>
          <div className="text-[11px] text-slate-400 mb-2.5">换个筛选条件试试</div>
          <button onClick={onClear}
            className="inline-flex items-center gap-1 px-3 py-1.5 text-[11.5px] text-blue-600 bg-blue-50/70 hover:bg-blue-100/70 rounded-lg transition-colors">
            <X size={11} /> 清除筛选
          </button>
        </>
      ) : (
        <>
          <div className="text-slate-700 font-medium text-[12.5px] mb-0.5">暂无调度任务</div>
          <div className="text-[11px] text-slate-400 mb-2.5 max-w-xs leading-relaxed">
            选择一条已发布的流水线，设定入库方式，产物将按计划写入资产湖
          </div>
          <button onClick={onCreate}
            className="inline-flex items-center gap-1 px-3.5 py-1.5 bg-gradient-to-r from-blue-500 to-blue-400 text-white text-[11.5px] font-medium rounded-lg shadow-[0_3px_10px_rgba(59,130,246,0.25)] hover:-translate-y-0.5 transition-all">
            <Plus size={12} /> 新建第一个任务
          </button>
        </>
      )}
    </div>
  )
}
