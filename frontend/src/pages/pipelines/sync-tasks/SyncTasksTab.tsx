import { useState, useEffect, useCallback, useRef, useMemo, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import {
  Plus, History, RefreshCw, Trash2, Edit2,
  Database, Clock, CheckCircle2, XCircle, Loader2, AlertCircle,
  Repeat, Timer, GitBranch, X, Search, ChevronLeft, ShieldCheck,
  RotateCw, Activity, Waves, ExternalLink, Workflow, ListChecks,
  Boxes, Network, ArrowRight,
} from 'lucide-react'
import { pipelineTasksApi, WRITE_MODE_META, type PipelineFilterOption, type PipelineTask, type PipelineTaskRecentRun, type PipelineTaskStats, type WriteMode, type LakeImpact } from '@/api/v2/pipeline-tasks'
import TaskFormModal from './TaskFormModal'
import HistoryDrawer from './HistoryDrawer'
import GlobalHistoryModal from './GlobalHistoryModal'
import ConfirmDialog from '@/components/ConfirmDialog'

// ── 常量 ──────────────────────────────────────────────
const QUICK_TABS = [
  { key: '',          label: '全部' },
  { key: 'running',   label: '运行中' },
  { key: 'failed',    label: '异常' },
  { key: 'disabled',  label: '已停用' },
] as const

const PAGE_SIZE_OPTIONS = [10, 20, 50]

const SCHEDULE_LABEL: Record<string, { label: string; color: string; Icon: typeof Clock }> = {
  MANUAL:  { label: '手动', color: 'text-slate-500',  Icon: Clock },
  CRON:    { label: 'Cron', color: 'text-violet-500', Icon: Timer },
  INTERVAL:{ label: '间隔', color: 'text-blue-500',   Icon: Repeat },
}

const WRITE_MODE_TONE: Record<WriteMode, string> = {
  overwrite: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  append: 'border-sky-200 bg-sky-50 text-sky-700',
  upsert: 'border-violet-200 bg-violet-50 text-violet-700',
  append_dedup: 'border-amber-200 bg-amber-50 text-amber-700',
}

const PANEL = 'rounded-xl border border-slate-200 bg-white shadow-sm/50 overflow-hidden'
const RECENT_RUN_LIMIT = 30

function FlowArrow() {
  return (
    <div className="flex w-[clamp(0.625rem,1.1vw,1rem)] shrink-0 items-center" aria-hidden="true">
      <span className="h-px w-full border-t border-dashed border-slate-300" />
      <ArrowRight className="-ml-1 h-[clamp(0.625rem,1vw,0.875rem)] w-[clamp(0.625rem,1vw,0.875rem)] shrink-0 text-slate-400" />
    </div>
  )
}

function FlowNode({
  label, icon, active = false, onClick,
}: {
  label: string
  icon: ReactNode
  active?: boolean
  onClick?: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      className={`group inline-flex h-[clamp(2rem,2.7vw,2.25rem)] flex-none items-center justify-center gap-[clamp(0.25rem,0.4vw,0.375rem)] rounded-lg border px-[clamp(0.375rem,0.65vw,0.625rem)] text-[clamp(10px,0.8vw,11px)] font-semibold transition-colors ${
        active
          ? 'border-emerald-400 bg-emerald-50 text-emerald-800 shadow-[inset_0_0_0_1px_rgba(16,185,129,0.12)]'
          : 'border-teal-300 bg-teal-50/50 text-teal-800 hover:border-teal-400 hover:bg-teal-100/70'
      }`}
    >
      <span className={`grid h-[clamp(1.125rem,1.5vw,1.25rem)] w-[clamp(1.125rem,1.5vw,1.25rem)] shrink-0 place-items-center rounded-md [&_svg]:h-[clamp(0.6875rem,1vw,0.875rem)] [&_svg]:w-[clamp(0.6875rem,1vw,0.875rem)] ${active ? 'bg-emerald-600 text-white' : 'bg-teal-100 text-teal-700'}`}>
        {icon}
      </span>
      <span className="whitespace-nowrap leading-none" title={label}>{label}</span>
      {active && <span className="ml-0.5 shrink-0 rounded bg-emerald-600 px-[clamp(0.25rem,0.4vw,0.375rem)] py-0.5 text-[clamp(8px,0.65vw,9px)] font-medium leading-none text-white">当前</span>}
    </button>
  )
}

/** 后端裸时间戳按 UTC 处理：无时区标识则补 Z，避免被 JS 当成本地时间产生偏移 */
function toLocalDate(iso: string): Date {
  const hasTz = /(Z|[+-]\d\d:?\d\d)$/.test(iso)
  return new Date(hasTz ? iso : iso + 'Z')
}

function TimeInline({ iso, withSeconds }: { iso: string | null | undefined; withSeconds?: boolean }) {
  if (!iso) return <span className="whitespace-nowrap text-xs text-slate-400">—</span>
  try {
    const d = toLocalDate(iso)
    const p = (n: number) => String(n).padStart(2, '0')
    const date = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
    const time = withSeconds
      ? `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
      : `${p(d.getHours())}:${p(d.getMinutes())}`
    return <span className="whitespace-nowrap text-xs tabular-nums text-slate-600">{date} {time}</span>
  } catch {
    return <span className="whitespace-nowrap text-xs text-slate-500">{iso}</span>
  }
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
  const [showGlobalHistory, setShowGlobalHistory] = useState(false)
  const [editingTask, setEditingTask] = useState<PipelineTask | null>(null)
  const [historyTask, setHistoryTask] = useState<PipelineTask | null>(null)
  const [historyRunId, setHistoryRunId] = useState<string | null>(null)
  const [triggeringIds, setTriggeringIds] = useState<Set<string>>(new Set())
  const [deleteTarget, setDeleteTarget] = useState<PipelineTask | null>(null)
  const [actionError, setActionError] = useState('')
  const [presetPipelineId, setPresetPipelineId] = useState<string | null>(null)
  const [filterPipelineId, setFilterPipelineId] = useState(() => searchParams.get('pipeline_id') || '')
  const [pipelineOptions, setPipelineOptions] = useState<PipelineFilterOption[]>([])
  const quickTabsRef = useRef<HTMLDivElement>(null)
  const [quickIndicator, setQuickIndicator] = useState({ left: 0, width: 0 })
  const deepLinkedTaskId = searchParams.get('task_id')
  const deepLinkedRunId = searchParams.get('run_id')

  useEffect(() => {
    const pid = searchParams.get('pipeline')
    if (pid) {
      setPresetPipelineId(pid)
      setEditingTask(null)
      setShowForm(true)
      setSearchParams(prev => { const n = new URLSearchParams(prev); n.delete('pipeline'); return n }, { replace: true })
    }

  }, [])

  useEffect(() => {
    if (!deepLinkedTaskId) return
    let active = true
    pipelineTasksApi.get(deepLinkedTaskId)
      .then(task => {
        if (!active) return
        setHistoryTask(task)
        setHistoryRunId(deepLinkedRunId)
      })
      .catch(err => {
        if (!active) return
        setActionError(err?.detail || err?.message || '收件箱关联的任务已不存在或无权访问')
      })
    return () => { active = false }
  }, [deepLinkedRunId, deepLinkedTaskId])

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
      if (filterPipelineId) params.pipeline_id = filterPipelineId

      const [listRes, statsRes, optionsRes] = await Promise.all([
        pipelineTasksApi.list(params),
        pipelineTasksApi.stats(),
        pipelineTasksApi.pipelineOptions(),
      ])
      setTasks(listRes.items)
      setTotal(listRes.total)
      setStats(statsRes)
      setPipelineOptions(optionsRes.items || [])
    } catch (err) {
      console.error('加载调度任务失败', err)
      setActionError('任务数据加载失败，请检查服务状态后重试')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }

  }, [page, pageSize, activeTab, search, filterPipelineId])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    const updateIndicator = () => {
      const container = quickTabsRef.current
      if (!container) return
      const value = activeTab || 'all'
      const activeButton = container.querySelector(`[data-tab-value="${value}"]`) as HTMLElement | null
      if (!activeButton) return
      const containerRect = container.getBoundingClientRect()
      const buttonRect = activeButton.getBoundingClientRect()
      setQuickIndicator({
        left: buttonRect.left - containerRect.left,
        width: buttonRect.width,
      })
    }
    updateIndicator()
    window.addEventListener('resize', updateIndicator)
    return () => window.removeEventListener('resize', updateIndicator)
  }, [activeTab])

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
  const handlePipelineFilter = (pipelineId: string) => {
    setFilterPipelineId(pipelineId)
    setPage(1)
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (pipelineId) next.set('pipeline_id', pipelineId)
      else next.delete('pipeline_id')
      return next
    }, { replace: true })
  }
  const clearFilters = () => {
    setSearchInput('')
    setSearch('')
    setActiveTab('')
    handlePipelineFilter('')
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
  // 今日成功率 =（今日执行次数 - 今日异常数）/ 今日执行次数；今日无执行则显示 —
  const todaySuccessRate = useMemo(() => {
    const runs = stats?.today_runs ?? 0
    if (runs <= 0) return '—'
    const errs = stats?.today_errors ?? 0
    return `${Math.round(((runs - errs) / runs) * 100)}%`
  }, [stats])

  const trendData = useMemo(() => {
    const source = stats?.trend_7d ?? []
    const days = source.map(item => {
      const [, month, day] = item.date.split('-')
      return `${Number(month)}/${Number(day)}`
    })
    const failures = source.map(item => Math.min(Math.max(item.errors, 0), Math.max(item.runs, 0)))
    const successes = source.map((item, index) => Math.max(item.runs - failures[index], 0))
    const successTotal = successes.reduce((a, b) => a + b, 0)
    const failureTotal = failures.reduce((a, b) => a + b, 0)
    return { days, successes, failures, successTotal, failureTotal, total7d: successTotal + failureTotal }
  }, [stats])

  // ECharts: 环形状态分布 - 固定尺寸容器、禁用 tooltip 防止溢出
  const pieOption = useMemo(() => {
    const s = stats
    const success = s?.success ?? 0
    const idle = s?.idle ?? Math.max(0, (s?.total ?? 0) - (s?.running ?? 0) - (s?.failed ?? 0) - success)
    const data = [
      { name: '运行中', value: s?.running ?? 0, itemStyle: { color: '#0D9488' } },
      { name: '上次成功', value: success, itemStyle: { color: '#34D399' } },
      { name: '上次失败', value: s?.failed ?? 0, itemStyle: { color: '#F87171' } },
      { name: '空闲', value: idle, itemStyle: { color: '#CBD5E1' } },
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

  // ECharts: 近七日成功/失败堆叠趋势，每根柱体高度仍表示当日总执行次数
  const miniTrendOption = useMemo(() => ({
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
    <div className="flex h-full min-h-[640px] flex-col gap-3">
      <div className="shrink-0 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm/50">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="max-w-full flex-none overflow-x-auto">
            <div className="flex w-max items-center">
              <FlowNode label="数据流水线" icon={<Workflow size={14} />} onClick={() => navigate('/data/pipelines')} />
              <FlowArrow />
              <FlowNode label="数据任务池" icon={<ListChecks size={14} />} active />
              <FlowArrow />
              <FlowNode label="数据资产湖" icon={<Database size={14} />} onClick={() => navigate('/data/structured')} />
              <FlowArrow />
              <FlowNode label="本体数据映射" icon={<Boxes size={14} />} />
              <FlowArrow />
              <FlowNode label="本体网络图谱" icon={<Network size={14} />} onClick={() => navigate('/ontologies')} />
            </div>
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => setShowGlobalHistory(true)}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-emerald-200 bg-white px-4 text-xs font-medium text-emerald-700 shadow-sm transition hover:border-emerald-300 hover:bg-emerald-50 active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/30"
            >
              <History size={14} />
              历史记录
            </button>
            <button
              type="button"
              onClick={handleCreate}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-emerald-600 px-4 text-xs font-medium text-white shadow-sm transition hover:bg-emerald-700 active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/40"
            >
              <Plus size={14} />
              新建任务
            </button>
          </div>
        </div>
      </div>

      <div className="grid shrink-0 grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
        <KpiCard label="任务总数" value={stats?.total ?? 0} note="当前任务配置" icon={<Database size={13} />} tone="slate" />
        <KpiCard label="已启用" value={stats?.enabled ?? 0} note="可被计划调度" icon={<CheckCircle2 size={13} />} tone="emerald" />
        <KpiCard label="今日执行" value={stats?.today_runs ?? 0} note={`累计 ${stats?.total_runs ?? 0} 次`} icon={<Activity size={13} />} tone="teal" />
        <KpiCard label="今日异常" value={stats?.today_errors ?? 0} note={`累计 ${stats?.total_errors ?? 0} 次`} icon={<AlertCircle size={13} />} tone="rose" pulse={(stats?.today_errors ?? 0) > 0} />
        <KpiCard label="今日成功率" value={todaySuccessRate} note="基于今日执行结果" icon={<Waves size={13} />} tone="cyan" />
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 2xl:grid-cols-12">
        <div className="col-span-1 flex min-h-0 flex-col 2xl:col-span-9">
          <div data-testid="task-list-panel" className={`${PANEL} flex min-h-0 flex-1 flex-col`}>
            <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-slate-100 px-5 py-3">
              <div ref={quickTabsRef} className="relative flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50/70 p-0.5">
                <span
                  data-testid="task-filter-indicator"
                  className="absolute top-0.5 h-[calc(100%-4px)] rounded-md bg-teal-600 shadow-sm transition-all duration-300 ease-out"
                  style={{ left: `${quickIndicator.left}px`, width: `${quickIndicator.width}px` }}
                />
                {QUICK_TABS.map(tab => {
                  const active = activeTab === tab.key
                  return (
                    <button
                      key={tab.key}
                      type="button"
                      data-tab-value={tab.key || 'all'}
                      aria-pressed={active}
                      onClick={() => handleTabChange(tab.key)}
                      className={`relative z-10 flex h-7 items-center gap-1.5 rounded-md px-3 text-xs font-medium transition-colors duration-200 ${
                        active
                          ? 'text-white'
                          : 'text-slate-500 hover:text-slate-700'
                      }`}
                    >
                      {tab.label}
                    </button>
                  )
                })}
              </div>

              <div className="relative w-full max-w-[260px]">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  type="text"
                  placeholder="搜索任务名或流水线"
                  value={searchInput}
                  onChange={e => setSearchInput(e.target.value)}
                  className="h-9 w-full rounded-lg border border-slate-200 bg-white pl-8 pr-8 text-xs text-slate-700 outline-none transition focus:border-teal-500 focus:ring-2 focus:ring-teal-500/15"
                />
                {searchInput && (
                  <button
                    type="button"
                    onClick={() => { setSearchInput(''); setSearch('') }}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-400 hover:text-slate-700"
                    aria-label="清除任务搜索"
                  >
                    <X size={12} />
                  </button>
                )}
              </div>

              <select
                value={filterPipelineId}
                onChange={e => handlePipelineFilter(e.target.value)}
                className="h-9 max-w-[240px] rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-600 outline-none transition focus:border-teal-500 focus:ring-2 focus:ring-teal-500/15"
                aria-label="按数据流水线筛选任务"
                title="按数据流水线筛选任务"
              >
                <option value="">全部数据流水线</option>
                {pipelineOptions.map(option => (
                  <option key={option.id} value={option.id}>{option.name}（{option.task_count}）</option>
                ))}
              </select>

              {(activeTab || search || filterPipelineId) && (
                <button
                  type="button"
                  onClick={clearFilters}
                  className="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-xs text-rose-500 transition hover:bg-rose-50 hover:text-rose-600"
                >
                  <X size={12} /> 清除筛选
                </button>
              )}

              <div className="ml-auto flex items-center gap-2">
                <span className="inline-flex items-center gap-1.5 text-[11px] text-slate-400 tabular-nums">
                  <span className={`h-1.5 w-1.5 rounded-full ${refreshing ? 'bg-amber-400' : 'bg-emerald-500'}`} />
                  {refreshing ? '刷新中' : '10 秒自动刷新'}
                </span>
                <button
                  type="button"
                  onClick={load}
                  disabled={refreshing}
                  className="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-xs text-slate-500 transition hover:bg-teal-50 hover:text-teal-700 disabled:opacity-50"
                >
                  <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
                  刷新
                </button>
              </div>
            </div>

            {actionError && (
              <div className="mx-5 mt-3 flex shrink-0 items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                <XCircle size={13} className="shrink-0" />
                <span className="flex-1">{actionError}</span>
                <button type="button" onClick={() => setActionError('')} className="text-rose-400 hover:text-rose-700" aria-label="关闭错误提示">
                  <X size={12} />
                </button>
              </div>
            )}

            <div className="min-h-0 flex-1 overflow-auto px-5 py-3 scrollbar-thin">
              {loading ? (
                <div className="flex h-full min-h-48 flex-col items-center justify-center gap-2 text-slate-400">
                  <Loader2 size={18} className="animate-spin text-teal-600" />
                  <span className="text-xs">加载任务...</span>
                </div>
              ) : tasks.length === 0 ? (
                <EmptyState activeTab={activeTab} hasSearch={!!search || !!filterPipelineId} onClear={clearFilters} onCreate={handleCreate} />
              ) : (
                <div className="min-w-full overflow-hidden rounded-xl border border-slate-200 bg-white">
                  <div data-testid="task-table-scroll" className="overflow-x-auto overscroll-x-contain scrollbar-thin">
                    <table className="w-max min-w-[1840px] table-auto text-center text-sm">
                      <thead className="bg-slate-50">
                        <tr className="border-b border-slate-200 text-xs text-slate-600">
                          <th scope="col" data-column="task-name" className="sticky left-0 z-20 min-w-[200px] border-r border-slate-200 bg-slate-50 px-4 py-2.5 text-center font-medium shadow-[10px_0_14px_-14px_rgba(15,23,42,0.35)]">任务名称</th>
                          <th scope="col" data-column="run-status" className="min-w-[105px] px-4 py-2.5 text-center font-medium">运行状态</th>
                          <th scope="col" data-column="enabled" className="min-w-[105px] px-4 py-2.5 text-center font-medium">启停</th>
                          <th scope="col" data-column="pipeline" className="min-w-[240px] px-4 py-2.5 text-center font-medium">关联流水线</th>
                          <th scope="col" data-column="last-run" className="min-w-[185px] px-4 py-2.5 text-center font-medium">最近执行</th>
                          <th scope="col" data-column="lake-result" className="min-w-[210px] px-4 py-2.5 text-center font-medium">入湖结果</th>
                          <th scope="col" data-column="next-run" className="min-w-[190px] px-4 py-2.5 text-center font-medium">下次执行</th>
                          <th scope="col" data-column="schedule-type" className="min-w-[110px] px-4 py-2.5 text-center font-medium">调度方式</th>
                          <th scope="col" data-column="write-mode" className="min-w-[210px] px-4 py-2.5 text-center font-medium">入库策略</th>
                          <th scope="col" data-column="schedule-rule" className="min-w-[160px] px-4 py-2.5 text-center font-medium">调度规则</th>
                          <th scope="col" data-column="description" className="min-w-[240px] px-4 py-2.5 text-center font-medium">任务描述</th>
                          <th scope="col" data-column="actions" className="sticky right-0 z-20 min-w-[150px] border-l border-slate-200 bg-slate-50 px-4 py-2.5 text-center font-medium shadow-[-10px_0_14px_-14px_rgba(15,23,42,0.35)]">操作</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {tasks.map(t => {
                          const isTriggering = triggeringIds.has(t.id)
                          const wm = WRITE_MODE_META[t.write_mode as WriteMode]
                          const pipelineGone = t.pipeline_status === 'deleted'
                          const pipelineUnpub = !pipelineGone && t.pipeline_status && t.pipeline_status !== 'published'
                          const pipelineDisabled = !pipelineGone && t.pipeline_enabled === false
                          const sch = SCHEDULE_LABEL[t.schedule_type] || SCHEDULE_LABEL.MANUAL
                          const SchIcon = sch.Icon
                          const scheduleRule = t.schedule_type === 'CRON'
                            ? (t.cron_expression || '未配置 Cron')
                            : t.schedule_type === 'INTERVAL'
                              ? `每 ${relativeDuration(t.interval_seconds) || `${t.interval_seconds || 0} 秒`}`
                              : '仅支持手动触发'
                          return (
                            <tr key={t.id} className="group whitespace-nowrap transition-colors hover:bg-slate-50/80">
                              <td data-column="task-name" className="sticky left-0 z-[1] min-w-[200px] border-r border-slate-100 bg-white px-4 py-3 text-center align-middle shadow-[10px_0_14px_-14px_rgba(15,23,42,0.35)] group-hover:bg-slate-50">
                                <span className="mx-auto block max-w-[176px] truncate text-sm font-medium text-slate-900" title={t.name}>{t.name}</span>
                              </td>
                              <td data-column="run-status" className="px-4 py-3 text-center align-middle"><RunStateBadge task={t} /></td>
                              <td data-column="enabled" className="px-4 py-3 text-center align-middle">
                                <div className="flex items-center justify-center gap-2">
                                  <Switch checked={t.enabled} onChange={() => handleToggle(t)} />
                                  <span className={`text-[11px] font-medium ${t.enabled ? 'text-emerald-700' : 'text-slate-400'}`}>
                                    {t.enabled ? '已启用' : '已停用'}
                                  </span>
                                </div>
                              </td>
                              <td data-column="pipeline" className="px-4 py-3 text-center align-middle">
                                <button
                                  type="button"
                                  onClick={() => !pipelineGone && navigate(`/data/pipelines?search=${encodeURIComponent(t.pipeline_name || t.pipeline_id)}`)}
                                  className={`inline-flex items-center gap-1 text-xs ${pipelineGone ? 'cursor-default text-slate-400' : 'text-teal-700 hover:underline underline-offset-2'}`}
                                  title={pipelineGone ? '流水线已删除' : '前往数据流水线管理页'}
                                >
                                  <GitBranch size={11} className="shrink-0" />
                                  <span>{t.pipeline_name || t.pipeline_id}</span>
                                  {t.pipeline_version ? <span className="text-[10px] text-slate-400">v{t.pipeline_version}</span> : null}
                                  {!pipelineGone && <ExternalLink size={9} className="shrink-0 opacity-60" />}
                                </button>
                                {(pipelineGone || pipelineUnpub || pipelineDisabled) && (
                                  <span className="ml-2 inline-flex items-center gap-1 text-[10px] text-rose-500">
                                    <AlertCircle size={10} />
                                    {pipelineGone ? '已删除' : pipelineUnpub ? '未发布' : '流水线已停用'}
                                  </span>
                                )}
                              </td>
                              <td data-column="last-run" className="px-4 py-3 text-center align-middle">
                                {t.last_run_at ? <TimeInline iso={t.last_run_at} withSeconds /> : <span className="text-xs text-slate-400">尚未执行</span>}
                              </td>
                              <td data-column="lake-result" className="px-4 py-3 text-center align-middle">
                                {t.status === 'failed' && t.last_error ? (
                                  <span
                                    data-testid={`lake-result-error-${t.id}`}
                                    className="mx-auto block max-w-[186px] cursor-help truncate text-xs text-rose-600"
                                    title={t.last_error}
                                    aria-label={`入湖失败：${t.last_error}`}
                                  >
                                    {t.last_error}
                                  </span>
                                ) : (
                                  <div className="inline-flex items-center justify-center gap-2">
                                    <span className="text-xs tabular-nums text-slate-500">产出 {t.last_rows ?? 0} 行</span>
                                    <ExecResultCell impact={t.last_impact} status={t.status} />
                                  </div>
                                )}
                              </td>
                              <td data-column="next-run" className="px-4 py-3 text-center align-middle">
                                {t.schedule_type === 'MANUAL' ? (
                                  <span className="text-xs text-slate-400">不自动调度</span>
                                ) : !t.enabled ? (
                                  <span className="text-xs text-slate-400">任务已停用</span>
                                ) : t.next_run_at ? (
                                  <div className="inline-flex items-center gap-2">
                                    <TimeInline iso={t.next_run_at} />
                                    <span className="text-[10px] text-teal-600">{formatFuture(t.next_run_at)}</span>
                                  </div>
                                ) : (
                                  <span className="text-xs text-slate-400">待调度器计算</span>
                                )}
                              </td>
                              <td data-column="schedule-type" className="px-4 py-3 text-center align-middle">
                                <div className="inline-flex items-center justify-center gap-1 text-[11px] text-slate-600">
                                  <SchIcon size={11} className={sch.color} />
                                  <span>{sch.label}</span>
                                </div>
                              </td>
                              <td data-column="write-mode" className="px-4 py-3 text-center align-middle">
                                <div className="inline-flex items-center justify-center gap-2">
                                  <span data-write-mode={t.write_mode} className={`inline-flex rounded-md border px-2 py-1 text-[11px] font-medium ${WRITE_MODE_TONE[t.write_mode]}`} title={wm?.desc}>
                                    {wm?.label || t.write_mode}
                                  </span>
                                  <span className={`inline-flex items-center gap-1 text-[10px] ${t.skip_empty ? 'text-teal-600' : 'text-slate-400'}`}>
                                    <ShieldCheck size={10} />空输出保护：{t.skip_empty ? '开启' : '关闭'}
                                  </span>
                                </div>
                              </td>
                              <td data-column="schedule-rule" className={`px-4 py-3 text-center align-middle text-xs ${t.schedule_type === 'CRON' ? 'font-mono text-slate-600' : 'text-slate-500'}`}>
                                {scheduleRule}
                              </td>
                              <td data-column="description" className="px-4 py-3 text-center align-middle text-xs text-slate-500">
                                <span className="mx-auto block max-w-[216px] truncate" title={t.description || undefined}>{t.description || '—'}</span>
                              </td>
                              <td data-column="actions" className="sticky right-0 z-[1] border-l border-slate-100 bg-white px-4 py-2 text-center align-middle shadow-[-10px_0_14px_-14px_rgba(15,23,42,0.35)] group-hover:bg-slate-50">
                                <div className="flex items-center justify-center gap-0.5">
                                  <IconBtn2 title={pipelineDisabled ? '关联流水线已停用' : '立即执行'}
                                    disabled={t.status === 'running' || isTriggering || pipelineGone || !!pipelineUnpub || pipelineDisabled}
                                    onClick={() => handleTrigger(t)} accent="teal">
                                    <RotateCw size={14} className={isTriggering ? 'animate-spin' : ''} />
                                  </IconBtn2>
                                  <IconBtn2 title="执行记录" onClick={() => { setHistoryTask(t); setHistoryRunId(null) }}>
                                    <History size={14} />
                                  </IconBtn2>
                                  <IconBtn2 title="编辑" onClick={() => handleEdit(t)}>
                                    <Edit2 size={14} />
                                  </IconBtn2>
                                  <IconBtn2 title="删除" danger onClick={() => setDeleteTarget(t)} accent="rose">
                                    <Trash2 size={14} />
                                  </IconBtn2>
                                </div>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>

            {!loading && total > 0 && (
              <div className="flex shrink-0 items-center justify-end gap-3 border-t border-slate-100 bg-slate-50/50 px-5 py-2.5">
                <span className="mr-auto text-xs tabular-nums text-slate-400">共 {total} 条任务</span>
                <label className="flex items-center gap-1.5 text-xs text-slate-500">
                  每页
                  <select
                    value={pageSize}
                    onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }}
                    className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs outline-none focus:border-teal-500"
                    aria-label="任务列表每页显示条数"
                  >
                    {PAGE_SIZE_OPTIONS.map(n => <option key={n} value={n}>{n}</option>)}
                  </select>
                  条
                </label>
                <span className="min-w-20 text-center text-xs tabular-nums text-slate-500">第 {page} / {totalPages} 页</span>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setPage(p => Math.max(1, p - 1))}
                    disabled={page <= 1}
                    className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-35"
                    aria-label="上一页"
                  >
                    <ChevronLeft size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages}
                    className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-35"
                    aria-label="下一页"
                  >
                    <ChevronLeft size={14} className="rotate-180" />
                  </button>
                </div>
              </div>
            )}
          </div>

        </div>

        <aside className="hidden min-h-0 flex-col gap-3 2xl:col-span-3 2xl:flex">
          <div className={`${PANEL} shrink-0 p-4`}>
            <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-700">
              <span className="h-1.5 w-1.5 rounded-full bg-teal-600" />
              状态分布
            </h3>
            <div className="flex items-center">
              <div className="relative h-[96px] w-[96px] shrink-0 overflow-hidden">
                <ReactECharts option={pieOption} style={{ height: '100%', width: '100%' }} opts={{ renderer: 'svg' }} notMerge />
                <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                  <div className="text-lg font-semibold leading-none text-slate-900 tabular-nums">{stats?.total ?? 0}</div>
                  <div className="mt-1 text-[10px] text-slate-400">总任务</div>
                </div>
              </div>
              <div className="flex flex-1 flex-col gap-2 pl-3">
                <LegendRow color="#0D9488" label="运行中" value={stats?.running ?? 0} />
                <LegendRow color="#34D399" label="上次成功" value={stats?.success ?? 0} />
                <LegendRow color="#F87171" label="上次失败" value={stats?.failed ?? 0} />
                <LegendRow color="#CBD5E1" label="空闲" value={stats?.idle ?? Math.max(0, (stats?.total ?? 0) - (stats?.running ?? 0) - (stats?.failed ?? 0) - (stats?.success ?? 0))} />
              </div>
            </div>
          </div>

          <div data-testid="seven-day-chart" className={`${PANEL} flex h-[clamp(208px,22vh,240px)] shrink-0 flex-col p-4`}>
            <div className="mb-1 flex items-center justify-between">
              <h3 className="flex items-center gap-2 text-xs font-semibold text-slate-700">
                <span className="h-1.5 w-1.5 rounded-full bg-teal-600" />
                近 7 日执行
              </h3>
              <span className="text-[11px] text-slate-500 tabular-nums">{trendData.total7d} 次</span>
            </div>
            <div className="flex shrink-0 items-center gap-3 pl-3 text-[10px] text-slate-500" aria-label="近 7 日执行结果图例">
              <span data-testid="trend-success-total" className="inline-flex items-center gap-1.5 tabular-nums">
                <span className="h-1.5 w-1.5 rounded-sm bg-emerald-500" aria-hidden="true" />
                成功 {trendData.successTotal}
              </span>
              <span data-testid="trend-failure-total" className="inline-flex items-center gap-1.5 tabular-nums">
                <span className="h-1.5 w-1.5 rounded-sm bg-red-400" aria-hidden="true" />
                失败 {trendData.failureTotal}
              </span>
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">
              <ReactECharts option={miniTrendOption} style={{ height: '100%', width: '100%' }} opts={{ renderer: 'svg' }} notMerge />
            </div>
          </div>

          <div data-testid="recent-run-card" className={`${PANEL} flex min-h-[196px] flex-1 flex-col p-4`}>
            <div className="mb-2.5 flex shrink-0 items-center justify-between">
              <h3 className="flex items-center gap-2 text-xs font-semibold text-slate-700">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                最近执行记录
              </h3>
              <span className="text-[10px] text-slate-400">实时留痕</span>
            </div>
            <RecentRunFeed runs={stats?.recent_runs ?? []} />
          </div>
        </aside>
      </div>

      {showForm && (
        <TaskFormModal
          initialTask={editingTask}
          initialPipelineId={presetPipelineId}
          onClose={() => { setShowForm(false); setEditingTask(null); setPresetPipelineId(null) }}
          onSaved={handleFormSaved}
        />
      )}
      {showGlobalHistory && (
        <GlobalHistoryModal
          pipelineOptions={pipelineOptions}
          onClose={() => setShowGlobalHistory(false)}
        />
      )}
      {historyTask && (
        <HistoryDrawer
          task={historyTask}
          initialRunId={historyRunId}
          onClose={() => {
            setHistoryTask(null)
            setHistoryRunId(null)
            if (searchParams.has('task_id') || searchParams.has('run_id')) {
              setSearchParams(prev => {
                const next = new URLSearchParams(prev)
                next.delete('task_id')
                next.delete('run_id')
                return next
              }, { replace: true })
            }
          }}
        />
      )}
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
  children: ReactNode; onClick?: () => void; title?: string; disabled?: boolean
  danger?: boolean; accent?: 'slate' | 'teal' | 'rose'
}) {
  const hover = danger || accent === 'rose'
    ? 'hover:bg-rose-50 hover:text-rose-600'
    : accent === 'teal' ? 'hover:bg-teal-50 hover:text-teal-700'
    : 'hover:bg-slate-100 hover:text-slate-700'
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition ${hover} focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30 disabled:cursor-not-allowed disabled:opacity-35`}
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
    <div data-testid="task-empty-state" className="flex h-full min-h-56 flex-1 items-center justify-center rounded-xl border-2 border-dashed border-slate-200 py-8 text-center">
      <div className="flex -translate-y-4 flex-col items-center">
        <div className="mb-3 grid h-12 w-12 place-items-center rounded-xl bg-teal-50 text-teal-600">
          <Repeat size={20} />
        </div>
        {isFiltered ? (
          <>
            <div className="mb-1 text-sm font-medium text-slate-700">没有匹配的任务</div>
            <div className="mb-3 text-xs text-slate-400">调整状态、关键词或流水线筛选条件</div>
            <button type="button" onClick={onClear}
              className="inline-flex items-center gap-1 rounded-lg bg-teal-50 px-3 py-1.5 text-xs text-teal-700 transition-colors hover:bg-teal-100">
              <X size={12} /> 清除筛选
            </button>
          </>
        ) : (
          <>
            <div className="mb-1 text-sm font-medium text-slate-700">暂无调度任务</div>
            <div className="mb-3 max-w-xs text-xs leading-relaxed text-slate-400">
              选择一条已发布的流水线，设定入库方式，产物将按计划写入资产湖
            </div>
            <button type="button" onClick={onCreate}
              className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3.5 py-1.5 text-xs font-medium text-white transition hover:bg-emerald-700">
              <Plus size={12} /> 新建第一个任务
            </button>
          </>
        )}
      </div>
    </div>
  )
}

