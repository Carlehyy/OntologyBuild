import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import {
  Plus, History, RefreshCw, Trash2, Edit2,
  Database, Clock, CheckCircle2, XCircle, Loader2, AlertCircle,
  Repeat, Timer, GitBranch, X, Search, ChevronLeft, ShieldCheck,
    RotateCw, Activity, Waves, DatabaseZap, ExternalLink,
} from 'lucide-react'
import { pipelineTasksApi, WRITE_MODE_META, type PipelineTask, type PipelineTaskStats, type WriteMode, type LakeImpact } from '@/api/v2/pipeline-tasks'
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

// 毛玻璃基础样式 - 紧凑、圆角更小、阴影更轻
const GLASS = 'backdrop-blur-xl bg-white/70 border border-white/80 shadow-[0_2px_12px_rgba(15,23,42,0.04)] rounded-lg overflow-hidden'

/** 后端裸时间戳按 UTC 处理：无时区标识则补 Z，避免被 JS 当成本地时间产生偏移 */
function toLocalDate(iso: string): Date {
  const hasTz = /(Z|[+-]\d\d:?\d\d)$/.test(iso)
  return new Date(hasTz ? iso : iso + 'Z')
}

/** 标准时间的两行紧凑展示：日期在上、时间在下（省表格横向宽度） */
function TimeStack({ iso, withSeconds }: { iso: string | null; withSeconds?: boolean }) {
  if (!iso) return <span className="text-[11px] text-slate-400">—</span>
  try {
    const d = toLocalDate(iso)
    const p = (n: number) => String(n).padStart(2, '0')
    const date = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
    const time = withSeconds
      ? `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
      : `${p(d.getHours())}:${p(d.getMinutes())}`
    return (
      <div className="text-center tabular-nums leading-tight whitespace-nowrap">
        <div className="text-[11px] text-slate-600">{date}</div>
        <div className="text-[10px] text-slate-400">{time}</div>
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

  // ── 旧版同步任务（DataSyncTask）—— 调度器仍在跑但页面不可见 ──
  const [legacyTasks, setLegacyTasks] = useState<SyncTask[]>([])
  const [legacyLoading, setLegacyLoading] = useState(true)
  const [legacyDisablingId, setLegacyDisablingId] = useState<string | null>(null)

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
      setActionError('任务数据加载失败，请检查服务状态后重试')
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
    try {
      await syncTasksApi.toggle(id, false)
      setLegacyTasks(prev => prev.map(t => t.id === id ? { ...t, enabled: false } : t))
    } catch {
      // 静默失败
    } finally {
      setLegacyDisablingId(null)
    }
  }

  const handleDisableAllLegacy = async () => {
    const enabled = legacyTasks.filter(t => t.enabled)
    for (const t of enabled) {
      try { await syncTasksApi.toggle(t.id, false) } catch { /* continue */ }
    }
    loadLegacy()
  }

  const legacyEnabledCount = legacyTasks.filter(t => t.enabled).length
  const legacyHasActive = legacyTasks.some(t => t.enabled && (t.schedule_type === 'CRON' || t.schedule_type === 'INTERVAL'))

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
        {/* ── 顶部：标题 + 5 个 KPI + 新建按钮 —— 整合到同一个毛玻璃卡片（加高呼吸感） ── */}
        <div className={`${GLASS} px-4 py-3.5 mb-3 shrink-0 flex items-center gap-5`}>
          {/* 左侧：图标 + 标题 */}
          <div className="flex items-center gap-2.5 shrink-0">
            <span className="inline-flex items-center justify-center w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500 to-cyan-400 text-white shadow-[0_3px_10px_rgba(59,130,246,0.25)]">
              <DatabaseZap size={17} />
            </span>
            <div>
              <h2 className="text-[15px] font-semibold text-slate-800 leading-tight tracking-tight">
                数据任务池
              </h2>
              <p className="text-[11px] text-slate-400 mt-0.5 leading-tight">调度 · 入湖 · 可观测</p>
            </div>
          </div>

          {/* 分隔线 */}
          <div className="w-px h-10 bg-slate-200/70 shrink-0" />

          {/* 中间：5 个 KPI 指标卡（主值 + 累计副值） */}
          <div className="flex-1 grid grid-cols-5 gap-2.5">
            <KpiCard label="总任务数" value={stats?.total ?? 0} icon={<Database size={13} />} tone="slate" />
            <KpiCard label="启用任务数" value={stats?.enabled ?? 0} icon={<CheckCircle2 size={13} />} tone="emerald" />
            <KpiCard label="今日执行" value={stats?.today_runs ?? 0} icon={<Activity size={13} />} tone="violet"
              secondary={`累计 ${stats?.total_runs ?? 0}`} />
            <KpiCard label="今日异常" value={stats?.today_errors ?? 0} icon={<AlertCircle size={13} />} tone="rose"
              pulse={(stats?.today_errors ?? 0) > 0} secondary={`累计 ${stats?.total_errors ?? 0}`} />
            <KpiCard label="今日成功率" value={todaySuccessRate} icon={<Waves size={13} />} tone="cyan" />
          </div>

          {/* 右侧：新建按钮 */}
          <button
            onClick={handleCreate}
            className="shrink-0 flex items-center gap-1.5 px-4 h-9 bg-gradient-to-r from-blue-500 to-blue-600 text-white text-[12.5px] font-medium rounded-lg shadow-[0_3px_10px_rgba(59,130,246,0.3)] hover:shadow-[0_5px_16px_rgba(59,130,246,0.4)] hover:-translate-y-0.5 transition-all"
          >
            <Plus size={14} />
            新建任务
          </button>
        </div>

        {/* ── 主体 9:3 ── */}
        <div className="flex-1 grid grid-cols-12 gap-3 min-h-0">
          {/* 左侧 */}
          <div className="col-span-9 flex flex-col min-h-0">
            {/* ── 旧版同步任务提醒 ── */}
            {!legacyLoading && legacyTasks.length > 0 && (
              <div className={`mb-2.5 px-3 py-2 rounded-lg border text-[11.5px] leading-relaxed shrink-0 ${
                legacyHasActive
                  ? 'bg-amber-50/80 border-amber-200/70 text-amber-800'
                  : 'bg-slate-50/80 border-slate-200/60 text-slate-600'
              }`}>
                {legacyHasActive ? (
                  <>
                    <div className="flex items-center gap-2 mb-1">
                      <AlertCircle size={13} className="shrink-0 text-amber-600" />
                      <span className="font-medium">
                        发现 {legacyTasks.length} 个旧版同步任务（{legacyEnabledCount} 个已启用），后台调度器仍在执行
                      </span>
                    </div>
                    <p className="text-[10.5px] text-amber-700/80 mb-1.5">
                      这些任务来自旧版同步系统（DataSyncTask），在"人工数据集"中产生 SYNC:: 数据集。
                      删除数据集时已自动禁用关联任务，但你也可手动处理遗留任务。
                    </p>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={handleDisableAllLegacy}
                        className="px-2.5 py-1 bg-amber-100 hover:bg-amber-200 text-amber-800 rounded text-[11px] font-medium transition-colors"
                      >
                        一键禁用全部启用任务
                      </button>
                      <span className="text-[10px] text-amber-600/60">
                        (也可在下方列表中逐个禁用)
                      </span>
                    </div>
                  </>
                ) : (
                  <div className="flex items-center gap-2">
                    <CheckCircle2 size={13} className="shrink-0 text-green-500" />
                    <span>{legacyTasks.length} 个旧版同步任务均已被禁用，不会产生新数据集</span>
                  </div>
                )}
              </div>
            )}

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
                          <th className="text-left font-medium px-3 py-1.5">任务名称</th>
                          <th className="text-center font-medium px-2 py-1.5">关联数据流水线</th>
                          <th className="text-center font-medium px-2 py-1.5">入库方式</th>
                          <th className="text-center font-medium px-2 py-1.5">调度方式</th>
                          <th className="text-center font-medium px-2 py-1.5">当前状态</th>
                          <th className="text-center font-medium px-2 py-1.5">最近执行</th>
                          <th className="text-center font-medium px-2 py-1.5">执行结果</th>
                          <th className="text-center font-medium px-2 py-1.5">下一次执行时间</th>
                          <th className="text-center font-medium px-3 py-1.5">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tasks.map(t => {
                          const isTriggering = triggeringIds.has(t.id)
                          const wm = WRITE_MODE_META[t.write_mode as WriteMode]
                          const pipelineGone = t.pipeline_status === 'deleted'
                          const pipelineUnpub = !pipelineGone && t.pipeline_status && t.pipeline_status !== 'published'
                          const pipelineDisabled = !pipelineGone && t.pipeline_enabled === false
                          const sch = SCHEDULE_LABEL[t.schedule_type] || SCHEDULE_LABEL.MANUAL
                          const SchIcon = sch.Icon
                          return (
                            <tr key={t.id} className="border-b border-slate-100/60 last:border-b-0 hover:bg-white/60 transition-colors group">
                              {/* 任务名称（左对齐，无竖线） */}
                              <td className="px-3 py-2">
                                <div className="min-w-0">
                                  <div className="font-medium text-slate-800 text-[12.5px] truncate max-w-[180px]">{t.name}</div>
                                  {t.description && <div className="text-[10.5px] text-slate-400 truncate max-w-[200px]">{t.description}</div>}
                                </div>
                              </td>
                              {/* 关联数据流水线：点击跳转管理页并检索该流水线 */}
                              <td className="px-2 py-2">
                                <div className="flex flex-col items-center">
                                  <button
                                    onClick={() => !pipelineGone && navigate(`/data/pipelines?search=${encodeURIComponent(t.pipeline_name || t.pipeline_id)}`)}
                                    className={`flex items-center gap-1 text-[11.5px] max-w-[170px] ${pipelineGone ? 'text-slate-400 cursor-default' : 'text-blue-600 hover:underline underline-offset-2'}`}
                                    title={pipelineGone ? '流水线已删除' : '前往数据流水线管理页并检索该流水线'}
                                  >
                                    <GitBranch size={10} className="shrink-0" />
                                    <span className="truncate">{t.pipeline_name || t.pipeline_id.slice(0, 8)}</span>
                                    {!pipelineGone && <ExternalLink size={9} className="shrink-0 opacity-60" />}
                                  </button>
                                  {(pipelineGone || pipelineUnpub || pipelineDisabled) && (
                                    <div className="text-[10px] text-rose-500 mt-0.5 flex items-center gap-0.5">
                                      <AlertCircle size={9} />{pipelineGone ? '已删除' : pipelineUnpub ? '未发布' : '流水线已停用'}
                                    </div>
                                  )}
                                </div>
                              </td>
                              {/* 入库方式 */}
                              <td className="px-2 py-2">
                                <div className="flex items-center justify-center gap-0.5">
                                  <span className="inline-flex items-center px-1.5 py-px rounded text-[10.5px] whitespace-nowrap bg-slate-100/80 text-slate-600 border border-slate-200/50" title={wm?.desc}>
                                    {wm?.label || t.write_mode}
                                  </span>
                                  {t.skip_empty && <span title="空输出保护"><ShieldCheck size={10} className="inline text-emerald-500" /></span>}
                                </div>
                              </td>
                              {/* 调度方式 */}
                              <td className="px-2 py-2">
                                <div className="flex flex-col items-center">
                                  <div className="flex items-center gap-1 text-[11.5px] text-slate-600 whitespace-nowrap">
                                    <SchIcon size={10} className={sch.color} />
                                    <span>{sch.label}</span>
                                  </div>
                                  {t.schedule_type === 'CRON' && t.cron_expression && (
                                    <div className="text-[10px] text-slate-400 font-mono truncate max-w-[92px]" title={t.cron_expression}>{t.cron_expression}</div>
                                  )}
                                  {t.schedule_type === 'INTERVAL' && !!t.interval_seconds && (
                                    <div className="text-[10px] text-slate-400">每 {relativeDuration(t.interval_seconds)}</div>
                                  )}
                                </div>
                              </td>
                              {/* 当前状态（开关） */}
                              <td className="px-2 py-2">
                                <div className="flex items-center justify-center gap-1.5">
                                  <Switch checked={t.enabled} onChange={() => handleToggle(t)} />
                                  <span className={`text-[11px] ${t.enabled ? 'text-emerald-600' : 'text-slate-400'}`}>
                                    {t.enabled ? '已启用' : '未启用'}
                                  </span>
                                </div>
                              </td>
                              {/* 最近执行（标准时间） */}
                              <td className="px-2 py-2">
                                <div className="flex flex-col items-center">
                                  <TimeStack iso={t.last_run_at} withSeconds />
                                  {t.status === 'failed' && t.last_error && (
                                    <div className="text-[10px] text-rose-500 truncate max-w-[120px] mt-0.5" title={t.last_error}>{t.last_error}</div>
                                  )}
                                </div>
                              </td>
                              {/* 执行结果（资产湖影响） */}
                              <td className="px-2 py-2">
                                <div className="flex justify-center">
                                  <ExecResultCell impact={t.last_impact} status={t.status} />
                                </div>
                              </td>
                              {/* 下一次执行时间 */}
                              <td className="px-2 py-2">
                                <div className="flex justify-center"><NextRunCell task={t} /></div>
                              </td>
                              {/* 操作 */}
                              <td className="px-3 py-2">
                                <div className="flex items-center justify-center gap-0 opacity-60 group-hover:opacity-100 transition-opacity">
                                  <IconBtn2 title={pipelineDisabled ? '关联流水线已停用' : '立即执行'}
                                    disabled={t.status === 'running' || isTriggering || pipelineGone || !!pipelineUnpub || pipelineDisabled}
                                    onClick={() => handleTrigger(t)} accent="blue">
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

            {/* ── 旧版同步任务列表 ── */}
            {!legacyLoading && legacyTasks.length > 0 && (
              <div className={`${GLASS} mt-2.5 shrink-0`}>
                <div className="px-3 py-2 border-b border-slate-100 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <History size={13} className="text-slate-400" />
                    <span className="text-[12px] font-medium text-slate-600">
                      旧版同步任务（{legacyTasks.length} 个）
                    </span>
                    <span className="text-[10px] text-slate-400">
                      · {legacyTasks.filter(t => t.enabled).length} 启用 · {legacyTasks.filter(t => t.schedule_type === 'INTERVAL').length} INTERVAL · {legacyTasks.filter(t => t.schedule_type === 'CRON').length} CRON
                    </span>
                  </div>
                </div>
                <div className="max-h-[180px] overflow-auto scrollbar-thin">
                  <table className="w-full text-[11.5px]">
                    <thead className="sticky top-0 bg-slate-50/90">
                      <tr className="text-slate-500 text-[10.5px]">
                        <th className="text-left font-medium px-3 py-1">任务名称</th>
                        <th className="text-center font-medium px-2 py-1">模式</th>
                        <th className="text-center font-medium px-2 py-1">调度</th>
                        <th className="text-center font-medium px-2 py-1">状态</th>
                        <th className="text-center font-medium px-3 py-1">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {legacyTasks.map(t => (
                        <tr key={t.id} className="border-t border-slate-100 hover:bg-slate-50/40">
                          <td className="px-3 py-1.5">
                            <span className="text-slate-700">{t.name}</span>
                          </td>
                          <td className="text-center px-2 py-1.5">
                            <span className={`px-1.5 py-0.5 rounded text-[10.5px] ${
                              t.sync_mode === 'SNAPSHOT' ? 'bg-blue-50 text-blue-600' : 'bg-violet-50 text-violet-600'
                            }`}>{t.sync_mode}</span>
                          </td>
                          <td className="text-center px-2 py-1.5">
                            {t.schedule_type === 'INTERVAL' ? (
                              <span className="text-blue-600 text-[10.5px]">{t.interval_seconds}s</span>
                            ) : t.schedule_type === 'CRON' ? (
                              <span className="text-violet-600 text-[10.5px]">{t.cron_expression}</span>
                            ) : (
                              <span className="text-slate-400 text-[10.5px]">手动</span>
                            )}
                          </td>
                          <td className="text-center px-2 py-1.5">
                            <span className={`px-1.5 py-0.5 rounded text-[10.5px] ${
                              t.enabled
                                ? 'bg-green-50 text-green-600'
                                : 'bg-slate-100 text-slate-400'
                            }`}>{t.enabled ? '启用' : '禁用'}</span>
                          </td>
                          <td className="text-center px-3 py-1.5">
                            {t.enabled && (
                              <button
                                onClick={() => handleDisableLegacy(t.id)}
                                disabled={legacyDisablingId === t.id}
                                className="px-2 py-0.5 bg-slate-100 hover:bg-slate-200 text-slate-600 rounded text-[10.5px] transition-colors disabled:opacity-50"
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
                                : t.pipeline_enabled === false ? '流水线已停用'
                                : t.last_error ? t.last_error.slice(0, 26) : '执行失败'}
                            </span>
                          </div>
                        </div>
                        <div className="flex items-center gap-px shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                          {t.status === 'failed' && t.pipeline_status === 'published' && t.pipeline_enabled !== false && (
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

function KpiCard({
  label, value, icon, tone, pulse, secondary,
}: {
  label: string; value: number | string; icon: React.ReactNode
  tone: 'slate' | 'blue' | 'rose' | 'emerald' | 'violet' | 'cyan'
  pulse?: boolean; secondary?: string
}) {
  const toneMap = {
    slate:   { text: 'text-slate-800',   iconBg: 'bg-slate-100 text-slate-500' },
    blue:    { text: 'text-blue-600',    iconBg: 'bg-blue-50 text-blue-500' },
    rose:    { text: 'text-rose-600',    iconBg: 'bg-rose-50 text-rose-500' },
    emerald: { text: 'text-emerald-600', iconBg: 'bg-emerald-50 text-emerald-500' },
    violet:  { text: 'text-violet-600',  iconBg: 'bg-violet-50 text-violet-500' },
    cyan:    { text: 'text-cyan-600',    iconBg: 'bg-cyan-50 text-cyan-500' },
  }[tone]
  return (
    <div className="px-3 py-2 flex flex-col justify-center gap-1.5 rounded-xl bg-white/45 border border-white/70 hover:bg-white/65 transition-all">
      <div className="flex items-center gap-1.5">
        <span className={`w-5 h-5 rounded-md ${toneMap.iconBg} flex items-center justify-center shrink-0 relative`}>
          {icon}
          {pulse && (
            <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-current animate-ping opacity-60" />
          )}
        </span>
        <span className="text-[11px] text-slate-500 truncate">{label}</span>
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className={`text-[22px] font-semibold tabular-nums tracking-tight leading-none ${toneMap.text}`}>{value}</span>
        {secondary && <span className="text-[10.5px] text-slate-400 tabular-nums truncate">{secondary}</span>}
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

// ── 启用开关 ──────────────────────────────────────────
function Switch({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <button
      type="button" role="switch" aria-checked={checked} onClick={onChange}
      title={checked ? '点击停用' : '点击启用'}
      className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors shrink-0 ${
        checked ? 'bg-emerald-500' : 'bg-slate-300'
      }`}
    >
      <span className={`inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${
        checked ? 'translate-x-3.5' : 'translate-x-0.5'
      }`} />
    </button>
  )
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
    <div className="flex flex-col items-center">
      <TimeStack iso={task.next_run_at} />
      <div className="text-[10px] text-blue-500/80 mt-px whitespace-nowrap">{formatFuture(task.next_run_at)}</div>
    </div>
  )
}

// ── 执行结果：最近一次执行对资产湖的影响 ──────────────
function ExecResultCell({ impact, status }: { impact?: LakeImpact | null; status: string }) {
  if (status === 'failed') return <span className="text-[11px] text-rose-400">执行失败</span>
  if (!impact) return <span className="text-[11px] text-slate-300">—</span>
  const added = impact.added ?? 0, updated = impact.updated ?? 0, deleted = impact.deleted ?? 0
  if (!added && !updated && !deleted) return <span className="text-[11px] text-slate-400">无变化</span>
  return (
    <div className="flex flex-col items-center gap-px text-[10.5px] leading-tight tabular-nums whitespace-nowrap">
      <span className="text-emerald-600">新增 {added} 行</span>
      <span className="text-rose-600">删除 {deleted} 行</span>
      <span className="text-amber-600">更新 {updated} 行</span>
    </div>
  )
}
