import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Plus, Play, Pause, History, RefreshCw, Trash2, Edit2,
  Database, Clock, CheckCircle2, XCircle, Loader2, AlertCircle,
  Repeat, Timer, GitBranch, X, Circle, Search,
  ChevronLeft, ShieldCheck,
} from 'lucide-react'
import { pipelineTasksApi, WRITE_MODE_META, type PipelineTask, type PipelineTaskStats, type WriteMode } from '@/api/v2/pipeline-tasks'
import TaskFormModal from './TaskFormModal'
import HistoryDrawer from './HistoryDrawer'
import ConfirmDialog from '@/components/ConfirmDialog'

const STATUS_META: Record<string, { icon: React.ReactNode; label: string; bg: string }> = {
  idle:    { icon: <Clock size={12} />,      label: '待运行', bg: 'bg-gray-100 text-gray-600' },
  running: { icon: <Loader2 size={12} className="animate-spin" />, label: '执行中', bg: 'bg-blue-50 text-blue-600' },
  success: { icon: <CheckCircle2 size={12} />, label: '成功',   bg: 'bg-green-50 text-green-600' },
  failed:  { icon: <XCircle size={12} />,    label: '失败',   bg: 'bg-red-50 text-red-600' },
}

const STATUS_FILTER_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'idle', label: '待运行' },
  { value: 'running', label: '执行中' },
  { value: 'success', label: '成功' },
  { value: 'failed', label: '失败' },
]

const PAGE_SIZE_OPTIONS = [10, 20, 50]

const SCHEDULE_LABEL: Record<string, string> = {
  MANUAL: '手动',
  CRON: 'Cron',
  INTERVAL: '间隔',
}