function formatFeedTime(iso: string | null): string {
  if (!iso) return '刚刚'
  try {
    const diff = Math.max(0, Date.now() - toLocalDate(iso).getTime())
    if (diff < 60_000) return '刚刚'
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
    return `${Math.floor(diff / 86_400_000)} 天前`
  } catch { return iso }
}

function RecentRunFeed({ runs }: { runs: PipelineTaskRecentRun[] }) {
  if (runs.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 text-xs text-slate-400">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-emerald-50 text-emerald-500">
          <History size={17} />
        </span>
        <span>任务执行后会在这里留痕</span>
      </div>
    )
  }
  const statusMeta: Record<string, { label: string; dot: string; text: string }> = {
    pending: { label: '排队', dot: 'bg-slate-400', text: 'text-slate-500' },
    running: { label: '执行中', dot: 'bg-teal-500', text: 'text-teal-700' },
    success: { label: '成功', dot: 'bg-emerald-500', text: 'text-emerald-700' },
    failed: { label: '失败', dot: 'bg-rose-500', text: 'text-rose-600' },
  }
  return (
    <div data-testid="recent-run-feed" className="scrollbar-none min-h-0 flex-1 overflow-y-auto pr-1">
      {runs.slice(0, RECENT_RUN_LIMIT).map((run, index) => {
        const meta = statusMeta[run.status] || statusMeta.pending
        return (
          <div key={run.id} data-testid="recent-run-item" className="relative flex gap-2.5 pb-2.5 last:pb-0">
            <div className="relative flex w-2 shrink-0 justify-center pt-1.5">
              <span className={`relative z-10 h-2 w-2 rounded-full ring-2 ring-white ${meta.dot}`} />
              {index < Math.min(runs.length, RECENT_RUN_LIMIT) - 1 && <span className="absolute bottom-0 top-2.5 w-px bg-slate-100" />}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className={`text-[10px] font-medium ${meta.text}`}>{meta.label}</span>
                <span className="truncate text-xs font-medium text-slate-700" title={run.task_name}>{run.task_name}</span>
                <time className="ml-auto shrink-0 text-[9px] text-slate-400">{formatFeedTime(run.started_at)}</time>
              </div>
              <div className="mt-0.5 flex items-center gap-1 truncate text-[10px] text-slate-400" title={run.error_message || run.pipeline_name}>
                <GitBranch size={9} className="shrink-0" />
                <span className="truncate">{run.pipeline_name}</span>
                {run.status === 'success' && <span className="shrink-0">· 输出 {run.rows_out} 行</span>}
                {run.status === 'failed' && <span className="truncate text-rose-500">· {run.error_message || '执行失败'}</span>}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── 启用开关 ──────────────────────────────────────────
function Switch({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <button
      type="button" role="switch" aria-checked={checked} onClick={onChange}
      title={checked ? '点击停用' : '点击启用'}
      className={`relative inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/35 focus-visible:ring-offset-1 ${
        checked ? 'bg-emerald-500' : 'bg-slate-300'
      }`}
    >
      <span className={`inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${
        checked ? 'translate-x-3.5' : 'translate-x-0.5'
      }`} />
    </button>
  )
}

function RunStateBadge({ task }: { task: PipelineTask }) {
  const { status } = task
  const meta = {
    running: { label: '执行中', className: 'bg-teal-50 text-teal-700' },
    failed: { label: '上次失败', className: 'bg-rose-50 text-rose-600' },
    success: { label: '上次成功', className: 'bg-emerald-50 text-emerald-700' },
    idle: {
      label: task.last_run_at ? '空闲' : '尚未执行',
      className: 'bg-slate-100 text-slate-500',
    },
  }[status] || { label: status, className: 'bg-slate-100 text-slate-500' }
  return <span className={`inline-flex rounded-md px-2 py-1 text-[10px] ${meta.className}`}>{meta.label}</span>
}

function formatFuture(iso: string): string {
  try {
    const diff = (toLocalDate(iso).getTime() - Date.now()) / 1000
    if (diff <= 0) return '即将'
    if (diff < 60) return `${Math.round(diff)}秒后`
    if (diff < 3600) return `${Math.round(diff / 60)}分钟后`
    if (diff < 86400) return `${Math.round(diff / 3600)}小时后`
    return `${Math.round(diff / 86400)}天后`
  } catch { return '' }
}

// ── 执行结果：最近一次执行对资产湖的影响 ──────────────
function ExecResultCell({ impact, status }: { impact?: LakeImpact | null; status: string }) {
  if (status === 'failed') return <span className="rounded-md bg-rose-50 px-2 py-1 text-[10px] text-rose-600">执行失败</span>
  if (!impact) return <span className="text-[11px] text-slate-300">—</span>
  const added = impact.added ?? 0, updated = impact.updated ?? 0, deleted = impact.deleted ?? 0
  if (!added && !updated && !deleted) return <span className="text-[11px] text-slate-400">无变化</span>
  return (
    <div className="inline-flex items-center gap-1 text-[10px] tabular-nums">
      {added > 0 && <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">+{added}</span>}
      {updated > 0 && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">改 {updated}</span>}
      {deleted > 0 && <span className="rounded bg-rose-50 px-1.5 py-0.5 text-rose-700">−{deleted}</span>}
    </div>
  )
}
