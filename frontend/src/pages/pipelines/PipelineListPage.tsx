import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Plus, Search, Play, GitBranch, Trash2, Pencil,
  X, Loader2, CheckCircle2, XCircle, Clock, Table2, ArrowRight, PlugZap, Sparkles,
} from 'lucide-react'
import pipelinesApi from '@/api/v2/pipelines'
import type { Pipeline } from '@/api/v2/pipelines'
import ConnectionsTab from './connections/ConnectionsTab'
import ConfirmDialog from '@/components/ConfirmDialog'

const STATUS_STYLE: Record<string, string> = {
  draft:     'bg-gray-100 text-gray-600 border-gray-200',
  editing:   'bg-blue-50 text-blue-600 border-blue-200',
  running:   'bg-amber-50 text-amber-600 border-amber-200',
  failed:    'bg-red-50 text-red-600 border-red-200',
  published: 'bg-green-50 text-green-600 border-green-200',
}

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿', editing: '编辑中', running: '运行中',
  failed: '失败', published: '已发布',
}

const RUN_STATUS_META: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  success: { icon: <CheckCircle2 size={12} />, label: '成功', color: 'text-green-600' },
  failed:  { icon: <XCircle size={12} />,      label: '失败', color: 'text-red-500' },
  running: { icon: <Loader2 size={12} className="animate-spin" />, label: '运行中', color: 'text-blue-600' },
  pending: { icon: <Clock size={12} />,        label: '排队中', color: 'text-gray-500' },
}

/** 数据管家托管的 n8n 流水线 — 编辑/删除走数据管家，不进画布 */
function isN8nPipeline(pl: Pipeline): boolean {
  return (pl.definition as { engine?: string } | null)?.engine === 'n8n'
}

function stewardUrl(pl: Pipeline): string {
  const rid = (pl.definition as { n8n?: { steward_id?: string } } | null)?.n8n?.steward_id
  return rid ? `/data/pipelines/steward?record=${encodeURIComponent(rid)}` : '/data/pipelines/steward'
}

function formatTime(iso?: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  } catch { return iso }
}

interface RunBanner {
  pipelineName: string
  status: 'success' | 'failed'
  rowsIn?: number
  rowsOut?: number
  error?: string
}