function formatDate(iso: string | null): string {
  if (!iso) return '-'
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch { return iso }
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
  // 从流水线发布弹窗跳转而来：预选该流水线并直接打开新建任务向导
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
  const [filterStatus, setFilterStatus] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [total, setTotal] = useState(0)

  const load = useCallback(async () => {
    setRefreshing(true)
    try {
      const params: Record<string, unknown> = { page, page_size: pageSize }
      if (filterStatus) params.status = filterStatus
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
  }, [page, pageSize, filterStatus, search])

  useEffect(() => { load() }, [load])

  // 10 秒轮询
  const loadRef = useRef(load)
  loadRef.current = load
  useEffect(() => {
    const timer = setInterval(() => loadRef.current(), 10_000)
    return () => clearInterval(timer)
  }, [])

  // 搜索防抖 300ms
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput)
      setPage(1)
    }, 300)
    return () => clearTimeout(timer)
  }, [searchInput])

  const handleStatusChange = (value: string) => {
    setFilterStatus(value)
    setPage(1)
  }

  const handleCreate = () => {
    setEditingTask(null)
    setShowForm(true)
  }

  const handleEdit = (task: PipelineTask) => {
    setEditingTask(task)
    setShowForm(true)
  }

  const handleFormSaved = () => {
    setShowForm(false)
    setEditingTask(null)
    setPresetPipelineId(null)
    load()
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await pipelineTasksApi.delete(deleteTarget.id)
      setDeleteTarget(null)
      load()
    } catch {
      setDeleteTarget(null)
    }
  }

  const handleToggle = async (task: PipelineTask) => {
    try {
      await pipelineTasksApi.toggle(task.id, !task.enabled)
      load()
    } catch {
      setActionError('切换启用状态失败')
    }
  }

  const handleTrigger = async (task: PipelineTask) => {
    if (task.status === 'running') return
    setTriggeringIds(prev => new Set(prev).add(task.id))
    setActionError('')
    try {
      await pipelineTasksApi.trigger(task.id, false)
      setTimeout(load, 1500)
    } catch (err: any) {
      setActionError(err?.detail || err?.message || '触发失败')
    } finally {
      setTriggeringIds(prev => {
        const n = new Set(prev); n.delete(task.id); return n
      })
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div>
      {/* 头部 */}
      <div className="flex items-start justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <Repeat size={22} className="text-[var(--color-nav-bg)]" />
            数据任务池
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            流水线与资产湖之间的调度方：按计划触发<b>已发布</b>的流水线，把流水线的<b>最终产物</b>按入库方式写进数据资产湖
          </p>
        </div>
        <button
          onClick={handleCreate}
          className="flex items-center gap-1.5 px-3.5 py-2 bg-[var(--color-nav-bg)] text-white text-sm font-medium rounded-lg hover:opacity-90 transition-opacity"
        >
          <Plus size={15} />
          新建调度任务
        </button>
      </div>

      {/* 统计卡片 */}
      {stats && (
        <div className="grid grid-cols-4 gap-3 mb-5">
          <StatCard label="任务总数" value={stats.total} icon={<Database size={16} />} />
          <StatCard label="执行中" value={stats.running} icon={<Circle size={16} />} color="text-blue-600" />
          <StatCard label="已启用" value={stats.enabled} icon={<CheckCircle2 size={16} />} color="text-green-600" />
          <StatCard label="异常" value={stats.failed} icon={<AlertCircle size={16} />} color="text-red-600" />
        </div>
      )}

      {actionError && (
        <div className="flex items-center gap-2 px-4 py-2.5 mb-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-600">
          <XCircle size={14} className="shrink-0" />
          <span className="flex-1">{actionError}</span>
          <button onClick={() => setActionError('')} className="text-gray-400 hover:text-gray-600"><X size={13} /></button>
        </div>
      )}

      {/* 筛选栏 + 刷新 */}
      <div className="flex items-center gap-3 mb-3">
        <div className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-200 rounded-lg flex-1 max-w-xs">
          <Search size={14} className="text-gray-400 shrink-0" />
          <input
            type="text"
            placeholder="搜索任务名..."
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            className="text-xs text-gray-700 placeholder-gray-400 bg-transparent outline-none w-full"
          />
          {searchInput && (
            <button onClick={() => { setSearchInput(''); setSearch(''); setPage(1) }}
              className="text-gray-400 hover:text-gray-600">
              <X size={12} />
            </button>
          )}
        </div>
        <select
          value={filterStatus}
          onChange={e => handleStatusChange(e.target.value)}
          className="px-3 py-1.5 text-xs border border-gray-200 rounded-lg bg-white text-gray-600 outline-none focus:ring-1 focus:ring-[var(--color-nav-bg)]"
        >
          {STATUS_FILTER_OPTIONS.map(opt => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
        <div className="flex-1" />
        <button onClick={load} disabled={refreshing}
          className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 disabled:opacity-50 shrink-0">
          <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      {/* 任务列表 */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {loading ? (
          <div className="py-16 text-center text-gray-400 text-sm">加载中...</div>
        ) : tasks.length === 0 ? (
          search || filterStatus ? (
            <div className="py-16 text-center text-gray-400 text-sm">
              没有匹配的任务
              <button
                onClick={() => { setSearchInput(''); setSearch(''); setFilterStatus(''); setPage(1) }}
                className="ml-1 text-[var(--color-nav-bg)] hover:underline"
              >
                清除筛选
              </button>
            </div>
          ) : (
            <EmptyState onCreate={handleCreate} />
          )
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-xs border-b border-gray-200">
                <th className="text-left px-4 py-2.5 font-medium">任务名</th>
                <th className="text-left px-4 py-2.5 font-medium">调度的流水线</th>
                <th className="text-left px-4 py-2.5 font-medium">入库方式</th>
                <th className="text-left px-4 py-2.5 font-medium">调度</th>
                <th className="text-left px-4 py-2.5 font-medium">状态</th>
                <th className="text-left px-4 py-2.5 font-medium">最后执行</th>
                <th className="text-right px-4 py-2.5 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map(t => {
                const sm = STATUS_META[t.status] || STATUS_META.idle
                const isTriggering = triggeringIds.has(t.id)
                const wm = WRITE_MODE_META[t.write_mode as WriteMode]
                const pipelineGone = t.pipeline_status === 'deleted'
                const pipelineUnpublished = !pipelineGone && t.pipeline_status !== 'published'
                return (
                  <tr key={t.id} className="border-b border-gray-100 hover:bg-gray-50/60 transition-colors">
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-900">{t.name}</div>
                      {t.description && <div className="text-xs text-gray-400 mt-0.5 truncate max-w-[200px]">{t.description}</div>}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => !pipelineGone && navigate(`/data/pipelines/${t.pipeline_id}`)}
                        className={`flex items-center gap-1 text-xs ${pipelineGone ? 'text-gray-400 cursor-default' : 'text-[var(--color-nav-bg)] hover:underline'}`}
                        title={pipelineGone ? '流水线已被删除' : '打开流水线画布'}
                      >
                        <GitBranch size={11} />
                        <span className="truncate max-w-[180px]">{t.pipeline_name || t.pipeline_id.slice(0, 8)}</span>
                        {t.pipeline_version ? <span className="text-gray-400">v{t.pipeline_version}</span> : null}
                      </button>
                      {(pipelineGone || pipelineUnpublished) && (
                        <div className="text-xs text-red-500 mt-0.5">
                          {pipelineGone ? '⚠ 流水线已删除' : '⚠ 流水线已退回未发布'}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-gray-100 text-gray-700" title={wm?.desc}>
                        {wm?.label || t.write_mode}
                      </span>
                      {t.write_mode === 'upsert' && t.primary_key && (
                        <div className="text-xs text-gray-400 mt-0.5">主键: {t.primary_key}</div>
                      )}
                      {t.skip_empty && (
                        <div className="flex items-center gap-0.5 text-[10px] text-gray-400 mt-0.5" title="流水线输出 0 行时跳过入库">
                          <ShieldCheck size={9} /> 空输出保护
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1 text-xs text-gray-600">
                        {t.schedule_type === 'MANUAL' ? <Clock size={12} className="text-gray-400" /> :
                         t.schedule_type === 'CRON' ? <Timer size={12} className="text-purple-500" /> :
                         <Repeat size={12} className="text-blue-500" />}
                        <span>
                          {SCHEDULE_LABEL[t.schedule_type]}
                          {t.schedule_type === 'CRON' && t.cron_expression ? ` · ${t.cron_expression}` : ''}
                          {t.schedule_type === 'INTERVAL' && t.interval_seconds ? ` · ${t.interval_seconds}s` : ''}
                        </span>
                      </div>
                      <div className="mt-1">
                        <button
                          onClick={() => handleToggle(t)}
                          className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] ${t.enabled
                            ? 'bg-green-50 text-green-600' : 'bg-gray-100 text-gray-400'}`}
                        >
                          {t.enabled ? <Play size={9} /> : <Pause size={9} />}
                          {t.enabled ? '已启用' : '已停用'}
                        </button>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs ${sm.bg}`}>
                        {sm.icon}{sm.label}
                      </span>
                      {t.status === 'failed' && t.last_error && (
                        <div className="text-xs text-red-500 mt-0.5 max-w-[180px] truncate" title={t.last_error}>
                          {t.last_error}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="text-xs text-gray-600">{formatDate(t.last_run_at)}</div>
                      {t.last_rows > 0 && (
                        <div className="text-xs text-gray-400">入湖 {t.last_rows} 行</div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="inline-flex items-center gap-0.5">
                        <IconBtn title="立即执行" disabled={t.status === 'running' || isTriggering || pipelineGone} onClick={() => handleTrigger(t)}>
                          <Play size={13} className={isTriggering ? 'animate-spin' : ''} />
                        </IconBtn>
                        <IconBtn title="执行历史" onClick={() => setHistoryTask(t)}>
                          <History size={13} />
                        </IconBtn>
                        <IconBtn title="编辑" onClick={() => handleEdit(t)}>
                          <Edit2 size={13} />
                        </IconBtn>
                        <IconBtn title="删除" danger onClick={() => setDeleteTarget(t)}>
                          <Trash2 size={13} />
                        </IconBtn>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}

        {/* 分页栏 */}
        {!loading && total > 0 && (
          <div className="flex items-center justify-between px-4 py-2.5 border-t border-gray-100 bg-gray-50/50">
            <div className="text-xs text-gray-500">共 {total} 条</div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="p-1 rounded text-gray-400 hover:text-gray-700 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <ChevronLeft size={14} />
              </button>
              <span className="text-xs text-gray-600">
                第 {page}/{totalPages} 页
              </span>
              <button
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="p-1 rounded text-gray-400 hover:text-gray-700 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <ChevronLeft size={14} className="rotate-180" />
              </button>
              <select
                value={pageSize}
                onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }}
                className="ml-3 px-2 py-1 text-xs border border-gray-200 rounded bg-white text-gray-600 outline-none"
              >
                {PAGE_SIZE_OPTIONS.map(n => (
                  <option key={n} value={n}>每页 {n} 条</option>
                ))}
              </select>
            </div>
          </div>
        )}
      </div>
      {/* 新建/编辑 Modal */}
      {showForm && (
        <TaskFormModal
          initialTask={editingTask}
          initialPipelineId={presetPipelineId}
          onClose={() => { setShowForm(false); setEditingTask(null); setPresetPipelineId(null) }}
          onSaved={handleFormSaved}
        />
      )}

      {/* 历史抽屉 */}
      {historyTask && (
        <HistoryDrawer
          task={historyTask}
          onClose={() => setHistoryTask(null)}
        />
      )}

      {/* 删除确认 */}
      <ConfirmDialog
        open={!!deleteTarget}
        title="删除调度任务"
        message={`确定删除任务「${deleteTarget?.name}」？流水线本身与已入湖的数据不受影响。`}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}

function StatCard({ label, value, icon, color = 'text-gray-700' }: {
  label: string; value: number; icon: React.ReactNode; color?: string
}) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 px-4 py-3 flex items-center justify-between">
      <div>
        <div className="text-xs text-gray-500">{label}</div>
        <div className={`text-2xl font-bold mt-0.5 ${color}`}>{value}</div>
      </div>
      <div className={`${color} opacity-60`}>{icon}</div>
    </div>
  )
}

function IconBtn({ children, onClick, title, disabled, danger }: {
  children: React.ReactNode; onClick?: () => void; title?: string; disabled?: boolean; danger?: boolean
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`p-1.5 rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed
        ${danger ? 'text-gray-400 hover:text-red-500 hover:bg-red-50' : 'text-gray-400 hover:text-gray-700 hover:bg-gray-100'}`}
    >
      {children}
    </button>
  )
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="py-20 flex flex-col items-center text-center">
      <div className="w-14 h-14 rounded-full bg-gray-50 flex items-center justify-center mb-4">
        <Repeat size={24} className="text-gray-300" />
      </div>
      <div className="text-gray-600 font-medium mb-1">暂无调度任务</div>
      <div className="text-xs text-gray-400 mb-4 max-w-sm">
        创建任务：选择一条已发布的流水线，设定入库方式与调度节奏，流水线的最终产物将按计划写进数据资产湖
      </div>
      <button onClick={onCreate}
        className="flex items-center gap-1.5 px-4 py-2 bg-[var(--color-nav-bg)] text-white text-sm font-medium rounded-lg hover:opacity-90">
        <Plus size={14} />
        新建第一个调度任务
      </button>
    </div>
  )
}
