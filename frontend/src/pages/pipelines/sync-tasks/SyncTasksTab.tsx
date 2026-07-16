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
import { pipelineTasksApi, WRITE_MODE_META, type PipelineFilterOption, type PipelineTask, type PipelineTaskStats, type WriteMode, type LakeImpact } from '@/api/v2/pipeline-tasks'
import { syncTasksApi, type SyncTask } from '@/api/v2/sync-tasks'
import TaskFormModal from './TaskFormModal'
import HistoryDrawer from './HistoryDrawer'
import ConfirmDialog from '@/components/ConfirmDialog'

// ── 常量 ──────────────────────────────────────────────
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

const PANEL = 'rounded-xl border border-slate-200 bg-white shadow-sm/50 overflow-hidden'

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

/** 标准时间的两行紧凑展示：日期在上、时间在下（省表格横向宽度） */
function TimeStack({ iso, withSeconds, align = 'center' }: { iso: string | null; withSeconds?: boolean; align?: 'left' | 'center' }) {
  if (!iso) return <span className="text-[11px] text-slate-400">—</span>
  try {
    const d = toLocalDate(iso)
    const p = (n: number) => String(n).padStart(2, '0')
    const date = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
    const time = withSeconds
      ? `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
      : `${p(d.getHours())}:${p(d.getMinutes())}`
    return (
      <div className={`${align === 'left' ? 'text-left' : 'text-center'} whitespace-nowrap tabular-nums leading-tight`}>
        <div className="text-xs text-slate-600">{date}</div>
        <div className="mt-0.5 text-[11px] text-slate-400">{time}</div>
      </div>
    )
  } catch { return <span className="text-[11px] text-slate-400">{iso}</span> }
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
  const [filterPipelineId, setFilterPipelineId] = useState(() => searchParams.get('pipeline_id') || '')
  const [pipelineOptions, setPipelineOptions] = useState<PipelineFilterOption[]>([])

  // ── 旧版同步任务（DataSyncTask）—— 调度器仍在跑但页面不可见 ──
  const [legacyTasks, setLegacyTasks] = useState<SyncTask[]>([])
  const [legacyLoading, setLegacyLoading] = useState(true)
  const [legacyDisablingId, setLegacyDisablingId] = useState<string | null>(null)
  const [legacyError, setLegacyError] = useState('')

  useEffect(() => {
    const pid = searchParams.get('pipeline')
    if (pid) {
      setPresetPipelineId(pid)
      setEditingTask(null)
      setShowForm(true)
      setSearchParams(prev => { const n = new URLSearchParams(prev); n.delete('pipeline'); return n }, { replace: true })
    }

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

  // ── 加载旧版同步任务 ──
  const loadLegacy = useCallback(async () => {
    setLegacyLoading(true)
    try {
      const res = await syncTasksApi.list({ page: 1, page_size: 100 })
      setLegacyTasks(res.items ?? [])
    } catch {
      setLegacyTasks([])
    } finally {
      setLegacyLoading(false)
    }
  }, [])

  useEffect(() => { loadLegacy() }, [loadLegacy])

  const handleDisableLegacy = async (id: string) => {
    setLegacyDisablingId(id)
    setLegacyError('')
    try {
      await syncTasksApi.toggle(id, false)
      setLegacyTasks(prev => prev.map(t => t.id === id ? { ...t, enabled: false } : t))
    } catch (error: unknown) {
      const err = error as { detail?: string; message?: string }
      setLegacyError(err.detail || err.message || '旧版同步任务停用失败，请重试')
    } finally {
      setLegacyDisablingId(null)
    }
  }

  const handleDisableAllLegacy = async () => {
    const enabled = legacyTasks.filter(t => t.enabled)
    setLegacyError('')
    const failed: string[] = []
    for (const t of enabled) {
      try {
        await syncTasksApi.toggle(t.id, false)
      } catch {
        failed.push(t.name)
      }
    }
    await loadLegacy()
    if (failed.length > 0) {
      setLegacyError(
        `以下 ${failed.length} 个旧版任务未能停用：${failed.slice(0, 3).join('、')}${failed.length > 3 ? '…' : ''}`,
      )
    }
  }

  const legacyEnabledCount = legacyTasks.filter(t => t.enabled).length
  const legacyHasActive = legacyTasks.some(t => t.enabled && (t.schedule_type === 'CRON' || t.schedule_type === 'INTERVAL'))

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
  const attentionTasks = useMemo(
    () => tasks.filter(t => t.status === 'failed' || t.pipeline_status === 'deleted'
      || (t.pipeline_status && t.pipeline_status !== 'published') || t.pipeline_enabled === false),
    [tasks],
  )

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
    const series = source.map(item => item.runs)
    return { days, series, total7d: series.reduce((a, b) => a + b, 0) }
  }, [stats])

  // ECharts: 环形状态分布 - 固定尺寸容器、禁用 tooltip 防止溢出
  const pieOption = useMemo(() => {
    const s = stats
    const idle = Math.max(0, (s?.total ?? 0) - (s?.running ?? 0) - (s?.failed ?? 0))
    const data = [
      { name: '运行中', value: s?.running ?? 0, itemStyle: { color: '#0D9488' } },
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
      lineStyle: { width: 2, color: '#0D9488' },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(13,148,136,0.28)' },
            { offset: 1, color: 'rgba(13,148,136,0.01)' },
          ],
        },
      },
      animationDuration: 700,
    }],
  }), [trendData])

  const failedCount = stats?.failed ?? 0

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

          <button
            type="button"
            onClick={handleCreate}
            className="ml-auto inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-emerald-600 px-4 text-xs font-medium text-white shadow-sm transition hover:bg-emerald-700 active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/40"
          >
            <Plus size={14} />
            新建任务
          </button>
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
          {!legacyLoading && legacyTasks.length > 0 && (
            <div className={`mb-3 shrink-0 rounded-xl border px-4 py-3 text-xs leading-relaxed ${
              legacyHasActive
                ? 'border-amber-200 bg-amber-50 text-amber-800'
                : 'border-slate-200 bg-slate-50 text-slate-600'
            }`}>
              {legacyHasActive ? (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <span className="inline-flex items-center gap-2 font-medium">
                    <AlertCircle size={14} className="shrink-0 text-amber-600" />
                    发现 {legacyTasks.length} 个旧版同步任务，其中 {legacyEnabledCount} 个仍在运行
                  </span>
                  <span className="text-[11px] text-amber-700/80">建议停用遗留调度，避免继续生成 SYNC:: 数据集</span>
                  <button
                    type="button"
                    onClick={handleDisableAllLegacy}
                    className="ml-auto rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-[11px] font-medium text-amber-800 transition hover:bg-amber-100"
                  >
                    一键禁用
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <CheckCircle2 size={14} className="shrink-0 text-emerald-500" />
                  <span>{legacyTasks.length} 个旧版同步任务均已停用，不会继续写入数据。</span>
                </div>
              )}
            </div>
          )}
          {legacyError && (
            <div className="mb-3 flex shrink-0 items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-xs text-red-700">
              <XCircle size={14} className="shrink-0" />
              <span className="flex-1">{legacyError}</span>
              <button type="button" onClick={() => setLegacyError('')} aria-label="关闭旧版任务错误提示">
                <X size={13} />
              </button>
            </div>
          )}

          <div className={`${PANEL} flex min-h-0 flex-1 flex-col`}>
            <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-slate-100 px-5 py-3">
              <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 p-1">
                {QUICK_TABS.map(tab => {
                  const active = activeTab === tab.key
                  return (
                    <button
                      key={tab.key}
                      type="button"
                      onClick={() => handleTabChange(tab.key)}
                      className={`flex h-7 items-center gap-1.5 rounded-md px-3 text-xs font-medium transition-colors ${
                        active
                          ? 'bg-emerald-600 text-white shadow-sm'
                          : 'text-slate-500 hover:bg-white hover:text-emerald-700'
                      }`}
                    >
                      {tab.dot && <span className="h-1.5 w-1.5 rounded-full" style={{ background: active ? '#fff' : tab.dot }} />}
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
                <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
                  <div className="overflow-auto scrollbar-thin">
                    <table className="w-full min-w-[900px] text-sm">
                      <thead className="bg-slate-50">
                        <tr className="border-b border-slate-200 text-xs text-slate-600">
                          <th className="px-4 py-2.5 text-left font-medium">任务</th>
                          <th className="px-4 py-2.5 text-left font-medium">关联流水线</th>
                          <th className="px-4 py-2.5 text-left font-medium">调度与入库</th>
                          <th className="px-4 py-2.5 text-left font-medium">状态</th>
                          <th className="px-4 py-2.5 text-left font-medium">最近执行与入湖</th>
                          <th className="px-4 py-2.5 text-left font-medium">下次执行</th>
                          <th className="px-4 py-2.5 text-right font-medium">操作</th>
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
                          return (
                            <tr key={t.id} className="group transition-colors hover:bg-slate-50/80">
                              <td className="max-w-[220px] px-4 py-3 align-top">
                                <div className="truncate text-sm font-medium text-slate-900" title={t.name}>{t.name}</div>
                                <div className="mt-0.5 truncate text-[11px] text-slate-400" title={t.description || t.id}>
                                  {t.description || `任务 ID · ${t.id.slice(0, 8)}`}
                                </div>
                              </td>
                              <td className="max-w-[190px] px-4 py-3 align-top">
                                <button
                                  type="button"
                                  onClick={() => !pipelineGone && navigate(`/data/pipelines?search=${encodeURIComponent(t.pipeline_name || t.pipeline_id)}`)}
                                  className={`flex max-w-full items-center gap-1 text-xs ${pipelineGone ? 'cursor-default text-slate-400' : 'text-teal-700 hover:underline underline-offset-2'}`}
                                  title={pipelineGone ? '流水线已删除' : '前往数据流水线管理页'}
                                >
                                  <GitBranch size={11} className="shrink-0" />
                                  <span className="truncate">{t.pipeline_name || t.pipeline_id.slice(0, 8)}</span>
                                  {!pipelineGone && <ExternalLink size={9} className="shrink-0 opacity-60" />}
                                </button>
                                {(pipelineGone || pipelineUnpub || pipelineDisabled) && (
                                  <div className="mt-1 flex items-center gap-1 text-[10px] text-rose-500">
                                    <AlertCircle size={10} />
                                    {pipelineGone ? '已删除' : pipelineUnpub ? '未发布' : '流水线已停用'}
                                  </div>
                                )}
                              </td>
                              <td className="px-4 py-3 align-top">
                                <div className="flex items-center gap-1.5">
                                  <span className="inline-flex items-center gap-1 rounded-md border border-teal-200 bg-teal-50 px-2 py-1 text-[11px] font-medium text-teal-700" title={wm?.desc}>
                                    {wm?.label || t.write_mode}
                                    {t.skip_empty && <ShieldCheck size={10} />}
                                  </span>
                                </div>
                                <div className="mt-1.5 flex items-center gap-1 text-[11px] text-slate-500">
                                  <SchIcon size={11} className={sch.color} />
                                  <span>{sch.label}</span>
                                  {t.schedule_type === 'CRON' && t.cron_expression && (
                                    <span className="max-w-[86px] truncate font-mono text-slate-400" title={t.cron_expression}>· {t.cron_expression}</span>
                                  )}
                                  {t.schedule_type === 'INTERVAL' && !!t.interval_seconds && (
                                    <span className="text-slate-400">· 每 {relativeDuration(t.interval_seconds)}</span>
                                  )}
                                </div>
                              </td>
                              <td className="px-4 py-3 align-top">
                                <div className="flex items-center gap-2">
                                  <Switch checked={t.enabled} onChange={() => handleToggle(t)} />
                                  <TaskRunBadge status={t.status} enabled={t.enabled} />
                                </div>
                              </td>
                              <td className="max-w-[150px] px-4 py-3 align-top">
                                <TimeStack iso={t.last_run_at} withSeconds align="left" />
                                {t.status === 'failed' && t.last_error && (
                                  <div className="mt-1 max-w-[140px] truncate text-[10px] text-rose-500" title={t.last_error}>{t.last_error}</div>
                                )}
                                <div className="mt-1.5">
                                  <ExecResultCell impact={t.last_impact} status={t.status} />
                                </div>
                              </td>
                              <td className="px-4 py-3 align-top">
                                <NextRunCell task={t} />
                              </td>
                              <td className="px-4 py-2 text-right align-middle">
                                <div className="flex items-center justify-end gap-0.5">
                                  <IconBtn2 title={pipelineDisabled ? '关联流水线已停用' : '立即执行'}
                                    disabled={t.status === 'running' || isTriggering || pipelineGone || !!pipelineUnpub || pipelineDisabled}
                                    onClick={() => handleTrigger(t)} accent="teal">
                                    <RotateCw size={14} className={isTriggering ? 'animate-spin' : ''} />
                                  </IconBtn2>
                                  <IconBtn2 title="执行历史" onClick={() => setHistoryTask(t)}>
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

          {!legacyLoading && legacyTasks.length > 0 && (
            <div className={`${PANEL} mt-3 max-h-[150px] shrink-0`}>
              <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-2.5">
                <History size={13} className="text-slate-400" />
                <span className="text-xs font-medium text-slate-700">旧版同步任务（{legacyTasks.length}）</span>
                <span className="text-[10px] text-slate-400">
                  {legacyTasks.filter(t => t.enabled).length} 启用 · {legacyTasks.filter(t => t.schedule_type === 'INTERVAL').length} INTERVAL · {legacyTasks.filter(t => t.schedule_type === 'CRON').length} CRON
                </span>
              </div>
              <div className="overflow-auto scrollbar-thin">
                <table className="w-full text-xs">
                  <tbody className="divide-y divide-slate-100">
                    {legacyTasks.map(t => (
                      <tr key={t.id} className="hover:bg-slate-50/80">
                        <td className="px-4 py-2 text-slate-700">{t.name}</td>
                        <td className="px-3 py-2 text-center text-[11px] text-slate-500">{t.sync_mode}</td>
                        <td className="px-3 py-2 text-center text-[11px] text-slate-500">
                          {t.schedule_type === 'INTERVAL' ? `${t.interval_seconds}s` : t.schedule_type === 'CRON' ? t.cron_expression : '手动'}
                        </td>
                        <td className="px-3 py-2 text-center">
                          <span className={`rounded-md px-2 py-1 text-[10px] ${t.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-400'}`}>
                            {t.enabled ? '启用' : '禁用'}
                          </span>
                        </td>
                        <td className="px-4 py-2 text-right">
                          {t.enabled && (
                            <button
                              type="button"
                              onClick={() => handleDisableLegacy(t.id)}
                              disabled={legacyDisablingId === t.id}
                              className="rounded-lg px-2.5 py-1 text-[10px] text-slate-500 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-50"
                            >
                              {legacyDisablingId === t.id ? '禁用中...' : '禁用'}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
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
                <LegendRow color="#F87171" label="异常" value={stats?.failed ?? 0} />
                <LegendRow color="#CBD5E1" label="待运行" value={Math.max(0, (stats?.total ?? 0) - (stats?.running ?? 0) - (stats?.failed ?? 0))} />
              </div>
            </div>
          </div>

          <div className={`${PANEL} flex min-h-0 flex-1 flex-col p-4`}>
            <div className="mb-2 flex shrink-0 items-center justify-between">
              <h3 className="flex items-center gap-2 text-xs font-semibold text-slate-700">
                {failedCount > 0 ? (
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400 opacity-60" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-rose-500" />
                  </span>
                ) : (
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                )}
                {failedCount > 0 ? '待关注任务' : '运行正常'}
              </h3>
              {attentionTasks.length > 0 && (
                <span className="rounded-md border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-[10px] text-rose-600 tabular-nums">{attentionTasks.length}</span>
              )}
            </div>
            <div className="min-h-0 flex-1 space-y-1.5 overflow-auto pr-0.5 scrollbar-thin">
              {attentionTasks.length === 0 ? (
                <div className="flex h-full flex-col items-center justify-center gap-2 py-4 text-xs text-slate-400">
                  <span className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-50 text-emerald-500">
                    <CheckCircle2 size={20} />
                  </span>
                  <span>暂无异常任务</span>
                </div>
              ) : (
                attentionTasks.slice(0, 8).map(t => (
                  <div key={t.id} className="group rounded-lg border border-slate-100 bg-slate-50/70 p-2.5 transition hover:border-slate-200 hover:bg-white">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs font-medium text-slate-700">{t.name}</div>
                        <div className="mt-1 flex items-center gap-1 text-[10px] text-rose-500">
                          <AlertCircle size={10} className="shrink-0" />
                          <span className="truncate">
                            {t.pipeline_status === 'deleted' ? '关联流水线已删除'
                              : t.pipeline_status && t.pipeline_status !== 'published' ? '流水线未发布'
                              : t.pipeline_enabled === false ? '流水线已停用'
                              : t.last_error ? t.last_error.slice(0, 26) : '执行失败'}
                          </span>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-0.5 opacity-60 transition-opacity group-hover:opacity-100">
                        {t.status === 'failed' && t.pipeline_status === 'published' && t.pipeline_enabled !== false && (
                          <button type="button" onClick={() => handleTrigger(t)} title="重试"
                            className="grid h-7 w-7 place-items-center rounded-lg text-teal-700 hover:bg-teal-50">
                            <RotateCw size={12} />
                          </button>
                        )}
                        <button type="button" onClick={() => handleEdit(t)} title="编辑"
                          className="grid h-7 w-7 place-items-center rounded-lg text-slate-500 hover:bg-slate-100">
                          <Edit2 size={12} />
                        </button>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className={`${PANEL} shrink-0 p-4`}>
            <div className="mb-1 flex items-center justify-between">
              <h3 className="flex items-center gap-2 text-xs font-semibold text-slate-700">
                <span className="h-1.5 w-1.5 rounded-full bg-teal-600" />
                近 7 日执行
              </h3>
              <span className="text-[11px] text-slate-500 tabular-nums">{trendData.total7d} 次</span>
            </div>
            <div className="h-12 overflow-hidden">
              <ReactECharts option={miniTrendOption} style={{ height: '100%', width: '100%' }} opts={{ renderer: 'svg' }} notMerge />
            </div>
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
    <div className="flex min-h-56 flex-1 flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-200 py-8 text-center">
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

function TaskRunBadge({ status, enabled }: { status: string; enabled: boolean }) {
  if (!enabled) {
    return <span className="rounded-md bg-slate-100 px-2 py-1 text-[10px] text-slate-500">已停用</span>
  }
  const meta = {
    running: { label: '运行中', className: 'bg-teal-50 text-teal-700' },
    failed: { label: '异常', className: 'bg-rose-50 text-rose-600' },
    success: { label: '上次成功', className: 'bg-emerald-50 text-emerald-700' },
    idle: { label: '待运行', className: 'bg-slate-100 text-slate-500' },
  }[status] || { label: status, className: 'bg-slate-100 text-slate-500' }
  return <span className={`rounded-md px-2 py-1 text-[10px] ${meta.className}`}>{meta.label}</span>
}

// ── 下一次执行时间 ─────────────────────────────────────
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

function NextRunCell({ task }: { task: PipelineTask }) {
  if (task.schedule_type === 'MANUAL')
    return <span className="text-[11px] text-slate-400">手动触发</span>
  if (!task.enabled)
    return <span className="text-[11px] text-slate-400">已停用</span>
  if (!task.next_run_at)
    return <span className="text-[11px] text-slate-400">—</span>
  return (
    <div className="flex flex-col items-start">
      <TimeStack iso={task.next_run_at} align="left" />
      <div className="mt-1 whitespace-nowrap text-[10px] text-teal-600">{formatFuture(task.next_run_at)}</div>
    </div>
  )
}

// ── 执行结果：最近一次执行对资产湖的影响 ──────────────
function ExecResultCell({ impact, status }: { impact?: LakeImpact | null; status: string }) {
  if (status === 'failed') return <span className="rounded-md bg-rose-50 px-2 py-1 text-[10px] text-rose-600">执行失败</span>
  if (!impact) return <span className="text-[11px] text-slate-300">—</span>
  const added = impact.added ?? 0, updated = impact.updated ?? 0, deleted = impact.deleted ?? 0
  if (!added && !updated && !deleted) return <span className="text-[11px] text-slate-400">无变化</span>
  return (
    <div className="flex max-w-[150px] flex-wrap items-center gap-1 text-[10px] tabular-nums">
      {added > 0 && <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">+{added}</span>}
      {updated > 0 && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">改 {updated}</span>}
      {deleted > 0 && <span className="rounded bg-rose-50 px-1.5 py-0.5 text-rose-700">−{deleted}</span>}
    </div>
  )
}