export default function PipelineListPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  // 数据连接是流水线连接器的数据源配置库，作为流水线页的子视图管理
  const [view, setView] = useState<'pipelines' | 'connections'>(
    searchParams.get('view') === 'connections' ? 'connections' : 'pipelines')
  const switchView = (v: 'pipelines' | 'connections') => {
    setView(v)
    setSearchParams(prev => {
      const n = new URLSearchParams(prev)
      if (v === 'connections') n.set('view', v)
      else n.delete('view')
      return n
    }, { replace: true })
  }
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [filterDomain, setFilterDomain] = useState('')
  const [filterStatus, setFilterStatus] = useState('')

  const [showCreate, setShowCreate] = useState(false)
  const [runningId, setRunningId] = useState<string | null>(null)
  const [runBanner, setRunBanner] = useState<RunBanner | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Pipeline | null>(null)
  const [deleting, setDeleting] = useState(false)

  const load = () => {
    setLoading(true)
    pipelinesApi.list({ search, domain: filterDomain, status: filterStatus })
      .then(res => setPipelines(Array.isArray(res) ? res : []))
      .catch(() => setPipelines([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [search, filterDomain, filterStatus])

  const handleRun = async (pl: Pipeline) => {
    if (runningId) return
    setRunningId(pl.id)
    setRunBanner(null)
    try {
      const res = await pipelinesApi.runSync(pl.id)
      const stats = (res.stats || {}) as Record<string, unknown>
      if (res.status === 'success') {
        setRunBanner({
          pipelineName: pl.name, status: 'success',
          rowsIn: Number(stats.rows_in ?? 0), rowsOut: Number(stats.rows_out ?? 0),
        })
      } else {
        setRunBanner({ pipelineName: pl.name, status: 'failed', error: String((res as { error?: string }).error || '运行失败') })
      }
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setRunBanner({ pipelineName: pl.name, status: 'failed', error: err?.detail || err?.message || '运行失败' })
    } finally {
      setRunningId(null)
      load()
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await pipelinesApi.delete(deleteTarget.id)
      setDeleteTarget(null)
      load()
    } finally {
      setDeleting(false)
    }
  }

  const domains = [...new Set(pipelines.map(p => p.domain || '通用').filter(Boolean))]

  const filtered = pipelines.filter(p => {
    if (filterDomain && p.domain !== filterDomain) return false
    if (filterStatus && p.status !== filterStatus) return false
    if (search) {
      const q = search.toLowerCase()
      return p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q)
    }
    return true
  })

  return (
    <div className="space-y-4">
      {/* 页头 */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <GitBranch size={20} className="text-[var(--color-nav-bg)]" />
            数据流水线
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            编排数据采集与加工节点，在线测试运行；<b>发布后</b>可在数据任务池挂接调度自动触发，产物进入数据资产湖
          </p>
        </div>
        {view === 'pipelines' && (
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => navigate('/data/pipelines/steward')}
              className="flex items-center gap-1.5 px-3.5 py-2 border border-violet-300 bg-violet-50 text-violet-700 text-sm font-medium rounded-lg hover:bg-violet-100"
              title="用对话创建和管理基于 n8n 的数据流水线（草稿需审批后生效）"
            >
              <Sparkles size={15} /> 数据管家
            </button>
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-1.5 px-3.5 py-2 bg-[var(--color-nav-bg)] text-white text-sm font-medium rounded-lg hover:opacity-90"
            >
              <Plus size={15} /> 新建流水线
            </button>
          </div>
        )}
      </div>

      {/* 子视图切换：流水线 / 数据连接 */}
      <div className="flex items-center gap-1 border-b border-gray-200">
        {([
          ['pipelines', '流水线', <GitBranch size={13} key="i" />],
          ['connections', '数据连接', <PlugZap size={13} key="i" />],
        ] as ['pipelines' | 'connections', string, React.ReactNode][]).map(([key, label, icon]) => (
          <button
            key={key}
            onClick={() => switchView(key)}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-sm border-b-2 -mb-px transition-colors ${
              view === key
                ? 'border-[var(--color-nav-bg)] text-[var(--color-nav-bg)] font-medium'
                : 'border-transparent text-gray-500 hover:text-gray-800'
            }`}
          >
            {icon} {label}
          </button>
        ))}
        <span className="ml-auto text-xs text-gray-400 pb-2">
          {view === 'pipelines' ? '' : '连接是流水线连接器的数据源配置；文件类数据请到资产湖直接上传'}
        </span>
      </div>

      {view === 'connections' && <ConnectionsTab />}

      {view === 'pipelines' && <>
      {/* 运行结果提示条 */}
      {runBanner && (
        <div className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border text-sm ${
          runBanner.status === 'success'
            ? 'bg-green-50 border-green-200 text-green-700'
            : 'bg-red-50 border-red-200 text-red-600'
        }`}>
          {runBanner.status === 'success' ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
          {runBanner.status === 'success' ? (
            <span className="flex-1">
              「{runBanner.pipelineName}」运行成功：输入 {runBanner.rowsIn} 行 → 输出 {runBanner.rowsOut} 行
            </span>
          ) : (
            <span className="flex-1 truncate" title={runBanner.error}>
              「{runBanner.pipelineName}」运行失败：{runBanner.error}
            </span>
          )}
          {runBanner.status === 'success' && (
            <button
              onClick={() => navigate('/data/structured')}
              className="flex items-center gap-1 text-xs px-2.5 py-1 bg-white border border-green-300 rounded-lg hover:bg-green-100 shrink-0"
            >
              去资产湖查看产物 <ArrowRight size={11} />
            </button>
          )}
          <button onClick={() => setRunBanner(null)} className="text-gray-400 hover:text-gray-600 shrink-0">
            <X size={14} />
          </button>
        </div>
      )}

      {/* 搜索与筛选 */}
      <div className="flex items-center gap-3 bg-white rounded-xl border px-4 py-3 flex-wrap">
        <div className="relative w-72">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="搜索名称 / ID..."
            className="w-full pl-8 pr-3 py-1.5 border rounded-lg text-sm"
          />
          {search && (
            <button onClick={() => setSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black">
              <X size={12} />
            </button>
          )}
        </div>
        <select
          value={filterDomain}
          onChange={e => setFilterDomain(e.target.value)}
          className="border rounded-lg px-3 py-1.5 text-sm"
        >
          <option value="">全部领域</option>
          {domains.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        <select
          value={filterStatus}
          onChange={e => setFilterStatus(e.target.value)}
          className="border rounded-lg px-3 py-1.5 text-sm"
        >
          <option value="">全部状态</option>
          {Object.entries(STATUS_LABEL).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        {(search || filterDomain || filterStatus) && (
          <button
            onClick={() => { setSearch(''); setFilterDomain(''); setFilterStatus('') }}
            className="text-xs text-gray-500 hover:text-black px-2 py-1"
          >
            清除筛选
          </button>
        )}
      </div>

      {/* 列表 */}
      {loading ? (
        <div className="text-gray-400 text-sm p-8 text-center">加载中...</div>
      ) : filtered.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl p-12 text-center text-gray-400 space-y-2">
          <GitBranch size={32} className="mx-auto opacity-30" />
          <p className="text-sm font-medium">{search || filterDomain || filterStatus ? '没有匹配的流水线' : '暂无流水线'}</p>
          <p className="text-xs">新建流水线后，在画布中编排「连接器 → 存储 → 转换 → 输出」节点并测试运行</p>
        </div>
      ) : (
        <div className="border rounded-xl overflow-hidden bg-white">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">名称</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">领域</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">状态</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">最近运行</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">产物</th>
                <th className="text-right px-4 py-2.5 font-medium text-gray-600 text-xs">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {filtered.map(pl => {
                const runMeta = pl.last_run_status ? RUN_STATUS_META[pl.last_run_status] : null
                const curatedCount = pl.target_curated_ids?.length ?? 0
                const isRunning = runningId === pl.id
                const n8n = isN8nPipeline(pl)
                return (
                  <tr
                    key={pl.id}
                    className="hover:bg-gray-50 transition-colors cursor-pointer"
                    onClick={() => navigate(n8n ? stewardUrl(pl) : `/data/pipelines/${pl.id}`)}
                  >
                    <td className="px-4 py-3">
                      <p className="font-medium text-gray-900 flex items-center gap-1.5">
                        {pl.name}
                        {n8n && (
                          <span className="inline-flex items-center gap-0.5 rounded border border-violet-200 bg-violet-50 px-1.5 py-px text-[10px] font-normal text-violet-600"
                            title="由数据管家托管的 n8n 流水线">
                            <Sparkles size={9} /> n8n 智能编排
                          </span>
                        )}
                      </p>
                      <p className="text-xs text-gray-400 truncate max-w-[240px]" title={pl.description || pl.id}>
                        {pl.description || <span className="font-mono">{pl.id.slice(0, 8)}</span>}
                      </p>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-500">{pl.domain || '通用'}</td>
                    <td className="px-4 py-3">
                      <span className={`text-xs px-1.5 py-0.5 rounded border ${STATUS_STYLE[pl.status] || STATUS_STYLE.draft}`}>
                        {STATUS_LABEL[pl.status] || pl.status}
                      </span>
                      {pl.status === 'published' && (
                        <span className="text-xs text-gray-400 ml-1.5">v{pl.version || 1}</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {runMeta ? (
                        <div>
                          <span className={`inline-flex items-center gap-1 text-xs ${runMeta.color}`}>
                            {runMeta.icon}{runMeta.label}
                          </span>
                          <span className="text-xs text-gray-400 ml-1.5">{formatTime(pl.last_run_at)}</span>
                          {pl.last_run_status === 'failed' && pl.last_run_error && (
                            <p className="text-xs text-red-400 truncate max-w-[200px]" title={pl.last_run_error}>
                              {pl.last_run_error}
                            </p>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs text-gray-300">从未运行</span>
                      )}
                    </td>
                    <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                      {curatedCount > 0 ? (
                        <button
                          onClick={() => navigate(`/data/structured?pipeline=${encodeURIComponent(pl.name)}`)}
                          className="inline-flex items-center gap-1 text-xs text-[var(--color-nav-bg)] hover:underline"
                          title="在资产湖查看该流水线的产物数据集"
                        >
                          <Table2 size={12} /> {curatedCount} 个数据集
                        </button>
                      ) : (
                        <span className="text-xs text-gray-300">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right" onClick={e => e.stopPropagation()}>
                      <div className="flex gap-1 justify-end">
                        <button
                          onClick={() => navigate(n8n ? stewardUrl(pl) : `/data/pipelines/${pl.id}`)}
                          className="p-1.5 rounded hover:bg-gray-100 text-gray-500 hover:text-black transition-colors"
                          title={n8n ? '在数据管家中管理' : '打开画布编辑'}
                        >
                          {n8n ? <Sparkles size={14} /> : <Pencil size={14} />}
                        </button>
                        <button
                          onClick={() => handleRun(pl)}
                          disabled={!!runningId}
                          className="p-1.5 rounded hover:bg-gray-100 text-gray-500 hover:text-black transition-colors disabled:opacity-40"
                          title="立即运行一次"
                        >
                          {isRunning ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                        </button>
                        {!n8n && (
                          <button
                            onClick={() => setDeleteTarget(pl)}
                            className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-500 transition-colors"
                            title="删除"
                          >
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      </>}

      {/* 新建弹窗 */}
      {showCreate && (
        <PipelineCreateModal
          onClose={() => setShowCreate(false)}
          onCreated={(pl) => {
            setShowCreate(false)
            navigate(`/data/pipelines/${pl.id}`)
          }}
        />
      )}

      {/* 删除确认 */}
      <ConfirmDialog
        open={!!deleteTarget}
        title="删除流水线"
        message={`确认删除流水线「${deleteTarget?.name}」？运行记录与版本历史将一并删除；已产出的成品数据集会保留在资产湖中。`}
        confirmLabel={deleting ? '删除中...' : '确认删除'}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}

/** Pipeline 创建弹窗 */
function PipelineCreateModal({
  onClose, onCreated,
}: {
  onClose: () => void
  onCreated: (pl: Pipeline) => void
}) {
  const [name, setName] = useState('')
  const [domain, setDomain] = useState('供应链')
  const [description, setDescription] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleCreate = async () => {
    if (!name.trim()) { setError('请填写流水线名称'); return }
    setSaving(true)
    setError('')
    try {
      const pl = await pipelinesApi.create({
        name: name.trim(),
        domain,
        description,
        definition: { nodes: [], edges: [] },
      })
      onCreated(pl)
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setError(err?.detail || err?.message || '创建失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-lg p-6 w-[420px]" onClick={e => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-4">
          <h3 className="font-semibold">新建数据流水线</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-black">
            <X size={16} />
          </button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1">流水线名称 *</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 text-sm"
              placeholder="例：供应链数据清洗"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">业务领域</label>
            <select
              value={domain}
              onChange={e => setDomain(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 text-sm"
            >
              <option value="供应链">供应链</option>
              <option value="金融">金融</option>
              <option value="医疗">医疗</option>
              <option value="法律">法律</option>
              <option value="通用">通用</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">描述</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 text-sm"
              rows={3}
              placeholder="流水线用途说明"
            />
          </div>
          {error && <p className="text-red-500 text-xs">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 mt-4">
          <button onClick={onClose} className="px-4 py-2 border rounded-lg text-sm hover:bg-gray-50">
            取消
          </button>
          <button
            onClick={handleCreate}
            disabled={saving}
            className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50"
          >
            {saving && <Loader2 size={13} className="animate-spin" />}
            {saving ? '创建中...' : '创建'}
          </button>
        </div>
      </div>
    </div>
  )
}
