import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Plus, Search, Play, GitBranch, Trash2, Pencil, ChevronLeft, ChevronRight,
  X, Loader2, CheckCircle2, XCircle, Clock, Table2, Sparkles, ExternalLink,
} from 'lucide-react'
import pipelinesApi from '@/api/v2/pipelines'
import type { Pipeline } from '@/api/v2/pipelines'
import { stewardApi } from '@/api/steward'
import ConfirmDialog from '@/components/ConfirmDialog'
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
  return (pl.definition as { engine?: string } | null)?.engine === 'n8n'
}

function formatTime(iso?: string | null): string {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  } catch { return iso }
}

function EnabledSwitch({ on, busy, locked, onToggle }: { on: boolean; busy: boolean; locked?: boolean; onToggle: () => void }) {
  return (
    <button
      role="switch" aria-checked={on} disabled={busy || locked} onClick={onToggle}
      className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
        on ? 'bg-teal-700' : 'bg-slate-300'} ${busy ? 'opacity-60' : locked ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
      title={locked
        ? '只有已发布的流水线才能启用，请先在编辑向导中完成发布'
        : on ? '已启用：任务池调度与联动触发会执行该流水线' : '未启用：任务池调度与联动触发将跳过（仍可手动执行试运行）'}
    >
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${on ? 'left-[18px]' : 'left-0.5'}`} />
    </button>
  )
}

/** 弹性列宽：table-fixed + 百分比，各列按比例自适应浏览器宽度 */

export default function PipelineListPage() {
  const navigate = useNavigate()
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
  const [pageError, setPageError] = useState('')

  const [showCreate, setShowCreate] = useState(false)
  const [previewTarget, setPreviewTarget] = useState<Pipeline | null>(null)
  const [editTarget, setEditTarget] = useState<Pipeline | null>(null)
  const [n8nApiUrl, setN8nApiUrl] = useState('')
  const [togglingId, setTogglingId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Pipeline | null>(null)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    setPageError('')
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
        if (page > 1 && res.items.length === 0 && res.total > 0) setPage(page - 1)
      })
      .catch(() => {
        setPipelines([])
        setTotal(0)
        setPageError('流水线列表加载失败，请检查服务连接后重试。')
      })
      .finally(() => setLoading(false))
  }, [filterEnabled, filterSource, filterStatus, page, pageSize, search])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    stewardApi.status().then(s => setN8nApiUrl(s.n8n.api_url)).catch(() => {})
  }, [])

  const handleToggleEnabled = async (pl: Pipeline) => {
    const next = !(pl.enabled ?? true)
    setTogglingId(pl.id)
    setPipelines(ps => ps.map(p => p.id === pl.id ? { ...p, enabled: next } : p))
    try {
      await pipelinesApi.setEnabled(pl.id, next)
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setPipelines(ps => ps.map(p => p.id === pl.id ? { ...p, enabled: !next } : p))
      setPageError(err?.detail || err?.message || '切换启用状态失败')
    } finally {
      setTogglingId(null)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await pipelinesApi.delete(deleteTarget.id)
      setDeleteTarget(null)
      load()
    } catch (e: unknown) {
      // 典型场景：被调度任务引用（后端引用保护 400）
      const err = e as { detail?: string; message?: string }
      setDeleteTarget(null)
      setPageError(err?.detail || err?.message || '归档失败')
    } finally {
      setDeleting(false)
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
  const refreshFirstPage = () => {
    if (page === 1) load()
    else setPage(1)
  }

  return (
    <div className="space-y-4 pb-4">
      {pageError && (
        <div role="alert" className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <XCircle size={15} className="mt-0.5 shrink-0" />
          <span className="flex-1">{pageError}</span>
          <button onClick={() => setPageError('')} aria-label="关闭错误提示" className="text-red-400 hover:text-red-700"><X size={14} /></button>
        </div>
      )}

      {/* 搜索、筛选、操作按钮 */}
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
          <option value="">全部来源</option>
          <option value="canvas">系统自定义</option>
          <option value="n8n">n8n 流水线</option>
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
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <button
            onClick={() => navigate('/data/pipelines/steward')}
            className="flex items-center gap-1.5 rounded-xl border border-teal-200 bg-teal-50 px-3.5 py-2 text-sm font-medium text-teal-800 transition hover:bg-teal-100 active:translate-y-px"
            title="用对话创建与编排 n8n 数据流水线"
          >
            <Sparkles size={15} /> 数据管家
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 rounded-xl bg-teal-700 px-3.5 py-2 text-sm font-medium text-white transition hover:bg-teal-800 active:translate-y-px"
          >
            <Plus size={15} /> 新建流水线
          </button>
        </div>
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
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.04)]">
          <table className="w-full min-w-[920px] text-sm table-fixed">
            <thead className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50/95 backdrop-blur">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs rounded-tl-xl" style={{ width: '26%' }}>
                  流水线信息
                </th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs" style={{ width: '10%' }}>流水线来源</th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs" style={{ width: '10%' }}>发布状态</th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs" style={{ width: '10%' }}>启用状态</th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs" style={{ width: '16%' }}>最近执行结果</th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs" style={{ width: '13%' }}>产物</th>
                <th className="text-center px-4 py-2.5 font-medium text-gray-600 text-xs rounded-tr-xl" style={{ width: '15%' }}>操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {pipelines.map(pl => {
                const runMeta = pl.last_run_status ? RUN_STATUS_META[pl.last_run_status] : null
                const runFailed = pl.last_run_status === 'failed' && !!pl.last_run_error
                const curatedCount = pl.target_curated_ids?.length ?? 0
                const n8n = isN8nPipeline(pl)
                const enabled = pl.enabled ?? true
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
                        <span className="inline-flex whitespace-nowrap items-center gap-1 rounded-lg border border-teal-200 bg-teal-50 px-2 py-1 text-[11px] font-medium text-teal-700"
                          title="由数据管家托管的 n8n 流水线：编排在数据管家，发布与启用在本列表的编辑向导">
                          <Sparkles size={10} /> n8n 流水线
                        </span>
                      ) : (
                        <span className="whitespace-nowrap inline-flex items-center gap-1 rounded border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] text-gray-600"
                          title="在流水线画布中自行编排的流水线">
                          <GitBranch size={10} /> 系统自定义
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
                          locked={!enabled && normStatus(pl.status) !== 'published'}
                          onToggle={() => handleToggleEnabled(pl)}
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
                      <div className="flex gap-1 justify-center">
                        <button
                          onClick={() => {
                            if (n8n) {
                              const wfId = (pl.definition as Record<string, unknown> | null)?.n8n as Record<string, unknown> | undefined
                              const workflowId = wfId?.n8n_workflow_id as string | undefined
                              if (workflowId && n8nApiUrl) {
                                const webUrl = n8nApiUrl.replace(/\/api\/.*$/, '')
                                window.open(`${webUrl}/workflow/${workflowId}`, '_blank')
                              }
                            } else {
                              navigate(`/data/pipelines/${pl.id}`)
                            }
                          }}
                          className="p-1.5 rounded hover:bg-gray-100 text-gray-500 hover:text-black transition-colors"
                          title={n8n ? '跳转 n8n 工作流' : '跳转画布编排'}
                        >
                          <ExternalLink size={14} />
                        </button>
                        <button
                          onClick={() => setEditTarget(pl)}
                          className="p-1.5 rounded hover:bg-gray-100 text-gray-500 hover:text-black transition-colors"
                          title={normStatus(pl.status) === 'published' ? '查看发布契约 / 编辑名称与描述' : '配置流水线：信息 / 执行预览 / 主键组 / 发布'}
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          onClick={() => setPreviewTarget(pl)}
                          className="p-1.5 rounded hover:bg-gray-100 text-gray-500 hover:text-black transition-colors"
                          title="执行：先试运行看输出，确认后再保存入资产湖"
                        >
                          <Play size={14} />
                        </button>
                        <button
                          onClick={() => setDeleteTarget(pl)}
                          className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-500 transition-colors"
                          title={n8n ? '归档流水线' : '删除'}
                        >
                          <Trash2 size={14} />
                        </button>
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
      )}

      {/* 执行：试运行预览 → 确认入湖 */}
      {previewTarget && (
        <RunPreviewModal
          pipeline={previewTarget}
          onClose={() => setPreviewTarget(null)}
          onSaved={load}
        />
      )}

      {/* 编辑向导 */}
      {editTarget && (
        <PipelineEditWizard
          pipeline={editTarget}
          onClose={() => setEditTarget(null)}
          onSaved={() => { setEditTarget(null); load() }}
        />
      )}

      {/* 新建弹窗 */}
      {showCreate && (
        <PipelineCreateModal
          onClose={() => setShowCreate(false)}
          onCreated={(_pl) => {
            setShowCreate(false)
            refreshFirstPage()
          }}
          onN8nCreated={() => {
            // 创建即在列表登记为未发布——留在列表让用户看到新行；
            // 编排完善随时进数据管家，发布在本列表的编辑向导
            setShowCreate(false)
            refreshFirstPage()
          }}
        />
      )}

      {/* 删除确认 */}
      <ConfirmDialog
        open={!!deleteTarget}
        title={deleteTarget && isN8nPipeline(deleteTarget) ? '归档流水线' : '删除流水线'}
        message={deleteTarget && isN8nPipeline(deleteTarget)
          ? `确认归档流水线「${deleteTarget.name}」？系统会停用 n8n 工作流，并保留发布版本、运行记录和资产湖产物用于审计。`
          : `确认删除流水线「${deleteTarget?.name}」？运行记录与版本历史将一并删除；已产出的成品数据集会保留在资产湖中。`}
        confirmLabel={deleting ? '处理中...' : deleteTarget && isN8nPipeline(deleteTarget) ? '确认归档' : '确认删除'}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}

function PipelineCreateModal({
  pipeline, onClose, onCreated, onN8nCreated, onSaved,
}: {
  pipeline?: Pipeline
  onClose: () => void
  onCreated?: (pl: Pipeline) => void
  onN8nCreated?: (recordId: string) => void
  onSaved?: () => void
}) {
  const isEdit = !!pipeline
  const defaultMode = isEdit
    ? (isN8nPipeline(pipeline) ? 'n8n' : 'canvas')
    : 'n8n'

  const [name, setName] = useState(pipeline?.name || '')
  const [description, setDescription] = useState(pipeline?.description || '')
  const [mode, setMode] = useState<'canvas' | 'n8n'>(defaultMode)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    if (!name.trim()) { setError('请填写流水线名称'); return }
    setSaving(true)
    setError('')
    try {
      if (isEdit) {
        await pipelinesApi.update(pipeline.id, { name: name.trim(), description })
        onSaved?.()
      } else if (mode === 'n8n') {
        const res = await stewardApi.bootstrap(name.trim(), description)
        onN8nCreated?.(res.record.id)
      } else {
        const pl = await pipelinesApi.create({
          name: name.trim(),
          description,
          definition: { nodes: [], edges: [] },
        })
        onCreated?.(pl)
      }
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setError(err?.detail || err?.message || (isEdit ? '保存失败' : '创建失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4 backdrop-blur-[2px]" onClick={onClose}>
      <div className="w-[500px] max-w-full rounded-2xl border border-white/70 bg-white p-6 shadow-[0_28px_90px_rgba(15,23,42,0.24)]" onClick={e => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-4">
          <h3 className="font-semibold">{isEdit ? '编辑数据流水线' : '新建数据流水线'}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-black">
            <X size={16} />
          </button>
        </div>
        <div className="space-y-3">
          {!isEdit && (
            <div>
              <label className="block text-xs text-gray-500 mb-1.5">创建方式 *</label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setMode('canvas')}
                  className={`text-left p-3 rounded-lg border-2 transition-all ${
                    mode === 'canvas' ? 'border-[var(--color-nav-bg)] bg-blue-50/40' : 'border-gray-200 hover:border-gray-300'}`}
                >
                  <div className={`text-sm font-medium flex items-center gap-1.5 ${mode === 'canvas' ? 'text-[var(--color-nav-bg)]' : 'text-gray-900'}`}>
                    <GitBranch size={13} /> 旧版系统流水线
                  </div>
                  <div className="text-xs text-gray-500 mt-0.5">创建后进入画布，自行编排采集与加工节点</div>
                </button>
                <button
                  type="button"
                  onClick={() => setMode('n8n')}
                  className={`text-left p-3 rounded-lg border-2 transition-all ${
                    mode === 'n8n' ? 'border-teal-400 bg-teal-50/60' : 'border-gray-200 hover:border-gray-300'}`}
                >
                  <div className={`text-sm font-medium flex items-center gap-1.5 ${mode === 'n8n' ? 'text-teal-700' : 'text-gray-900'}`}>
                    <Sparkles size={13} /> n8n 流水线
                  </div>
                  <div className="text-xs text-gray-500 mt-0.5">后台自动在 n8n 创建骨架工作流并加入列表；点击流水线可到数据管家用 AI 完善编排</div>
                </button>
              </div>
            </div>
          )}
          <div>
            <label className="block text-xs text-gray-500 mb-1">流水线名称 *</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 text-sm focus:border-emerald-400 focus:ring-1 focus:ring-emerald-200 focus:outline-none transition-colors"
              placeholder="例：供应链数据清洗"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">描述</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 text-sm focus:border-emerald-400 focus:ring-1 focus:ring-emerald-200 focus:outline-none transition-colors"
              rows={3}
              placeholder="流水线用途说明"
            />
          </div>
          {error && <p className="text-red-500 text-xs">{error}</p>}
        </div>
        <div className="flex items-center justify-between mt-4">
          <p className="text-xs text-gray-400 max-w-[60%] leading-relaxed">
            {isEdit
              ? '名称和描述始终可修改；发布后仅编排与字段契约封版。'
              : '推荐使用 n8n 流水线。系统流水线为旧版能力，仅保留兼容入口。'}
          </p>
          <div className="flex gap-3 shrink-0">
            <button onClick={onClose} className="px-4 py-2 border rounded-lg text-sm hover:bg-gray-50">
              取消
            </button>
            <button
              onClick={handleSubmit}
              disabled={saving}
              className="flex items-center gap-1.5 px-4 py-2 bg-[var(--color-nav-bg)] text-white rounded-lg text-sm disabled:opacity-50 hover:opacity-90 transition-opacity"
            >
              {saving && <Loader2 size={13} className="animate-spin" />}
              {saving ? (isEdit ? '保存中...' : '创建中...') : (isEdit ? '保存' : '创建')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
