import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Plus, Search, Play, GitBranch, Trash2, Pencil, ChevronLeft, ChevronRight,
  X, Loader2, CheckCircle2, XCircle, Clock, Table2, ListChecks, Sparkles, ExternalLink,
  AlertCircle, FileCode2, Copy, MoreHorizontal, Activity,
} from 'lucide-react'
import pipelinesApi from '@/api/v2/pipelines'
import type { Pipeline, PipelineOverview } from '@/api/v2/pipelines'
import { getPipelineEngine } from '@/api/v2/pipelines'
import { stewardApi } from '@/api/steward'
import type { StewardStatus } from '@/api/steward'
import ConfirmDialog from '@/components/ConfirmDialog'
import { useToast } from '@/components/ui/Toast'
import RunPreviewModal from './RunPreviewModal'
import PipelineEditWizard from './PipelineEditWizard'

// 发布状态：draft/published 双态（运行态在「最近执行结果」列，不混入生命周期）；
// editing/running/failed 是 0008 迁移前的遗留值，展示上归为草稿
const STATUS_STYLE: Record<string, string> = {
  draft:     'bg-slate-100 text-slate-600 border-slate-200',
  published: 'bg-teal-50 text-teal-700 border-teal-200',
}

const STATUS_LABEL: Record<string, string> = {
  draft: '未发布', published: '已发布',
}

const normStatus = (s?: string): 'draft' | 'published' => (s === 'published' ? 'published' : 'draft')

const RUN_STATUS_META: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  success: { icon: <CheckCircle2 size={12} />, label: '成功', color: 'text-green-600' },
  failed:  { icon: <XCircle size={12} />,      label: '失败', color: 'text-red-500' },
  running: { icon: <Loader2 size={12} className="animate-spin" />, label: '运行中', color: 'text-blue-600' },
  pending: { icon: <Clock size={12} />,        label: '排队中', color: 'text-gray-500' },
}

function isN8nPipeline(pl: Pipeline): boolean {
  return getPipelineEngine(pl) === 'n8n'
}

function isPythonPipeline(pl: Pipeline): boolean {
  return getPipelineEngine(pl) === 'python'
}

function mergePipeline(current: Pipeline, updated: Pipeline): Pipeline {
  const currentDefinition = current.definition as Record<string, unknown> | null
  const updatedDefinition = updated.definition as Record<string, unknown> | null
  const currentN8n = currentDefinition?.n8n as Record<string, unknown> | undefined
  const updatedN8n = updatedDefinition?.n8n as Record<string, unknown> | undefined

  return {
    ...current,
    ...updated,
    definition: updatedDefinition
      ? {
          ...currentDefinition,
          ...updatedDefinition,
          ...(currentN8n || updatedN8n
            ? { n8n: { ...currentN8n, ...updatedN8n } }
            : {}),
        } as Pipeline['definition']
      : current.definition,
  }
}

function updateOverview(
  current: PipelineOverview | null,
  before?: Pipeline,
  after?: Pipeline,
): PipelineOverview | null {
  if (!current) return current
  const contributes = (pipeline: Pipeline | undefined, key: 'published' | 'enabled' | 'latest_failed') => {
    if (!pipeline) return 0
    if (key === 'published') return normStatus(pipeline.status) === 'published' ? 1 : 0
    if (key === 'enabled') return (pipeline.enabled ?? true) ? 1 : 0
    return pipeline.last_run_status === 'failed' ? 1 : 0
  }
  return {
    total: Math.max(0, current.total + (after ? 1 : 0) - (before ? 1 : 0)),
    published: Math.max(0, current.published + contributes(after, 'published') - contributes(before, 'published')),
    enabled: Math.max(0, current.enabled + contributes(after, 'enabled') - contributes(before, 'enabled')),
    latest_failed: Math.max(0, current.latest_failed + contributes(after, 'latest_failed') - contributes(before, 'latest_failed')),
  }
}

function formatTime(iso?: string | null): string {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  } catch { return iso }
}

