import { useEffect, useMemo, useState, type MouseEvent, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  CheckCircle, AlertTriangle, Clock,
  X, Loader2, Trash2, Table2, RefreshCw,
  Eye, XCircle, Workflow, ListChecks, Database,
  Boxes, Network, ArrowRight, BarChart3,
} from 'lucide-react'
import pipelinesApi, { type Pipeline } from '@/api/v2/pipelines'
import curatedApi from '@/api/v2/curated'
import type { CuratedDataset } from '@/api/v2/curated'
import { pipelineTasksApi, type PipelineTask } from '@/api/v2/pipeline-tasks'
import datasetsApi from '@/api/v2/datasets'
import CuratedDetailPanel from './CuratedDetailPanel'
import RawDatasetsView from './RawDatasetsView'
import ConfirmDialog from '@/components/ConfirmDialog'

interface Row {
  pipelineId: string
  pipelineName: string
  domain: string
  curatedId: string
  curatedName: string
  curatedStatus: string
  rowCount: number | null
  quality: number | null
  hasReviewEvidence: boolean
}

const STATUS_ICON = (status: string) => {
  if (status === 'approved') return <CheckCircle size={13} className="text-green-500" />
  if (status === 'rejected') return <AlertTriangle size={13} className="text-red-400" />
  return <Clock size={13} className="text-yellow-400" />
}

const STATUS_LABEL: Record<string, string> = {
  pending_review: '待审核',
  pending:        '待审核',
  in_review:      '审核中',
  approved:       '已审核',
  rejected:       '已拒绝',
}

const STATUS_STYLE: Record<string, string> = {
  pending_review: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  pending:        'bg-yellow-50 text-yellow-700 border-yellow-200',
  in_review:      'bg-blue-50 text-blue-700 border-blue-200',
  approved:       'bg-green-50 text-green-700 border-green-200',
  rejected:       'bg-red-50 text-red-600 border-red-200',
}

type LakeTab = 'curated' | 'raw'

const isPendingReview = (status: string) => status === 'pending_review' || status === 'pending' || status === 'in_review'
const ASSET_CHANGED_EVENT = 'ontoprompt:data-assets-changed'

const notifyAssetChanged = () => window.dispatchEvent(new Event(ASSET_CHANGED_EVENT))

function errorText(error: unknown, fallback: string): string {
  if (!error || typeof error !== 'object') return fallback
  const e = error as { detail?: unknown; data?: { detail?: unknown }; message?: unknown }
  const detail = e.detail ?? e.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message
  }
  return typeof e.message === 'string' && e.message.trim() ? e.message : fallback
}

function FlowArrow() {
  return (
    <div className="flex items-center min-w-8 flex-1" aria-hidden="true">
      <span className="h-px w-full border-t border-dashed border-slate-300" />
      <ArrowRight size={14} className="-ml-1 shrink-0 text-slate-400" />
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
      className={`group flex h-10 shrink-0 items-center gap-2 rounded-lg border px-3 text-xs font-semibold transition-colors ${
        active
          ? 'border-emerald-400 bg-emerald-50 text-emerald-800 shadow-[inset_0_0_0_1px_rgba(16,185,129,0.12)]'
          : 'border-slate-200 bg-white text-slate-700 hover:border-teal-300 hover:bg-teal-50/50 disabled:hover:border-slate-200 disabled:hover:bg-white'
      }`}
    >
      <span className={`grid h-6 w-6 place-items-center rounded-md ${active ? 'bg-emerald-600 text-white' : 'bg-slate-100 text-slate-600 group-hover:bg-teal-100 group-hover:text-teal-700'}`}>
        {icon}
      </span>
      <span className="whitespace-nowrap">{label}</span>
      {active && <span className="rounded-full bg-emerald-600 px-1.5 py-0.5 text-[9px] font-medium text-white">当前</span>}
    </button>
  )
}

/** 洞察只使用接口返回的真实数据；任何接口失败时都明确提示，不补造指标。 */
function AssetInsightStrip() {
  const [retryToken, setRetryToken] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [metrics, setMetrics] = useState<Array<{ label: string; value: string; note: string }> | null>(null)

  useEffect(() => {
    const refresh = () => {
      setLoading(true)
      setError('')
      setRetryToken(token => token + 1)
    }
    window.addEventListener(ASSET_CHANGED_EVENT, refresh)
    return () => window.removeEventListener(ASSET_CHANGED_EVENT, refresh)
  }, [])

  useEffect(() => {
    let alive = true
    Promise.all([curatedApi.list(), datasetsApi.overview()])
      .then(([curatedResult, rawResult]) => {
        if (!alive) return
        const curatedItems = Array.isArray(curatedResult) ? curatedResult : []
        const rawItems = Array.isArray(rawResult?.items) ? rawResult.items : []
        const manualItems = rawItems.filter(item => item.source === 'upload' || item.source === 'manual')
        const legacySyncCount = rawItems.filter(item => item.source === 'sync').length
        const scored = curatedItems
          .map(item => item.quality_score)
          .filter((score): score is number => typeof score === 'number' && Number.isFinite(score))
        const avgQuality = scored.length
          ? `${Math.round((scored.reduce((sum, score) => sum + score, 0) / scored.length) * 100)}%`
          : '—'
        setMetrics([
          { label: '数据集总数', value: String(curatedItems.length + rawItems.length), note: `成品 ${curatedItems.length} · 人工 ${manualItems.length}${legacySyncCount ? ` · 历史同步 ${legacySyncCount}` : ''}` },
          { label: '待审核', value: String(curatedItems.filter(item => isPendingReview(item.status)).length), note: '需要人工确认的成品数据集' },
          { label: '平均质量分', value: avgQuality, note: scored.length ? `基于 ${scored.length} 个已评分成品` : '暂无已评分成品' },
          { label: '人工数据集', value: String(manualItems.length), note: '文件上传或在线维护' },
          { label: '已声明主键', value: String(manualItems.filter(item => Boolean(item.primary_key)).length), note: '具备主键契约的人工数据集' },
        ])
      })
      .catch(error => {
        if (!alive) return
        setMetrics(null)
        setError(errorText(error, '洞察数据加载失败'))
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [retryToken])

  if (loading) {
    return (
      <div className="grid shrink-0 grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5" aria-label="洞察加载中">
        {Array.from({ length: 5 }).map((_, index) => <div key={index} className="h-[74px] animate-pulse rounded-xl border border-slate-200 bg-slate-100/70" />)}
      </div>
    )
  }

  if (error || !metrics) {
    return (
      <div className="flex shrink-0 items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        <AlertTriangle size={15} className="shrink-0" />
        <span className="flex-1">{error || '洞察数据不可用'}</span>
        <button
          type="button"
          onClick={() => { setLoading(true); setError(''); setRetryToken(token => token + 1) }}
          className="rounded-md border border-red-200 bg-white px-2.5 py-1 text-xs hover:bg-red-100"
        >重试</button>
      </div>
    )
  }

  return (
    <div className="grid shrink-0 grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
      {metrics.map(metric => (
        <div key={metric.label} className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm/50">
          <p className="text-[11px] font-medium text-slate-500">{metric.label}</p>
          <p className="mt-0.5 text-xl font-semibold tabular-nums text-slate-900">{metric.value}</p>
          <p className="mt-0.5 truncate text-[10px] text-slate-400" title={metric.note}>{metric.note}</p>
        </div>
      ))}
    </div>
  )
}

export default function StructuredDataPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab: LakeTab = searchParams.get('tab') === 'raw' ? 'raw' : 'curated'
  const [insightSelected, setInsightSelected] = useState(true)
  const focusDatasetId = searchParams.get('dataset')

  const switchTab = (tab: LakeTab) => {
    setSearchParams(prev => {
      const n = new URLSearchParams(prev)
      n.set('tab', tab)
      n.delete('dataset')
      return n
    }, { replace: true })
  }

  const TABS: [LakeTab, string][] = [['curated', '成品数据集'], ['raw', '人工数据集']]

  return (
    <div className="flex h-full flex-col gap-3">
      {/* 不重复页面标题，首屏直接呈现用户真正需要理解和操作的数据流。 */}
      <div className="shrink-0 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm/50">
        <div className="flex items-center gap-4">
          <div className="min-w-0 flex-1 overflow-x-auto pb-0.5">
            <div className="flex min-w-[650px] items-center">
              <FlowNode label="n8n 流水线" icon={<Workflow size={14} />} onClick={() => navigate('/data/pipelines')} />
              <FlowArrow />
              <FlowNode label="数据任务池" icon={<ListChecks size={14} />} onClick={() => navigate('/data/pipelines/sync-tasks')} />
              <FlowArrow />
              <FlowNode label="数据资产湖" icon={<Database size={14} />} active />
              <FlowArrow />
              <FlowNode label="成品 / 人工数据集" icon={<Boxes size={14} />} />
              <FlowArrow />
              <FlowNode label="本体模型" icon={<Network size={14} />} onClick={() => navigate('/ontologies')} />
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 p-1">
            <div className="relative grid grid-cols-2 rounded-md">
              <span
                aria-hidden="true"
                className={`absolute inset-y-0 left-0 w-1/2 rounded-md bg-emerald-600 shadow-sm transition-transform duration-300 ease-out ${activeTab === 'raw' ? 'translate-x-full' : 'translate-x-0'}`}
              />
              {TABS.map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => switchTab(key)}
                  className={`relative z-10 rounded-md px-3 py-1.5 text-xs font-medium transition-colors duration-200 ${
                    activeTab === key ? 'text-white' : 'text-slate-500 hover:text-emerald-700'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <span className="mx-0.5 h-5 w-px bg-slate-200" aria-hidden="true" />
            <button
              type="button"
              aria-pressed={insightSelected}
              onClick={() => setInsightSelected(selected => !selected)}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                insightSelected
                  ? 'bg-emerald-600 text-white shadow-sm'
                  : 'bg-slate-200 text-slate-600 hover:bg-slate-300'
              }`}
              title={insightSelected ? '隐藏数据洞察' : '显示数据洞察'}
            >
              <BarChart3 size={13} /> 洞察
            </button>
          </div>
        </div>
      </div>

      {insightSelected && <AssetInsightStrip />}

      {/* 下方内容区域 —— 单页展示，内容区可滚动 */}
      <div className="flex-1 min-h-0">
        <div key={activeTab} className="h-full animate-lake-tab-in">
          {activeTab === 'raw'
            ? <RawDatasetsView focusDatasetId={focusDatasetId} />
            : <CuratedView />}
        </div>
      </div>
    </div>
  )
}

/** 成品数据集（Curated）视图：流水线 × 产物 关联表 */
function CuratedView() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [pipelineTasks, setPipelineTasks] = useState<PipelineTask[]>([])
  const [curated, setCurated] = useState<CuratedDataset[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [curatedLoadFailed, setCuratedLoadFailed] = useState(false)
  const [actionError, setActionError] = useState('')
  const [taskFilter, setTaskFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const [panelRow, setPanelRow] = useState<Row | null>(null)
  const [approvingId, setApprovingId] = useState<string | null>(null)
  const [rejectingId, setRejectingId] = useState<string | null>(null)
  const [deleteRow, setDeleteRow] = useState<Row | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteErr, setDeleteErr] = useState('')

  const load = async () => {
    setLoading(true)
    setLoadError('')
    const results = await Promise.allSettled([
      pipelinesApi.list(),
      pipelineTasksApi.list(),
      curatedApi.list() as Promise<CuratedDataset[]>,
    ])
    const [pipelineResult, taskResult, curatedResult] = results
    if (pipelineResult.status === 'fulfilled') {
      setPipelines(Array.isArray(pipelineResult.value) ? pipelineResult.value : [])
    }
    if (taskResult.status === 'fulfilled') {
      setPipelineTasks(Array.isArray(taskResult.value?.items) ? taskResult.value.items : [])
    }
    if (curatedResult.status === 'fulfilled') {
      setCurated(Array.isArray(curatedResult.value) ? curatedResult.value : [])
      setCuratedLoadFailed(false)
    } else {
      setCuratedLoadFailed(true)
    }
    const failures = [
      pipelineResult.status === 'rejected' ? `流水线：${errorText(pipelineResult.reason, '加载失败')}` : '',
      taskResult.status === 'rejected' ? `数据任务：${errorText(taskResult.reason, '加载失败')}` : '',
      curatedResult.status === 'rejected' ? `成品数据集：${errorText(curatedResult.reason, '加载失败')}` : '',
    ].filter(Boolean)
    if (failures.length) setLoadError(failures.join('；'))
    setLoading(false)
  }

  useEffect(() => { void Promise.resolve().then(load) }, [])

  // 以成品数据集为主视角，关联其来源流水线
  const allRows = useMemo<Row[]>(() => {
    const rows: Row[] = []
    const claimed = new Set<string>()

    const pushRow = (c: CuratedDataset, pl?: Pipeline) => rows.push({
      pipelineId: pl?.id ?? '', pipelineName: pl?.name ?? '—', domain: pl?.domain || '通用',
      curatedId: c.id, curatedName: c.name, curatedStatus: c.status || 'pending_review',
      rowCount: c.row_count ?? null, quality: c.quality_score ?? null,
      hasReviewEvidence: Boolean(c.has_review_evidence),
    })

    const curatedById = new Map(curated.map(c => [c.id, c]))
    pipelines.forEach(pl => {
      curated.filter(c => !claimed.has(c.id) && c.producer_pipeline_id === pl.id).forEach(c => {
        claimed.add(c.id); pushRow(c, pl)
      })
    })
    // legacy 资产只接受流水线明确保存的 target id 绑定，名称不再作为身份。
    pipelines.forEach(pl => {
      const ids: string[] = pl.target_curated_ids ?? []
      ids.forEach(cid => {
        const c = curatedById.get(cid)
        if (c && !claimed.has(c.id)) { claimed.add(c.id); pushRow(c, pl) }
      })
    })
    // 无来源流水线的孤儿产物也要可见可管理
    curated.filter(c => !claimed.has(c.id)).forEach(c => pushRow(c))
    return rows
  }, [pipelines, curated])

  // 已发布状态的流水线（用于筛选下拉）
  const publishedPipelines = useMemo(() =>
    pipelines.filter(p => p.status === 'published'),
  [pipelines])

  // 兼容历史深链传名称和新深链传 ID；筛选内部只使用解析后的稳定 ID。
  const pipelineFilter = searchParams.get('pipeline') || ''
  const normalizedPipelineFilter = useMemo(() => {
    if (!pipelineFilter) return ''
    return pipelines.find(pipeline => pipeline.id === pipelineFilter || pipeline.name === pipelineFilter)?.id ?? pipelineFilter
  }, [pipelineFilter, pipelines])

  const changePipelineFilter = (value: string) => {
    setSearchParams(previous => {
      const next = new URLSearchParams(previous)
      if (value) next.set('pipeline', value)
      else next.delete('pipeline')
      return next
    }, { replace: true })
  }

  // 选中任务关联的流水线 ID
  const taskPipelineId = useMemo(() => {
    if (!taskFilter) return null
    return pipelineTasks.find(task => task.id === taskFilter)?.pipeline_id || null
  }, [taskFilter, pipelineTasks])

  const filtered = useMemo(() => {
    return allRows.filter(r => {
      if (normalizedPipelineFilter && r.pipelineId !== normalizedPipelineFilter) return false
      if (taskPipelineId && r.pipelineId !== taskPipelineId) return false
      if (statusFilter === 'pending_review' && !isPendingReview(r.curatedStatus)) return false
      if (statusFilter && statusFilter !== 'pending_review' && r.curatedStatus !== statusFilter) return false
      return true
    })
  }, [allRows, normalizedPipelineFilter, taskPipelineId, statusFilter])

  const clearFilters = () => {
    changePipelineFilter('')
    setTaskFilter('')
    setStatusFilter('')
  }

  const handleStatusChange = (id: string, newStatus: string) => {
    setCurated(prev => prev.map(c => c.id === id ? { ...c, status: newStatus } : c))
    if (panelRow?.curatedId === id) setPanelRow(r => r ? { ...r, curatedStatus: newStatus } : r)
    notifyAssetChanged()
  }

  const handleDeleted = (id: string) => {
    setCurated(prev => prev.filter(c => c.id !== id))
    setPanelRow(null)
    setDeleteRow(null)
    notifyAssetChanged()
  }

  const handleQuickApprove = async (e: MouseEvent, row: Row) => {
    e.stopPropagation()
    if (!row.curatedId) return
    setApprovingId(row.curatedId)
    setActionError('')
    try {
      const result = await curatedApi.approve(row.curatedId) as {
        mapping_dispatch?: { status?: string; error?: string }
      }
      handleStatusChange(row.curatedId, 'approved')
      if (result?.mapping_dispatch?.status === 'failed') {
        setActionError(result.mapping_dispatch.error || '数据已批准，但自动灌入本体失败，请检查映射任务。')
      }
    } catch (error) {
      setActionError(`批准失败：${errorText(error, '请稍后重试')}`)
    } finally { setApprovingId(null) }
  }

  const handleQuickReject = async (e: MouseEvent, row: Row) => {
    e.stopPropagation()
    if (!row.curatedId) return
    setRejectingId(row.curatedId)
    setActionError('')
    try {
      await curatedApi.reject(row.curatedId)
      handleStatusChange(row.curatedId, 'rejected')
    } catch (error) {
      setActionError(`驳回失败：${errorText(error, '请稍后重试')}`)
    } finally { setRejectingId(null) }
  }

  const handleQuickDelete = async () => {
    if (!deleteRow?.curatedId) return
    setDeleting(true)
    setDeleteErr('')
    try {
      await curatedApi.delete(deleteRow.curatedId)
      handleDeleted(deleteRow.curatedId)
      setDeleteRow(null)
    } catch (error: unknown) {
      const raw = errorText(error, '删除失败')
      setDeleteErr(raw === 'Admin required' ? '删除数据集需要管理员权限' : String(raw))
      setDeleteRow(null)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm/50 h-full flex flex-col">
      {/* 筛选 */}
      <div className="shrink-0 flex gap-3 flex-wrap items-center px-5 pt-4 pb-3 border-b border-gray-100">
        {/* 按流水线筛选（仅已发布） */}
        <select
          value={publishedPipelines.some(pipeline => pipeline.id === normalizedPipelineFilter) ? normalizedPipelineFilter : ''}
          onChange={e => changePipelineFilter(e.target.value)}
          className="px-3 py-1.5 border rounded-lg text-sm text-gray-600 bg-white"
        >
          <option value="">全部已发布流水线</option>
          {publishedPipelines.map(pl => (
            <option key={pl.id} value={pl.id}>{pl.name}</option>
          ))}
        </select>

        {/* 按数据任务筛选 */}
        <select
          value={taskFilter}
          onChange={e => setTaskFilter(e.target.value)}
          className="px-3 py-1.5 border rounded-lg text-sm text-gray-600 bg-white"
        >
          <option value="">全部数据任务</option>
          {pipelineTasks.map(task => (
            <option key={task.id} value={task.id}>{task.name}</option>
          ))}
        </select>

        {/* 审核状态 */}
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="px-3 py-1.5 border rounded-lg text-sm text-gray-600 bg-white"
        >
          <option value="">全部审核状态</option>
          <option value="pending_review">待审核</option>
          <option value="approved">已审核</option>
          <option value="rejected">已拒绝</option>
        </select>

        {/* 清除筛选 */}
        {(pipelineFilter || taskFilter || statusFilter) && (
          <button
            onClick={clearFilters}
            className="flex items-center gap-1 text-xs text-red-500 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50 transition-colors"
          >
            <X size={11} /> 清除筛选
          </button>
        )}

        <button onClick={load} className="ml-auto flex items-center gap-1 text-xs text-gray-500 hover:text-gray-800 px-2 py-1.5">
          <RefreshCw size={12} /> 刷新
        </button>
      </div>

      {(loadError || actionError) && (
        <div className="mx-5 mt-3 flex shrink-0 items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span className="flex-1">{actionError || `部分数据加载失败：${loadError}`}</span>
          {loadError && (
            <button type="button" onClick={load} className="rounded border border-red-200 bg-white px-2 py-0.5 hover:bg-red-100">重试</button>
          )}
          {actionError && <button type="button" onClick={() => setActionError('')} className="text-red-400 hover:text-red-700" aria-label="关闭错误提示">×</button>}
        </div>
      )}

      {/* 表格 — 可滚动 */}
      <div className="flex-1 overflow-y-auto px-5 py-3">
      {loading ? (
        <div className="flex items-center justify-center gap-2 p-12 text-sm text-gray-400">
          <Loader2 size={16} className="animate-spin" /> 加载数据集...
        </div>
      ) : curatedLoadFailed && curated.length === 0 ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-10 text-center text-red-700">
          <AlertTriangle size={28} className="mx-auto mb-2 opacity-70" />
          <p className="text-sm font-medium">成品数据集加载失败</p>
          <p className="mt-1 text-xs text-red-500">{loadError}</p>
          <button type="button" onClick={load} className="mt-3 rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs hover:bg-red-100">重新加载</button>
        </div>
      ) : allRows.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl p-12 text-center text-gray-400 space-y-2">
          <Table2 size={32} className="mx-auto opacity-30" />
          <p className="text-sm font-medium">暂无成品数据集</p>
          <p className="text-xs">运行数据流水线后，加工产物会自动出现在这里</p>
          <button
            onClick={() => navigate('/data/pipelines')}
            className="text-xs px-3 py-1.5 mt-1 bg-[var(--color-nav-bg)] text-white rounded-lg hover:opacity-90"
          >
            去流水线运行
          </button>
        </div>
      ) : filtered.length === 0 ? (
        <div className="border rounded-xl p-8 text-center text-gray-400 text-sm">没有匹配的数据集</div>
      ) : (
        <div className="border rounded-xl overflow-hidden bg-white">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">数据集</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">来源流水线</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">领域</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">行数</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">质量分</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">审核状态</th>
                <th className="px-4 py-2.5 text-gray-600 text-xs text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {filtered.map((row, idx) => (
                <tr
                  key={`${row.pipelineId}-${row.curatedId}-${idx}`}
                  onClick={() => row.curatedId && setPanelRow(row)}
                  className={`transition-colors ${row.curatedId ? 'cursor-pointer hover:bg-gray-50' : 'opacity-60'}`}
                >
                  <td className="px-4 py-3 font-medium text-gray-800 max-w-[240px]">
                    <span className="block truncate" title={row.curatedName}>{row.curatedName}</span>
                    <span className="text-xs text-gray-400 font-mono font-normal">{row.curatedId.slice(0, 8)}</span>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-600 max-w-[160px] truncate" title={row.pipelineName}>
                    {row.pipelineName}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">{row.domain}</td>
                  <td className="px-4 py-3 text-xs text-gray-600">
                    {row.rowCount != null ? `${row.rowCount} 行` : '—'}
                  </td>
                  <td className="px-4 py-3 text-xs">
                    {row.quality != null ? (
                      <span className={row.quality >= 0.9 ? 'text-green-600' : row.quality >= 0.7 ? 'text-yellow-600' : 'text-red-500'}>
                        {(row.quality * 100).toFixed(0)}%
                      </span>
                    ) : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-3">
                    {row.curatedStatus ? (
                      <span className={`inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border ${STATUS_STYLE[row.curatedStatus] || 'bg-gray-100 text-gray-600 border-gray-200'}`}>
                        {STATUS_ICON(row.curatedStatus)}
                        {STATUS_LABEL[row.curatedStatus] || row.curatedStatus}
                      </span>
                    ) : (
                      <span className="text-xs text-gray-300">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right" onClick={e => e.stopPropagation()}>
                    <div className="flex items-center gap-1 justify-end">
                      {/* 批准 — 仅待审核 */}
                      {row.curatedId && isPendingReview(row.curatedStatus) && (
                        <button
                          onClick={e => handleQuickApprove(e, row)}
                          disabled={approvingId === row.curatedId}
                          className="p-1.5 rounded hover:bg-green-50 text-gray-400 hover:text-green-600 disabled:opacity-50"
                          title="批准"
                        >
                          {approvingId === row.curatedId
                            ? <Loader2 size={13} className="animate-spin" />
                            : <CheckCircle size={13} />}
                        </button>
                      )}

                      {/* 驳回 — 仅待审核 */}
                      {row.curatedId && isPendingReview(row.curatedStatus) && (
                        <button
                          onClick={e => handleQuickReject(e, row)}
                          disabled={rejectingId === row.curatedId}
                          className="p-1.5 rounded hover:bg-orange-50 text-gray-400 hover:text-orange-600 disabled:opacity-50"
                          title="驳回"
                        >
                          {rejectingId === row.curatedId
                            ? <Loader2 size={13} className="animate-spin" />
                            : <XCircle size={13} />}
                        </button>
                      )}

                      {/* 删除 — 待审核 / 已拒绝 */}
                      {row.curatedId && !row.hasReviewEvidence && (isPendingReview(row.curatedStatus) || row.curatedStatus === 'rejected') && (
                        <button
                          onClick={() => setDeleteRow(row)}
                          className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-500"
                          title="删除"
                        >
                          <Trash2 size={13} />
                        </button>
                      )}

                      {/* 查看 — 所有状态 */}
                      {row.curatedId && (
                        <button
                          onClick={() => setPanelRow(row)}
                          className="p-1.5 rounded hover:bg-blue-50 text-gray-400 hover:text-blue-600"
                          title="查看详情"
                        >
                          <Eye size={13} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      </div>

      {/* 详情面板 */}
      {panelRow && (
        <CuratedDetailPanel
          key={panelRow.curatedId}
          datasetId={panelRow.curatedId}
          datasetName={panelRow.curatedName}
          datasetStatus={panelRow.curatedStatus}
          pipelineName={panelRow.pipelineName}
          onClose={() => setPanelRow(null)}
          onStatusChange={handleStatusChange}
          onDeleted={handleDeleted}
        />
      )}

      {/* 删除确认 */}
      <ConfirmDialog
        open={!!deleteRow}
        title="删除数据集"
        message={`确认删除「${deleteRow?.curatedName}」？将永久删除该数据集及其全部历史版本，不可恢复。若已被流水线或本体映射引用，删除会被拦截。`}
        confirmLabel={deleting ? '删除中...' : '确认删除'}
        onConfirm={handleQuickDelete}
        onCancel={() => setDeleteRow(null)}
      />

      {deleteErr && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[60] max-w-md px-4 py-2.5 bg-red-600 text-white text-sm rounded-lg shadow-lg flex items-start gap-2">
          <span className="flex-1">{deleteErr}</span>
          <button onClick={() => setDeleteErr('')} className="text-white/70 hover:text-white shrink-0">×</button>
        </div>
      )}
    </div>
  )
}