function EnabledSwitch({ on, busy, lockReason, onToggle, onLocked }: {
  on: boolean
  busy: boolean
  lockReason?: string
  onToggle: () => void
  onLocked?: () => void
}) {
  const locked = !!lockReason
  return (
    <button
      type="button"
      role="switch" aria-label={on ? '停用流水线' : '启用流水线'} aria-checked={on} aria-disabled={locked} disabled={busy}
      onClick={locked ? onLocked : onToggle}
      className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
        on ? 'bg-teal-700' : 'bg-slate-300'} ${busy ? 'opacity-60' : locked ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
      title={locked
        ? lockReason
        : on ? '已启用：任务池调度与联动触发会执行该流水线' : '未启用：任务池调度与联动触发将跳过（仍可手动执行试运行）'}
    >
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${on ? 'left-[18px]' : 'left-0.5'}`} />
    </button>
  )
}

/** 弹性列宽：table-fixed + 百分比，各列按比例自适应浏览器宽度 */

export default function PipelineListPage() {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [searchParams] = useSearchParams()
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState(() => searchParams.get('search') || '')
  const [filterSource, setFilterSource] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [filterEnabled, setFilterEnabled] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [total, setTotal] = useState(0)
  const [overview, setOverview] = useState<PipelineOverview | null>(null)

  const [showCreate, setShowCreate] = useState(false)
  const [previewTarget, setPreviewTarget] = useState<Pipeline | null>(null)
  const [editTarget, setEditTarget] = useState<Pipeline | null>(null)
  const [n8nApiUrl, setN8nApiUrl] = useState('')
  const [n8nStatus, setN8nStatus] = useState<StewardStatus['n8n'] | null>(null)
  const [pythonStatus, setPythonStatus] = useState<StewardStatus['python'] | null>(null)
  const [togglingId, setTogglingId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Pipeline | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [cloneTarget, setCloneTarget] = useState<Pipeline | null>(null)
  const [cloning, setCloning] = useState(false)
  const [actionMenuId, setActionMenuId] = useState<string | null>(null)
  const [actionMenuPosition, setActionMenuPosition] = useState<React.CSSProperties>({})

  const load = useCallback(() => {
    setLoading(true)
    pipelinesApi.listPage({
      search: search || undefined,
      engine: filterSource || undefined,
      status: filterStatus || undefined,
      enabled: filterEnabled ? filterEnabled === 'enabled' : undefined,
      page,
      page_size: pageSize,
    })
      .then(res => {
        setPipelines(Array.isArray(res.items) ? res.items : [])
        setTotal(res.total || 0)
        setOverview(res.overview ?? null)
        if (page > 1 && res.items.length === 0 && res.total > 0) setPage(page - 1)
      })
      .catch(() => {
        setPipelines([])
        setTotal(0)
        setOverview(null)
        toast({
          tone: 'error',
          title: '流水线列表加载失败',
          description: '请检查服务连接后重试。',
        })
      })
      .finally(() => setLoading(false))
  }, [filterEnabled, filterSource, filterStatus, page, pageSize, search, toast])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    stewardApi.status()
      .then(s => {
        setN8nStatus(s.n8n)
        setN8nApiUrl(s.n8n.api_url)
        setPythonStatus(s.python ?? null)
      })
      .catch(() => {
        setN8nStatus({
          configured: false,
          enabled: false,
          api_url: '',
          reachable: false,
          error: '无法读取 n8n 配置状态',
        })
        setPythonStatus(null)
      })
  }, [])

  useEffect(() => {
    if (!actionMenuId) return
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target as Element | null
      if (!target?.closest('[data-pipeline-action-menu]')) setActionMenuId(null)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setActionMenuId(null)
    }
    const closeOnViewportChange = () => setActionMenuId(null)
    document.addEventListener('pointerdown', closeOnOutside)
    document.addEventListener('keydown', closeOnEscape)
    window.addEventListener('resize', closeOnViewportChange)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutside)
      document.removeEventListener('keydown', closeOnEscape)
      window.removeEventListener('resize', closeOnViewportChange)
    }
  }, [actionMenuId])

  const matchesActiveFilters = useCallback((pl: Pipeline) => {
    const keyword = search.trim().toLowerCase()
    if (keyword && !pl.name.toLowerCase().includes(keyword) && !pl.id.toLowerCase().includes(keyword)) {
      return false
    }
    if (filterSource && getPipelineEngine(pl) !== filterSource) return false
    if (filterStatus && normStatus(pl.status) !== filterStatus) return false
    if (filterEnabled) {
      const enabled = pl.enabled ?? true
      if ((filterEnabled === 'enabled') !== enabled) return false
    }
    return true
  }, [filterEnabled, filterSource, filterStatus, search])

  const insertPipelineLocally = (pl: Pipeline, toastTitle = '流水线已创建') => {
    setOverview(current => updateOverview(current, undefined, pl))
    if (matchesActiveFilters(pl)) {
      setTotal(current => current + 1)
      if (page === 1) {
        setPipelines(current => [pl, ...current.filter(item => item.id !== pl.id)].slice(0, pageSize))
      }
    }
    toast({
      tone: 'success',
      title: toastTitle,
      description: page === 1 && matchesActiveFilters(pl)
        ? `「${pl.name}」已加入当前列表。`
        : `「${pl.name}」已创建，可调整筛选或返回第一页查看。`,
    })
  }

  const updatePipelineLocally = (updated: Pipeline) => {
    const current = pipelines.find(item => item.id === updated.id)
    if (!current) return
    const merged = mergePipeline(current, updated)
    setOverview(value => updateOverview(value, current, merged))
    const remainsVisible = matchesActiveFilters(merged)
    setPipelines(items => remainsVisible
      ? items.map(item => item.id === merged.id ? merged : item)
      : items.filter(item => item.id !== merged.id))
    if (!remainsVisible) setTotal(value => Math.max(0, value - 1))
    toast({
      tone: 'success',
      title: '流水线已更新',
      description: remainsVisible
        ? `「${merged.name}」的信息已局部更新。`
        : `「${merged.name}」已更新，并因当前筛选条件从列表中移除。`,
    })
  }

  const handleToggleEnabled = async (pl: Pipeline) => {
    const next = !(pl.enabled ?? true)
    const optimistic = { ...pl, enabled: next }
    setTogglingId(pl.id)
    setPipelines(ps => ps.map(p => p.id === pl.id ? { ...p, enabled: next } : p))
    setOverview(current => updateOverview(current, pl, optimistic))
    try {
      await pipelinesApi.setEnabled(pl.id, next)
      const updated = optimistic
      if (!matchesActiveFilters(updated)) {
        setPipelines(items => items.filter(item => item.id !== pl.id))
        setTotal(value => Math.max(0, value - 1))
      }
      toast({
        tone: 'success',
        title: next ? '流水线已启用' : '流水线已停用',
        description: `「${pl.name}」的启用状态已更新。`,
      })
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setPipelines(ps => ps.map(p => p.id === pl.id ? { ...p, enabled: !next } : p))
      setOverview(current => updateOverview(current, optimistic, pl))
      toast({
        tone: 'error',
        title: '启用状态更新失败',
        description: err?.detail || err?.message || '请稍后重试。',
      })
    } finally {
      setTogglingId(null)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    const target = deleteTarget
    setDeleting(true)
    try {
      await pipelinesApi.delete(target.id)
      setDeleteTarget(null)
      setPipelines(items => items.filter(item => item.id !== target.id))
      setTotal(value => Math.max(0, value - 1))
      setOverview(current => updateOverview(current, target, undefined))
      toast({
        tone: 'success',
        title: '流水线已归档',
        description: `「${target.name}」已从当前列表移除。`,
      })
    } catch (e: unknown) {
      // 典型场景：被调度任务引用（后端引用保护 400）
      const err = e as { detail?: string; message?: string }
      setDeleteTarget(null)
      toast({
        tone: 'error',
        title: '流水线归档失败',
        description: err?.detail || err?.message || '请稍后重试。',
      })
    } finally {
      setDeleting(false)
    }
  }

  const handleClone = async () => {
    if (!cloneTarget) return
    const target = cloneTarget
    setCloning(true)
    try {
      const cloned = await pipelinesApi.clone(target.id)
      setCloneTarget(null)
      insertPipelineLocally(cloned, '流水线已克隆')
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setCloneTarget(null)
      toast({
        tone: 'error',
        title: '流水线克隆失败',
        description: err?.detail || err?.message || '请稍后重试。',
      })
    } finally {
      setCloning(false)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const resetFilters = () => {
    setSearch('')
    setFilterSource('')
    setFilterStatus('')
    setFilterEnabled('')
    setPage(1)
  }

  const openEngineEditor = (pl: Pipeline) => {
    if (isPythonPipeline(pl)) {
      navigate(`/data/pipelines/script/${pl.id}`)
      return
    }
    if (!isN8nPipeline(pl)) return
    const definition = pl.definition as Record<string, unknown> | null
    const n8n = definition?.n8n as Record<string, unknown> | undefined
    const workflowId = n8n?.n8n_workflow_id as string | undefined
    if (!workflowId || !n8nApiUrl) return
    const webUrl = n8nApiUrl.replace(/\/api\/.*$/, '').replace(/\/+$/, '')
    window.open(`${webUrl}/workflow/${workflowId}`, '_blank', 'noopener,noreferrer')
  }

  const renderMoreActions = (
    pl: Pipeline,
    n8n: boolean,
    python: boolean,
    layout: 'mobile' | 'desktop',
  ) => {
    const definition = pl.definition as Record<string, unknown> | null
    const n8nDefinition = definition?.n8n as Record<string, unknown> | undefined
    const canOpenEngine = python || Boolean(n8n && n8nDefinition?.n8n_workflow_id && n8nApiUrl)
    const openLabel = n8n ? '打开 n8n 工作流' : '编辑 Python 脚本'
    const menuKey = `${layout}:${pl.id}`
    const menuOpen = actionMenuId === menuKey
    return (
      <div className="relative" data-pipeline-action-menu>
        <button
          type="button"
          onClick={event => {
            if (actionMenuId === menuKey) {
              setActionMenuId(null)
              return
            }
            const rect = event.currentTarget.getBoundingClientRect()
            const menuWidth = 192
            const left = Math.max(8, Math.min(
              window.innerWidth - menuWidth - 8,
              rect.right - menuWidth,
            ))
            const openUp = window.innerHeight - rect.bottom < 170 && rect.top > 170
            setActionMenuPosition(openUp
              ? { left, bottom: window.innerHeight - rect.top + 6 }
              : { left, top: rect.bottom + 6 })
            setActionMenuId(menuKey)
          }}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label={`更多操作：${pl.name}`}
          className="inline-flex h-9 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30"
        >
          <MoreHorizontal size={14} /> 更多
        </button>
        {menuOpen && createPortal(
          <div
            role="menu"
            data-pipeline-action-menu
            style={actionMenuPosition}
            className="fixed z-50 w-48 overflow-hidden rounded-xl border border-slate-200 bg-white p-1.5 text-left shadow-[0_16px_44px_rgba(15,23,42,0.16)]"
          >
            {(n8n || python) && (
              <button
                type="button"
                role="menuitem"
                disabled={!canOpenEngine}
                onClick={() => { openEngineEditor(pl); setActionMenuId(null) }}
                title={canOpenEngine ? openLabel : 'n8n 工作流地址暂不可用'}
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs text-slate-600 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ExternalLink size={14} /> {openLabel}
              </button>
            )}
            {(n8n || python) && (
              <button
                type="button"
                role="menuitem"
                onClick={() => { setCloneTarget(pl); setActionMenuId(null) }}
                title="克隆流水线结构为未发布草稿"
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs text-slate-600 transition hover:bg-slate-50 hover:text-slate-900"
              >
                <Copy size={14} /> 克隆为草稿
              </button>
            )}
            <div className="my-1 h-px bg-slate-100" />
            <button
              type="button"
              role="menuitem"
              onClick={() => { setDeleteTarget(pl); setActionMenuId(null) }}
              title="归档流水线"
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs text-rose-600 transition hover:bg-rose-50"
            >
              <Trash2 size={14} /> 归档流水线
            </button>
          </div>,
          document.body,
        )}
      </div>
    )
  }

  return (
    <div className="space-y-4 pb-4">
      <header className="flex flex-col gap-4 rounded-2xl border border-slate-200 bg-white px-5 py-5 shadow-[0_8px_30px_rgba(15,23,42,0.04)] sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-teal-700 text-white shadow-[0_6px_18px_rgba(15,118,110,0.18)]">
              <GitBranch size={17} />
            </span>
            <div>
              <h1 className="text-xl font-semibold tracking-tight text-slate-950">数据流水线</h1>
              <p className="mt-0.5 text-sm text-slate-500">管理采集编排，依次完成试执行、字段契约、发布与任务调度。</p>
            </div>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => navigate('/data/pipelines/steward')}
            className="flex h-10 items-center gap-1.5 rounded-xl border border-teal-200 bg-teal-50 px-3.5 text-sm font-medium text-teal-800 transition hover:bg-teal-100 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30"
            title="用对话创建与编排 n8n 数据流水线"
          >
            <Sparkles size={15} /> 数据管家
          </button>
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            className="flex h-10 items-center gap-1.5 rounded-xl bg-teal-700 px-3.5 text-sm font-medium text-white transition hover:bg-teal-800 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30"
          >
            <Plus size={15} /> 新建流水线
          </button>
        </div>
      </header>

      <section aria-label="流水线运行概览" className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <PipelineOverviewCard label="流水线总数" value={overview?.total ?? (loading ? '—' : total)} note="未归档的全部流水线" icon={<GitBranch size={14} />} tone="slate" />
        <PipelineOverviewCard label="已发布" value={overview?.published ?? '—'} note="字段契约与版本已锁定" icon={<CheckCircle2 size={14} />} tone="teal" />
        <PipelineOverviewCard label="已启用" value={overview?.enabled ?? '—'} note="可被任务池调度" icon={<Activity size={14} />} tone="emerald" />
        <PipelineOverviewCard label="最近执行失败" value={overview?.latest_failed ?? '—'} note="最新一次运行仍异常" icon={<XCircle size={14} />} tone="rose" alert={(overview?.latest_failed ?? 0) > 0} />
      </section>

      {pythonStatus?.configured === false && (
        <div role="status" className="flex items-start gap-2.5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <AlertCircle size={16} className="mt-0.5 shrink-0 text-amber-600" />
          <div>
            <p className="font-medium">Python 执行网关尚未配置</p>
            <p className="mt-0.5 text-xs leading-5 text-amber-700">仍可创建和编辑脚本草稿，但试执行、保存校验与任务调度暂不可用。请联系管理员配置 PYTHON_KERNEL_GATEWAY_URL。</p>
          </div>
        </div>
      )}

      {/* 搜索与筛选 */}
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 xl:flex-nowrap">
        <div className="relative w-full sm:w-64 xl:w-72 xl:flex-none">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(1) }}
            placeholder="搜索名称 / ID..."
            className="w-full rounded-xl border border-slate-200 py-2 pl-8 pr-3 text-sm outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10"
          />
          {search && (
            <button onClick={() => { setSearch(''); setPage(1) }} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black">
              <X size={12} />
            </button>
          )}
        </div>
        <select
          value={filterSource}
          onChange={e => { setFilterSource(e.target.value); setPage(1) }}
          className="shrink-0 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-teal-500"
        >
          <option value="">全部类型</option>
          <option value="n8n">n8n 流水线</option>
          <option value="python">Python 脚本</option>
        </select>
        <select
          value={filterStatus}
          onChange={e => { setFilterStatus(e.target.value); setPage(1) }}
          className="shrink-0 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-teal-500"
        >
          <option value="">全部发布状态</option>
          <option value="published">已发布</option>
          <option value="draft">未发布</option>
        </select>
        <select
          value={filterEnabled}
          onChange={e => { setFilterEnabled(e.target.value); setPage(1) }}
          className="shrink-0 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-teal-500"
        >
          <option value="">全部启用状态</option>
          <option value="enabled">已启用</option>
          <option value="disabled">未启用</option>
        </select>
        {(search || filterSource || filterStatus || filterEnabled) && (
          <button
            onClick={resetFilters}
            className="shrink-0 px-2 py-1 text-xs text-gray-500 hover:text-black"
          >
            清除筛选
          </button>
        )}
      </div>

      {/* 列表 */}
      {loading ? (
        <div className="text-gray-400 text-sm p-8 text-center">加载中...</div>
      ) : pipelines.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl p-12 text-center text-gray-400 space-y-2">
          <GitBranch size={32} className="mx-auto opacity-30" />
          <p className="text-sm font-medium">{search || filterSource || filterStatus || filterEnabled ? '没有匹配的流水线' : '暂无流水线'}</p>
          <p className="text-xs">新建 n8n 流水线后，可到数据管家完善编排，再通过编辑向导验证并发布</p>
        </div>
      ) : (
        <>
          <div className="space-y-3 lg:hidden">
            {pipelines.map(pl => {
              const runMeta = pl.last_run_status ? RUN_STATUS_META[pl.last_run_status] : null
              const n8n = isN8nPipeline(pl)
              const python = isPythonPipeline(pl)
              const enabled = pl.enabled ?? true
              const taskCount = pl.task_count ?? 0
              const curatedCount = pl.target_curated_ids?.length ?? 0
              const enableLockReason = taskCount > 0
                ? `流水线「${pl.name}」已被 ${taskCount} 个数据任务关联。为避免影响任务调度，请先在数据任务池删除或改绑关联任务，解除关联后再更改启用状态。`
                : !enabled && normStatus(pl.status) !== 'published'
                  ? '只有已发布的流水线才能启用，请先在编辑向导中完成发布'
                  : undefined
              return (
                <article key={pl.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_8px_30px_rgba(15,23,42,0.04)]">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h2 className="truncate text-sm font-semibold text-slate-950" title={pl.name}>{pl.name}</h2>
                      <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{pl.description || '暂未设置描述信息'}</p>
                    </div>
                    <span className={`inline-flex shrink-0 items-center rounded-full border px-2 py-1 text-[11px] ${STATUS_STYLE[normStatus(pl.status)]}`}>
                      {STATUS_LABEL[normStatus(pl.status)]}
                    </span>
                  </div>

                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <span className={`inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-medium ${n8n ? 'border-teal-200 bg-teal-50 text-teal-700' : python ? 'border-indigo-200 bg-indigo-50 text-indigo-700' : 'border-slate-200 bg-slate-50 text-slate-600'}`}>
                      {n8n ? <Sparkles size={11} /> : python ? <FileCode2 size={11} /> : <GitBranch size={11} />}
                      {n8n ? 'n8n 流水线' : python ? 'Python 脚本' : '未知引擎'}
                    </span>
                    {runMeta ? (
                      <span className={`inline-flex items-center gap-1 text-xs ${runMeta.color}`}>
                        {runMeta.icon}{runMeta.label}<span className="text-slate-400">· {formatTime(pl.last_run_at)}</span>
                      </span>
                    ) : <span className="text-xs text-slate-400">从未运行</span>}
                  </div>

                  <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-2.5">
                    <div className="inline-flex items-center gap-2">
                      <EnabledSwitch
                        on={enabled}
                        busy={togglingId === pl.id}
                        lockReason={enableLockReason}
                        onToggle={() => handleToggleEnabled(pl)}
                        onLocked={() => enableLockReason && toast({
                          tone: 'warning',
                          title: '当前无法切换启用状态',
                          description: enableLockReason,
                        })}
                      />
                      <span className={`text-xs ${enabled ? 'text-teal-700' : 'text-slate-400'}`}>{enabled ? '已启用' : '未启用'}</span>
                    </div>
                    <div className="flex items-center gap-3 text-xs text-slate-500">
                      <span>{curatedCount} 个数据集</span>
                      <span>{taskCount} 个任务</span>
                    </div>
                  </div>

                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setEditTarget(pl)}
                      className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-teal-200 bg-teal-50 px-3 text-xs font-medium text-teal-800 transition hover:bg-teal-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30"
                    >
                      <Pencil size={13} /> {normStatus(pl.status) === 'published' ? '查看契约' : '配置'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setPreviewTarget(pl)}
                      className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30"
                    >
                      <Play size={13} /> 试执行
                    </button>
                    <div className="ml-auto">{renderMoreActions(pl, n8n, python, 'mobile')}</div>
                  </div>
                </article>
              )
            })}
          </div>

          <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5 lg:hidden">
            <span className="text-xs tabular-nums text-slate-500">第 {page} / {totalPages} 页</span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setPage(current => Math.max(1, current - 1))}
                disabled={page <= 1}
                className="flex h-9 items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition-colors hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-35"
                aria-label="上一页"
              >
                <ChevronLeft size={14} /> 上一页
              </button>
              <button
                type="button"
                onClick={() => setPage(current => Math.min(totalPages, current + 1))}
                disabled={page >= totalPages}
                className="flex h-9 items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition-colors hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-35"
                aria-label="下一页"
              >
                下一页 <ChevronRight size={14} />
              </button>
            </div>
          </div>

          <div className="hidden overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.04)] lg:block">
          <table className="w-full min-w-[1080px] text-sm table-fixed">
            <thead className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50/95 backdrop-blur">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs rounded-tl-xl" style={{ width: '20%' }}>
                  流水线信息
                </th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs" style={{ width: '10%' }}>流水线类型</th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs" style={{ width: '9%' }}>发布状态</th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs" style={{ width: '9%' }}>启用状态</th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs" style={{ width: '13%' }}>最近执行结果</th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs" style={{ width: '9%' }}>产物</th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs" style={{ width: '9%' }}>关联任务</th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs rounded-tr-xl" style={{ width: '21%' }}>操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {pipelines.map(pl => {
                const runMeta = pl.last_run_status ? RUN_STATUS_META[pl.last_run_status] : null
                const runFailed = pl.last_run_status === 'failed' && !!pl.last_run_error
                const curatedCount = pl.target_curated_ids?.length ?? 0
                const taskCount = pl.task_count ?? 0
                const n8n = isN8nPipeline(pl)
                const python = isPythonPipeline(pl)
                const enabled = pl.enabled ?? true
                const enableLockReason = taskCount > 0
                  ? `流水线「${pl.name}」已被 ${taskCount} 个数据任务关联。为避免影响任务调度，请先在数据任务池删除或改绑关联任务，解除关联后再更改启用状态。`
                  : !enabled && normStatus(pl.status) !== 'published'
                    ? '只有已发布的流水线才能启用，请先在编辑向导中完成发布'
                    : undefined
                return (
                  <tr
                    key={pl.id}
                    className={`align-middle transition-colors hover:bg-slate-50/80 ${enabled ? '' : 'bg-slate-50/30'}`}
                  >
                    <td className="px-4 py-3 align-middle">
                      <p className="font-medium text-gray-900 truncate" title={pl.name}>{pl.name}</p>
                      <p className="text-xs text-gray-400 truncate" title={pl.description || undefined}>
                        {pl.description || '暂未设置描述信息'}
                      </p>
                    </td>
                    <td className="px-4 py-3 text-center align-middle whitespace-nowrap">
                      {n8n ? (
                        <span className="inline-flex w-[100px] justify-center whitespace-nowrap items-center gap-1 rounded-lg border border-teal-200 bg-teal-50 px-2 py-1 text-[11px] font-medium text-teal-700"
                          title="由数据管家托管的 n8n 流水线：编排在数据管家，发布在编辑向导，启用由本列表开关控制">
                          <Sparkles size={10} /> n8n 流水线
                        </span>
                      ) : python ? (
                        <span className="inline-flex w-[100px] justify-center whitespace-nowrap items-center gap-1 rounded-lg border border-indigo-200 bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-700"
                          title="Python 脚本流水线：在脚本编辑页编写取数脚本，输出 list[dict] 行数据入湖">
                          <FileCode2 size={10} /> Python 脚本
                        </span>
                      ) : (
                        <span className="whitespace-nowrap inline-flex items-center gap-1 rounded border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] text-gray-600"
                          title="存量数据缺少可识别的引擎标记（engine 非 n8n/python），无编排入口">
                          <GitBranch size={10} /> 未知引擎
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center align-middle whitespace-nowrap">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[11px] ${STATUS_STYLE[normStatus(pl.status)]}`}
                        title={normStatus(pl.status) === 'published'
                          ? '已发布：契约与编排封版，可被任务池挂接'
                          : '未发布：可自由修改；发布后才能被任务池使用'}
                      >
                        {STATUS_LABEL[normStatus(pl.status)]}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-center align-middle whitespace-nowrap" onClick={e => e.stopPropagation()}>
                      <div className="inline-flex items-center gap-2">
                        <EnabledSwitch
                          on={enabled}
                          busy={togglingId === pl.id}
                          lockReason={enableLockReason}
                          onToggle={() => handleToggleEnabled(pl)}
                          onLocked={() => enableLockReason && toast({
                            tone: 'warning',
                            title: '当前无法切换启用状态',
                            description: enableLockReason,
                          })}
                        />
                        <span className={`text-xs whitespace-nowrap ${enabled ? 'text-teal-700' : 'text-slate-400'}`}>
                          {enabled ? '已启用' : '未启用'}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-center align-middle whitespace-nowrap">
                      {runMeta ? (
                        <div className={`relative inline-flex items-center gap-1.5 ${runFailed ? 'cursor-help group/err' : ''}`}>
                          <span className={`inline-flex items-center gap-1 text-xs ${runMeta.color}`}>
                            {runMeta.icon}{runMeta.label}
                          </span>
                          <span className="text-xs text-gray-400">{formatTime(pl.last_run_at)}</span>
                          {runFailed && (
                            <div className="pointer-events-none absolute left-1/2 -translate-x-1/2 top-full mt-1.5 z-30 hidden group-hover/err:block w-80 text-left">
                              <div className="bg-gray-900/95 text-white text-xs rounded-lg px-3 py-2.5 shadow-xl whitespace-normal break-all leading-relaxed">
                                {pl.last_run_error}
                              </div>
                            </div>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs text-gray-300">从未运行</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center align-middle whitespace-nowrap" onClick={e => e.stopPropagation()}>
                      {curatedCount > 0 ? (
                        <button
                          onClick={() => navigate(`/data/structured?pipeline=${encodeURIComponent(pl.name)}`)}
                          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-[var(--color-nav-bg)] transition-colors hover:bg-teal-50 hover:text-teal-800"
                          title="点击查看数据集内容"
                        >
                          <Table2 size={12} /> {curatedCount} 个数据集
                        </button>
                      ) : (
                        <span className="text-xs text-gray-300">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center align-middle whitespace-nowrap" onClick={e => e.stopPropagation()}>
                      {taskCount > 0 ? (
                        <button
                          onClick={() => navigate(`/data/pipelines/sync-tasks?pipeline_id=${encodeURIComponent(pl.id)}`)}
                          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-[var(--color-nav-bg)] transition-colors hover:bg-teal-50 hover:text-teal-800"
                          title="点击查看关联的数据任务"
                        >
                          <ListChecks size={12} /> {taskCount} 个任务
                        </button>
                      ) : (
                        <span className="text-xs text-gray-300">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center align-middle whitespace-nowrap" onClick={e => e.stopPropagation()}>
                      <div className="flex items-center justify-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => setEditTarget(pl)}
                          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-teal-200 bg-teal-50 px-2.5 text-xs font-medium text-teal-800 transition hover:bg-teal-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30"
                          title={normStatus(pl.status) === 'published' ? '查看发布契约 / 编辑名称与描述' : '配置流水线：信息 / 执行预览 / 主键组 / 发布'}
                        >
                          <Pencil size={13} /> {normStatus(pl.status) === 'published' ? '查看契约' : '配置'}
                        </button>
                        <button
                          type="button"
                          onClick={() => setPreviewTarget(pl)}
                          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30"
                          title="试执行流水线并查看输出"
                        >
                          <Play size={13} /> 试执行
                        </button>
                        {renderMoreActions(pl, n8n, python, 'desktop')}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <div className="flex items-center justify-end gap-3 border-t border-slate-100 bg-slate-50/50 px-4 py-2.5">
            <label className="flex items-center gap-1.5 text-xs text-slate-500">
              每页
              <select
                value={pageSize}
                onChange={event => { setPageSize(Number(event.target.value)); setPage(1) }}
                className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs outline-none focus:border-teal-500"
                aria-label="每页显示条数"
              >
                {[10, 20, 50].map(size => <option key={size} value={size}>{size}</option>)}
              </select>
              条
            </label>
            <span className="min-w-20 text-center text-xs tabular-nums text-slate-500">第 {page} / {totalPages} 页</span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setPage(current => Math.max(1, current - 1))}
                disabled={page <= 1}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition-colors hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-35"
                aria-label="上一页"
              >
                <ChevronLeft size={14} />
              </button>
              <button
                type="button"
                onClick={() => setPage(current => Math.min(totalPages, current + 1))}
                disabled={page >= totalPages}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition-colors hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-35"
                aria-label="下一页"
              >
                <ChevronRight size={14} />
              </button>
            </div>
          </div>
          </div>
        </>
      )}

      {/* 试执行：仅运行并查看输出，入湖统一由数据任务池负责 */}
      {previewTarget && (
        <RunPreviewModal
          pipeline={previewTarget}
          onClose={() => setPreviewTarget(null)}
        />
      )}

      {/* 编辑向导 */}
      {editTarget && (
        <PipelineEditWizard
          pipeline={editTarget}
          onClose={() => setEditTarget(null)}
          onSaved={(updated) => {
            setEditTarget(null)
            updatePipelineLocally(updated)
          }}
        />
      )}

      {/* 新建弹窗 */}
      {showCreate && (
        <PipelineCreateModal
          n8nStatus={n8nStatus}
          onClose={() => setShowCreate(false)}
          onCreated={(pl) => {
            setShowCreate(false)
            insertPipelineLocally(pl)
          }}
        />
      )}

      {/* 删除确认：后端对所有引擎统一归档语义（保留版本、运行记录与资产湖产物） */}
      <ConfirmDialog
        open={!!deleteTarget}
        title="归档流水线"
        message={`确认归档流水线「${deleteTarget?.name}」？系统会停用该流水线，并保留发布版本、运行记录和资产湖产物用于审计。`}
        confirmLabel={deleting ? '处理中...' : '确认归档'}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />

      {/* 克隆确认：复制编排结构（n8n workflow / Python 脚本）与字段契约，副本未发布未启用 */}
      <ConfirmDialog
        open={!!cloneTarget}
        title="克隆流水线"
        message={`确认克隆流水线「${cloneTarget?.name}」？系统将复制其${cloneTarget && isN8nPipeline(cloneTarget) ? ' n8n 工作流编排' : ' Python 脚本'}与字段契约，生成未发布、未启用的草稿副本，名称在原名称后追加「_复制」尾缀（重名自动递增）。`}
        confirmLabel={cloning ? '克隆中...' : '确认克隆'}
        tone="primary"
        onConfirm={handleClone}
        onCancel={() => setCloneTarget(null)}
      />
    </div>
  )
}

function PipelineOverviewCard({
  label, value, note, icon, tone, alert = false,
}: {
  label: string
  value: number | string
  note: string
  icon: React.ReactNode
  tone: 'slate' | 'teal' | 'emerald' | 'rose'
  alert?: boolean
}) {
  const tones = {
    slate: 'bg-slate-100 text-slate-600',
    teal: 'bg-teal-50 text-teal-700',
    emerald: 'bg-emerald-50 text-emerald-700',
    rose: 'bg-rose-50 text-rose-600',
  }
  return (
    <article className={`rounded-xl border bg-white px-4 py-3 shadow-[0_6px_22px_rgba(15,23,42,0.035)] ${alert ? 'border-rose-200' : 'border-slate-200'}`}>
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-medium text-slate-500">{label}</p>
        <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg ${tones[tone]}`}>{icon}</span>
      </div>
      <p className={`mt-1 text-2xl font-semibold tracking-tight tabular-nums ${alert ? 'text-rose-600' : 'text-slate-950'}`}>{value}</p>
      <p className="mt-0.5 truncate text-[11px] text-slate-400" title={note}>{note}</p>
    </article>
  )
}

function PipelineCreateModal({
  pipeline, n8nStatus, onClose, onCreated, onSaved,
}: {
  pipeline?: Pipeline
  n8nStatus?: StewardStatus['n8n'] | null
  onClose: () => void
  onCreated?: (pl: Pipeline) => void
  onSaved?: () => void
}) {
  const { toast } = useToast()
  const isEdit = !!pipeline
  const n8nReady = Boolean(
    n8nStatus?.configured && n8nStatus.enabled && n8nStatus.reachable !== false,
  )
  const defaultMode = isEdit
    ? (isN8nPipeline(pipeline) ? 'n8n' : 'python')
    : (n8nReady ? 'n8n' : 'python')

  const [name, setName] = useState(pipeline?.name || '')
  const [description, setDescription] = useState(pipeline?.description || '')
  const [mode, setMode] = useState<'n8n' | 'python'>(defaultMode)
  const [saving, setSaving] = useState(false)
  const [nameTouched, setNameTouched] = useState(false)
  const nameError = nameTouched && !name.trim() ? '请输入流水线名称' : ''

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setNameTouched(true)
    if (!name.trim()) {
      return
    }
    if (!isEdit && mode === 'n8n' && !n8nReady) {
      toast({
        tone: 'warning',
        title: 'n8n 当前不可用',
        description: '请联系管理员检查部署环境的 N8N_* 启动配置并重启平台。',
      })
      return
    }
    setSaving(true)
    try {
      if (isEdit) {
        await pipelinesApi.update(pipeline.id, { name: name.trim(), description })
        onSaved?.()
      } else if (mode === 'n8n') {
        const res = await stewardApi.bootstrap(name.trim(), description)
        if (!res.record.pipelineId) throw new Error('n8n 流水线已创建，但未生成平台流水线记录。')
        const pl = await pipelinesApi.get(res.record.pipelineId)
        const definition = pl.definition as Record<string, unknown> | null
        const n8n = definition?.n8n as Record<string, unknown> | undefined
        onCreated?.({
          ...pl,
          definition: {
            ...definition,
            n8n: {
              ...n8n,
              n8n_workflow_id: res.record.n8nWorkflowId,
            },
          } as unknown as Pipeline['definition'],
        })
      } else if (mode === 'python') {
        const pl = await pipelinesApi.create({
          name: name.trim(),
          description,
          // 脚本留空：首个脚本必须经脚本编辑页「保存」（服务端重跑复验）写入，
          // 保证落库脚本一定通过执行与输出格式校验；编辑页会预填模板。
          definition: { engine: 'python', nodes: [], edges: [], python: {} },
        })
        onCreated?.(pl)
      }
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      toast({
        tone: 'error',
        title: isEdit ? '流水线保存失败' : '流水线创建失败',
        description: err?.detail || err?.message || '请稍后重试。',
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4 backdrop-blur-[2px]" onClick={onClose}>
      <form noValidate onSubmit={handleSubmit} className="w-[500px] max-w-full rounded-2xl border border-white/70 bg-white p-6 shadow-[0_28px_90px_rgba(15,23,42,0.24)]" onClick={e => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-4">
          <div>
            <h3 className="text-lg font-semibold tracking-tight text-slate-950">{isEdit ? '编辑数据流水线' : '新建数据流水线'}</h3>
            <p className="mt-1 text-xs text-slate-500">先选择执行引擎，再补充便于团队识别的名称与用途。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭新建流水线弹窗" className="grid h-9 w-9 place-items-center rounded-xl text-gray-400 transition hover:bg-slate-100 hover:text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30">
            <X size={18} />
          </button>
        </div>
        <div className="space-y-3">
          {!isEdit && (
            <div>
              <label className="block text-xs text-gray-500 mb-1.5">创建方式 *</label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setMode('n8n')}
                  disabled={!n8nReady}
                  className={`text-left p-3 rounded-lg border-2 transition-all ${
                    mode === 'n8n'
                      ? 'border-teal-400 bg-teal-50/60'
                      : !n8nReady
                        ? 'cursor-not-allowed border-gray-200 bg-gray-50 opacity-55'
                        : 'border-gray-200 hover:border-gray-300'}`}
                >
                  <div className={`text-sm font-medium flex items-center gap-1.5 ${mode === 'n8n' ? 'text-teal-700' : 'text-gray-900'}`}>
                    <Sparkles size={13} /> n8n 流水线
                  </div>
                  <div className="text-xs text-gray-500 mt-0.5">后台自动在 n8n 创建骨架工作流并加入列表；点击流水线可到数据管家用 AI 完善编排</div>
                </button>
                <button
                  type="button"
                  onClick={() => setMode('python')}
                  className={`text-left p-3 rounded-lg border-2 transition-all ${
                    mode === 'python' ? 'border-indigo-400 bg-indigo-50/50' : 'border-gray-200 hover:border-gray-300'}`}
                >
                  <div className={`text-sm font-medium flex items-center gap-1.5 ${mode === 'python' ? 'text-indigo-700' : 'text-gray-900'}`}>
                    <FileCode2 size={13} /> Python 脚本
                  </div>
                  <div className="text-xs text-gray-500 mt-0.5">自行编写 Python 脚本取数（HTTP 请求等），输出行数据写入资产湖</div>
                </button>
              </div>
              {!n8nReady && (
                <div className="mt-2 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  <AlertCircle size={13} className="shrink-0" />
                  <span className="flex-1">
                    {!n8nStatus
                      ? '正在检查 n8n 配置状态…'
                      : !n8nStatus.configured
                        ? '启动配置缺少 n8n 地址或 API Key，请联系管理员在部署环境补齐 N8N_* 并重启平台。'
                        : !n8nStatus.enabled
                          ? 'n8n 集成当前处于停用状态。'
                          : `n8n 当前不可达${n8nStatus.error ? `：${n8nStatus.error}` : '。'}`}
                  </span>
                </div>
              )}
            </div>
          )}
          <div>
            <label htmlFor="pipeline-name" className="block text-sm font-medium text-slate-700 mb-1.5">流水线名称 <span className="text-rose-500" aria-label="必填">*</span></label>
            <input
              id="pipeline-name"
              value={name}
              onChange={e => setName(e.target.value)}
              onBlur={() => setNameTouched(true)}
              required
              aria-invalid={Boolean(nameError)}
              aria-describedby={nameError ? 'pipeline-name-error' : 'pipeline-name-help'}
              className={`h-11 w-full rounded-xl border px-3 text-sm outline-none transition focus:ring-4 ${nameError ? 'border-rose-400 bg-rose-50/40 focus:border-rose-500 focus:ring-rose-500/10' : 'border-slate-200 focus:border-teal-500 focus:ring-teal-500/10'}`}
              placeholder="例：供应链数据清洗"
              autoFocus
            />
            {nameError ? (
              <p id="pipeline-name-error" role="alert" className="mt-1.5 flex items-center gap-1 text-xs text-rose-600"><AlertCircle size={12} />{nameError}</p>
            ) : (
              <p id="pipeline-name-help" className="mt-1.5 text-xs text-slate-400">建议使用“业务对象 + 动作”，例如“供应链订单清洗”。</p>
            )}
          </div>
          <div>
            <label htmlFor="pipeline-description" className="block text-sm font-medium text-slate-700 mb-1.5">流水线描述</label>
            <textarea
              id="pipeline-description"
              value={description}
              onChange={e => setDescription(e.target.value)}
              className="w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10"
              rows={3}
              placeholder="说明数据来源、处理目标或使用场景"
            />
          </div>
        </div>
        <div className="mt-5 flex flex-col gap-3 border-t border-slate-100 pt-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs leading-relaxed text-gray-400 sm:max-w-[60%]">
            {isEdit
              ? '名称和描述始终可修改；发布后仅编排与字段契约封版。'
              : n8nReady
                ? '推荐使用 n8n 流水线；需要自行编写取数逻辑时选择 Python 脚本。'
                : 'n8n 当前不可用；可创建 Python 脚本流水线，或检查启动配置与服务连通性后再创建 n8n 流水线。'}
          </p>
          <div className="flex gap-3 shrink-0">
            <button type="button" onClick={onClose} className="h-10 rounded-xl border border-slate-200 px-4 text-sm font-medium text-slate-600 transition hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400/30">
              取消
            </button>
            <button
              type="submit"
              disabled={saving}
              className="flex h-10 items-center gap-1.5 rounded-xl bg-[var(--color-nav-bg)] px-4 text-sm font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/40"
            >
              {saving && <Loader2 size={13} className="animate-spin" />}
              {saving ? (isEdit ? '保存中...' : '创建中...') : (isEdit ? '保存' : '创建')}
            </button>
          </div>
        </div>
      </form>
    </div>
  )
}
